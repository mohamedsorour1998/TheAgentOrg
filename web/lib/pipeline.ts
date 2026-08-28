/**
 * HOW THE WEB LAYER REACHES THE PIPELINE. Read this before adding an endpoint.
 *
 * =========================================================================
 * THE DECISION: this layer calls the PYTHON MODULES as a subprocess, and does
 * NOT call Lane K's HTTP API. Three measured reasons, not a preference.
 * =========================================================================
 *
 * 1. LANE K HAS NO APPROVAL ROUTE, BY DESIGN. Measured — its eight routes are
 *    health, submit, status, cancel, config read/write, ingress and openapi, and
 *    its scopes are `runs:{read,write}` and `config:{read,write}`. There is no
 *    `gates:approve`, no route maps to `gates.resume` or `queue.resume`, and an
 *    AST test enforces that per module. So I5 — the task this lane exists for —
 *    cannot be served by it at all, whatever else it could serve.
 *
 * 2. ITS KEY STORE IS IN-PROCESS. `auth.InMemoryKeyStore` is module state, so
 *    `issue_key` writes into the store of whichever process called it. A Node
 *    process cannot provision a key into a separate Python server, which means
 *    calling that API over HTTP requires an out-of-band provisioning step that
 *    does not survive either process restarting. Its own docstring names this as
 *    one of three known gaps.
 *
 * 3. THE TENANT COMES FROM ITS CREDENTIAL, NOT FROM A SESSION. Every Lane K route
 *    derives the tenant from the machine key, and `test_no_route_takes_a_tenant_
 *    from_the_request` asserts structurally that none reads one from a request. A
 *    web session's tenant therefore has nowhere to go: it would need one key per
 *    tenant, minted per process, which is a worse version of the session we
 *    already have.
 *
 * WHAT THIS DOES INSTEAD, AND THE PROPERTY THAT MATTERS
 * ====================================================
 * One short-lived `python` subprocess per read, running a named module in
 * `agentorg/`. So Lane B's tenant scoping, Lane A's queue, Lane C's scoring and
 * Lane E's cost are reached through THEIR OWN accessors, in Python, with
 * `engine.acting_as(tenant)` bound around every read.
 *
 * **THIS API MUST NOT BECOME A WAY AROUND LANE B'S ENFORCEMENT**, which is the
 * single worst thing this lane could ship. A Node-side database client would be
 * exactly that: `node:sqlite` is available (measured — `DatabaseSync` is in the
 * stdlib here), so reimplementing `WHERE tenant_id = ?` in TypeScript is
 * genuinely possible, and it would be a SECOND, WEAKER copy of the predicate
 * whose removal fails 13 named Python tests. It would also miss the SQLite
 * triggers entirely, since those compare against `current_tenant()` — an
 * application-defined function registered only by `db.engine.connect()`. A
 * connection opened from Node has no such function, so every scoped write fails
 * with "no such function", and every scoped READ silently succeeds unscoped.
 *
 * That asymmetry is the whole argument. Lane B's own ADR says it: SQLite cannot
 * constrain a SELECT, so on the tested path a read is only as scoped as its
 * accessor. Reaching the data any way other than through those accessors means
 * re-deriving the one predicate that does the work.
 *
 * THE COST, MEASURED AND STATED
 * =============================
 * A subprocess per read is slower than an in-process query. Measured on this
 * machine: importing `agentorg.tenancy.accessors`, `agentorg.queue`,
 * `agentorg.gates` and `agentorg.log` together takes **0.183s total** wall clock
 * (`0.08s user 0.03s system`). So the floor is roughly 200ms per read, which is
 * fine for a run list and a detail screen and is NOT fine for a two-second poll —
 * which is one more reason I4 is a stream rather than a poll.
 *
 * The alternative that would remove it is a long-lived Python process speaking
 * HTTP, i.e. Lane K's server with an approval route and session-derived tenancy.
 * That is the right end state and it is Lane K's file, not mine. Stated as a
 * known limit rather than worked around.
 */

import { spawn } from "node:child_process";
import path from "node:path";

/**
 * The repository root, from this file's location. `web/lib/pipeline.ts` → two up.
 *
 * Derived rather than configured, for `fixtures_loader`'s reason: it resolves
 * `fixtures/` from the repo root because a configured path is one more thing that
 * can be wrong in a deployment nobody is watching.
 */
export const REPO_ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "..",
);

/**
 * The interpreter. `.venv-main/bin/python` unless overridden.
 *
 * CLAUDE.md is explicit that `.venv-habiba` / `.venv-sorour` / `.venv-testing`
 * each carry an editable-install `.pth` pointing at a sibling worktree, so imports
 * resolve somewhere other than where you are editing. Naming the interpreter
 * rather than trusting `PATH` is what keeps this from picking one of those up.
 */
export const PYTHON =
  process.env.AGENTORG_PYTHON ?? path.join(REPO_ROOT, ".venv-main", "bin", "python");

/**
 * Where the reader scripts live. Under `web/lib/`, which this lane owns.
 *
 * `import.meta.url` rather than `__dirname`: this package is `"type": "module"`,
 * so `__dirname` is not defined and the CommonJS form fails at runtime rather
 * than at build time.
 */
export const READERS = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "reader",
);

/** How long one read may take before it is abandoned, in milliseconds. */
const TIMEOUT_MS = 20_000;

/** The largest answer a read may produce. A run's detail is kilobytes, not megabytes. */
const MAX_OUTPUT_BYTES = 8 * 1024 * 1024;

export class PipelineError extends Error {
  constructor(
    message: string,
    readonly detail: string = "",
  ) {
    super(message);
    this.name = "PipelineError";
  }
}

/**
 * Run one reader script with a JSON request on stdin, and parse its answer.
 *
 * THE READERS LIVE UNDER `web/lib/reader/`, invoked BY PATH rather than as `-m`.
 * They are Python, and they are this lane's files: the alternative was a new
 * top-level `agentorg_web/` package, which would put Lane I's code in the
 * repository root beside `agentorg/` and outside the paths this lane owns. A
 * reader that reads is not part of the pipeline package.
 *
 * ARGUMENTS GO OVER STDIN AS JSON, NEVER ON THE COMMAND LINE. A run id and a
 * tenant id both reach this function from a request, and `argv` interpolation is
 * where a shell injection lives. `spawn` with an argument ARRAY does not invoke a
 * shell at all, and putting the untrusted values in the BODY rather than in argv
 * means even a future change to a shell-invoking spawn cannot reach them.
 *
 * THE VALUES ARE STILL VALIDATED ON THE PYTHON SIDE. `log.is_safe_run_id` refuses a
 * traversal, and `queue.enqueue` / `adopt_run_id` already validate for the same
 * reason: a run id becomes a path component, a partition key AND a subprocess
 * argument. This function does not attempt to be that check — a validation
 * reimplemented here would be the second weaker copy this module's header argues
 * against.
 *
 * PYTHONPATH IS SET TO THE REPO ROOT, and this is a measured requirement rather
 * than a precaution. CLAUDE.md records `cf5cb83`: a subprocess run from `scripts/`
 * has `sys.path[0]` pointing there, so the worktree root never reaches `sys.path`
 * and the editable install resolves `agentorg` to the SHARED CHECKOUT — a stage
 * wrote one checkout's `runs/` while a test globbed another's. Three lanes each
 * lost time to it.
 */
async function readPipeline<T>(
  moduleName: string,
  request: Record<string, unknown>,
): Promise<T> {
  const body = JSON.stringify(request);

  return new Promise<T>((resolve, reject) => {
    const child = spawn(PYTHON, [path.join(READERS, `${moduleName}.py`)], {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: REPO_ROOT,
        // The web layer reads; it never runs a stage. Closing the GitHub seam
        // means a read cannot post a comment or open a pull request even if a
        // future module reached for one by mistake.
        OFFLINE: "true",
        LLM_DISABLED: "true",
      },
      // No shell. The argument array is passed to execve directly.
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let size = 0;
    let settled = false;

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill("SIGKILL");
        reject(
          new PipelineError(
            "the pipeline read did not finish in time",
            `${moduleName} exceeded ${TIMEOUT_MS}ms`,
          ),
        );
      }
    }, TIMEOUT_MS);

    child.stdout.on("data", (chunk: Buffer) => {
      size += chunk.length;
      // BOUNDED BEFORE IT IS ACCUMULATED, the way `agents/server.py` checks its
      // 4 MiB cap BEFORE the read "so a hostile length cannot make the container
      // allocate". Here the producer is our own code, so this is a guard against
      // a runaway loop rather than against an attacker — but the allocation is
      // just as real.
      if (size > MAX_OUTPUT_BYTES) {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          child.kill("SIGKILL");
          reject(
            new PipelineError(
              "the pipeline read produced more output than this layer will hold",
              `${moduleName} exceeded ${MAX_OUTPUT_BYTES} bytes`,
            ),
          );
        }
        return;
      }
      stdout += chunk.toString("utf8");
    });

    child.stderr.on("data", (chunk: Buffer) => {
      // Kept, and surfaced on failure only. A Python warning on a successful read
      // is not an error, and treating it as one would make the layer fail on
      // deprecation notices.
      stderr += chunk.toString("utf8");
    });

    child.on("error", (error: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(
        new PipelineError(
          "the pipeline reader could not be started",
          `${PYTHON}: ${error.message}`,
        ),
      );
    });

    child.on("close", (code: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);

      if (code !== 0) {
        reject(
          new PipelineError(
            "the pipeline read failed",
            // The exit code AND the stderr, because "the read failed" without
            // either is the reassuring non-answer this repository keeps paying
            // for. Truncated: a traceback is long and this reaches a log.
            `${moduleName} exited ${code}: ${stderr.slice(-2000)}`,
          ),
        );
        return;
      }

      // AN EMPTY BODY IS NOT PARSED AS `{}`. `agent_client` refuses a zero-byte
      // body for exactly this reason: it "makes a blank response
      // indistinguishable from a runtime that answered `{}`". A reader that
      // printed nothing did not answer.
      if (stdout.trim() === "") {
        reject(
          new PipelineError(
            "the pipeline read returned nothing",
            `${moduleName} exited 0 and printed no JSON`,
          ),
        );
        return;
      }

      let parsed: unknown;
      try {
        parsed = JSON.parse(stdout);
      } catch (error) {
        reject(
          new PipelineError(
            "the pipeline read could not be parsed",
            `${moduleName}: ${(error as Error).message}`,
          ),
        );
        return;
      }

      if (parsed === null || typeof parsed !== "object") {
        reject(
          new PipelineError(
            "the pipeline read answered something that is not an object",
            `${moduleName} answered ${typeof parsed}`,
          ),
        );
        return;
      }

      // AN `error` KEY IS A REFUSAL, not a result. The Python side reports a
      // refused read (a cross-tenant access, an absent run) as data rather than
      // as a non-zero exit, because those are answers and not crashes — but they
      // must not be handed back as though they were the thing asked for.
      const record = parsed as Record<string, unknown>;
      if (typeof record.error === "string") {
        reject(
          new PipelineError(
            record.error,
            typeof record.detail === "string" ? record.detail : "",
          ),
        );
        return;
      }

      resolve(record as T);
    });

    child.stdin.on("error", () => {
      // A closed stdin is reported by the `close` handler above with the exit
      // code; swallowing it here only stops an unhandled EPIPE from crashing the
      // Node process. Deliberately not a reject: doing both races the two.
    });
    child.stdin.end(body);
  });
}

export { readPipeline };
