/**
 * GET /api/runs/[runId]/events — server-sent events for one run. Task I4.
 *
 * =========================================================================
 * SSE, NOT WEBSOCKETS, AND NOT POLLING. Each choice measured.
 * =========================================================================
 * The plan says "polling every two seconds is the first thing a judge notices", and
 * it is right — but the stronger reason is arithmetic. Each read costs a Python
 * subprocess: measured at **0.183s** wall clock to import the four packages
 * (`0.08s user 0.03s system`). A two-second poll from ten open tabs is five
 * subprocesses a second, permanently. The stream reads on a schedule this endpoint
 * controls, and one connection is one reader.
 *
 * SSE over websockets because the traffic is **one-directional**. A screen watches a
 * run; it never sends anything back over the same channel — the one thing it sends is
 * a gate decision, which is a POST to `/api/approvals` and must stay one, because
 * that is where the `Origin` check and the audit record live. A websocket would add a
 * second mutation path into the same process with no CSRF story, on the surface that
 * can open a gate.
 *
 * SSE also reconnects by itself. `EventSource` retries on drop and replays
 * `Last-Event-ID`, so a laptop that slept mid-demo resumes rather than showing a
 * stalled screen — and the cursor below is what makes that resumption real rather
 * than a fresh start.
 *
 * =========================================================================
 * THE CURSOR IS `updated_at`, AND ITS LIMITS ARE STATED RATHER THAN HIDDEN
 * =========================================================================
 * There is **no event table, no sequence number and no LISTEN/NOTIFY** anywhere in
 * `agentorg/` — checked, not assumed. `queue_jobs` is the only table in the queue's
 * schema, `job_id` is a random uuid4 (unordered, so useless as a cursor), and the
 * only monotonic columns are two ISO-8601 UTC timestamp strings. ISO-8601 UTC sorts
 * lexicographically, which the queue already relies on for lease comparison in both
 * Python and SQL, so `updated_at` is a usable cursor.
 *
 * Three honest limits:
 *
 *   * **`heartbeat` bumps `updated_at` with no state change**, so a naive tail sees
 *     churn that is not progress. Handled by diffing on the FIELDS a screen renders
 *     (`stage`, `status`, `exit_code`) rather than emitting a frame per row change —
 *     otherwise a long `develop` stage would emit a frame every renewal and the
 *     screen would flicker while nothing happened.
 *   * **there is no index on `updated_at`**, so a `WHERE updated_at > ?` tail is a
 *     full scan. Irrelevant at this scale and it would not be at a thousand tenants;
 *     the fix is an index on Lane A's table, which is not this lane's file.
 *   * **deletes are invisible.** Nothing deletes a job today, so this is latent.
 *
 * A REAL EVENT TABLE WITH A SEQUENCE IS THE CORRECT END STATE, and it is new schema
 * on Lane A's table rather than something this endpoint can create. Recorded as a
 * further step; what is here is honest polling of a durable row, on the server, at a
 * rate the server picks — which is a different thing from a browser polling REST.
 *
 * WHAT A FRAME IS NOT: it is not the run's state document. The stream carries stage
 * transitions and nothing else, so a screen fetches `/api/runs/[runId]` once and then
 * updates stages from the stream. Sending the whole document per frame would make
 * every heartbeat a file read.
 */

import type { RunEvent } from "@/lib/endpoints";
import { refuse } from "@/lib/http";
import { readPipeline } from "@/lib/pipeline";
import { currentIdentity } from "@/lib/session";
import { type StageRow, framesFor } from "@/lib/stream";

/**
 * How often the server re-reads. Two seconds, and the difference from a two-second
 * browser poll is real rather than semantic: this is ONE reader per connection on the
 * server, not one per tab plus a round trip, and it costs a subprocess rather than a
 * request through the whole Next.js stack.
 */
const READ_INTERVAL_MS = 2_000;

/**
 * A comment frame every fifteen seconds, whatever happens.
 *
 * NOT DECORATION. Proxies and load balancers close an idle connection — commonly at
 * 30 or 60 seconds — and an SSE stream with nothing to say is idle by definition. A
 * `:` comment line is valid SSE that `EventSource` ignores, so it keeps the
 * connection open without inventing an event a screen would render.
 *
 * Fifteen seconds is under half the shortest common timeout, so one dropped keepalive
 * does not close the stream.
 */
const KEEPALIVE_MS = 15_000;

/**
 * How long one connection may live. Ten minutes, then the client reconnects.
 *
 * A BOUND IS REQUIRED, not optional: without one a tab left open overnight holds a
 * subprocess-spawning loop forever, and a crashed client that never sent FIN is
 * indistinguishable from a live one. `EventSource` reconnects automatically and
 * resumes from `Last-Event-ID`, so the cost of the bound is one reconnect.
 */
const MAX_STREAM_MS = 10 * 60 * 1_000;

/**
 * `frame` is the ONLY thing left in this file that shapes a message, and the diff it
 * feeds on lives in `web/lib/stream.ts`.
 *
 * THAT SPLIT IS NOT TIDINESS. A second copy of the diff here would make
 * `lib/__tests__/stream.test.ts` pin a function the deployed path never runs — which
 * is this repository's named pattern exactly: "a test double, a helper, an inference,
 * or a measurement that cannot express the failing case produces confidence that
 * cannot be falsified". So this route imports `framesFor` and holds no comparison of
 * its own.
 */
function frame(event: RunEvent): string {
  return `id: ${event.cursor}\ndata: ${JSON.stringify(event)}\n\n`;
}

export async function GET(
  request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<Response> {
  const session = await currentIdentity();
  if (session === null) {
    // REFUSED BEFORE THE STREAM OPENS. An unauthenticated caller must not get a
    // 200 with an empty stream — that reads as "this run has no activity" rather
    // than "you are not signed in", and a screen would show a stalled pipeline.
    return refuse("sign in to watch this run", 401);
  }

  const { runId } = await context.params;

  // OWNERSHIP IS CHECKED ONCE, BEFORE THE STREAM OPENS, and a refusal is an HTTP
  // status rather than a frame. A stream that opened and then said "not yours" would
  // be a 200 for an unauthorised read.
  try {
    await readPipeline<unknown>("detail", {
      action: "run_detail",
      tenant_id: session.tenantId,
      run_id: runId,
    });
  } catch {
    return refuse("no such run", 404);
  }

  const encoder = new TextEncoder();
  const startedAt = Date.now();
  // Resume from where the client left off. `Last-Event-ID` is what `EventSource`
  // sends automatically on reconnect; `?since=` is for a client that tracks its own.
  const since =
    request.headers.get("last-event-id") ??
    new URL(request.url).searchParams.get("since") ??
    "";

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let seen = new Map<string, string>();
      let closed = false;
      let lastKeepalive = Date.now();

      // The client going away is the normal ending, not an error: a person closes a
      // tab. Without this the loop keeps spawning subprocesses for a socket nobody
      // is reading.
      request.signal.addEventListener("abort", () => {
        closed = true;
      });

      const send = (text: string) => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(text));
        } catch {
          // The stream was closed under us between the check and the write. Not an
          // error; stop looping.
          closed = true;
        }
      };

      while (!closed && Date.now() - startedAt < MAX_STREAM_MS) {
        let rows: StageRow[] = [];
        try {
          const answer = await readPipeline<{ stages: StageRow[] }>("detail", {
            action: "run_detail",
            tenant_id: session.tenantId,
            run_id: runId,
          });
          rows = answer.stages;
        } catch (error) {
          // A FAILED READ IS REPORTED, NOT SWALLOWED, and the stream stays open. A
          // silent skip would leave the screen showing stale stages with no
          // indication that the server stopped being able to read them — the
          // "did not run versus passed" conflation on a live screen.
          send(
            frame({
              cursor: new Date().toISOString(),
              run_id: runId,
              kind: "heartbeat",
              stage: "",
              status: "unreadable",
              summary:
                error instanceof Error
                  ? `the run could not be read: ${error.message}`
                  : "the run could not be read",
            }),
          );
          await sleep(READ_INTERVAL_MS);
          continue;
        }

        // THE TESTED DIFF. See `web/lib/stream.ts`: a heartbeat must not produce a
        // frame, and a reconnecting client must not be re-sent what it rendered.
        const diff = framesFor(rows, seen, since, runId);
        for (const event of diff.events) {
          send(frame(event));
        }
        seen = diff.seen;

        if (Date.now() - lastKeepalive >= KEEPALIVE_MS) {
          // A COMMENT LINE, not an event. Valid SSE, ignored by `EventSource`, and it
          // keeps a proxy from closing an idle connection.
          send(": keepalive\n\n");
          lastKeepalive = Date.now();
        }

        await sleep(READ_INTERVAL_MS);
      }

      try {
        controller.close();
      } catch {
        // Already closed by the abort handler. Nothing to do.
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      // NEVER CACHED, and `no-transform` is the one that is easy to omit: a proxy
      // that buffers or compresses an SSE stream holds every frame until the
      // connection closes, which turns a live screen into a blank one that suddenly
      // fills at the end. `X-Accel-Buffering` says the same thing to nginx, which
      // ignores `no-transform`.
      "Cache-Control": "no-store, no-transform, private",
      "X-Accel-Buffering": "no",
      Connection: "keep-alive",
    },
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
