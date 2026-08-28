/**
 * THE LIVE RUN VIEW. Seven stages, the current one, and output as it arrives.
 * THE SCREEN THE DEMO LIVES ON.
 *
 * A client component, because it holds a live stream and a decision form.
 *
 * WHAT IT DOES WHILE NOTHING IS HAPPENING, which is most of the time: it shows
 * when the stream was last heard from rather than a spinner. A run spends its
 * wall clock waiting -- a stage takes tens of seconds and a gate waits for a
 * person indefinitely -- so "quiet and current" and "quiet and broken" are the two
 * states a viewer actually needs told apart, and a spinner says neither. See
 * `useRunStream.ts`.
 *
 * WHY IT RE-READS THE RUN AFTER EVERY STAGE FRAME
 * ==============================================
 * The stream carries transitions, not the run. A frame says `security -> done`; it
 * does not carry the verdict, the findings or the PR url. Rendering from frames
 * alone would leave the security panel empty on a blocked run -- the one thing
 * this screen exists to show -- so a stage frame triggers a re-fetch of
 * `/api/runs/[runId]` and the frames drive WHEN to read, never WHAT to display.
 */

"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { CostPanel } from "@/components/CostPanel";
import { DecisionLog, GateControls } from "@/components/GateControls";
import { ErrorState, Mark, Skeleton } from "@/components/primitives";
import { SecurityPanel } from "@/components/SecurityPanel";
import { StageSpine } from "@/components/StageSpine";
import { ago, useRunStream } from "@/components/useRunStream";
import { RUN_STATUS } from "@/components/vocabulary";
import { getJson } from "@/components/fetching";
import type { RunDetail } from "@/lib/contract";
import type { CostView, ScoringResponse } from "@/lib/endpoints";

type Failure = { error: string; fix: string; detail?: string };

/** A run whose status can no longer change. Nothing to stream. */
const ENDED = new Set(["blocked", "rejected", "promoted", "failed"]);

export default function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params);

  const [run, setRun] = useState<RunDetail | null>(null);
  const [scoring, setScoring] = useState<ScoringResponse | null>(null);
  const [cost, setCost] = useState<CostView | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(() => new Date());
  // Bumped by an event -- a recorded decision, or a retry. An EVENT may set
  // state; an effect may not, which is what shapes the reload below.
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((n) => n + 1), []);

  const ended = run !== null && ENDED.has(run.status);
  const stream = useRunStream(runId, run !== null && !ended);
  // A stage transition means the run itself changed, so the frame count is a
  // DEPENDENCY of the read rather than a trigger for one. Written as an effect
  // that calls a loader, Next 16's `react-hooks/set-state-in-effect` refuses it
  // -- correctly: an effect that sets state which re-runs an effect is a
  // cascading render, and the honest form is one effect whose inputs include
  // everything that should make it re-read.
  const frames = stream.events.length;

  /**
   * ONE READ, and the `cancelled` guard is not ceremony: switching runs while a
   * response is in flight would otherwise write the previous run's data into the
   * new run's view, which reads as a wrong run rather than as a race.
   */
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const result = await getJson<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`);
      if (cancelled) return;

      if (!result.ok) {
        setFailure({ error: result.error, fix: result.fix, detail: result.detail });
        setLoading(false);
        return;
      }
      setFailure(null);
      setRun(result.value);
      setLoading(false);

      // Scoring and cost are separate reads and each may legitimately be absent
      // -- a run with no security stage has no scoring. A failure on either must
      // not blank the run, so neither sets `failure`.
      const [scored, priced] = await Promise.all([
        getJson<ScoringResponse>(`/api/runs/${encodeURIComponent(runId)}/scoring`),
        getJson<CostView>(`/api/runs/${encodeURIComponent(runId)}/cost`),
      ]);
      if (cancelled) return;
      setScoring(scored.ok ? scored.value : null);
      setCost(priced.ok ? priced.value : null);
    })();

    return () => {
      cancelled = true;
    };
  }, [runId, frames, revision]);

  // Ticks the "last heard" line. One second, and only while streaming -- a timer
  // on an ended run would run forever for no reason. `setNow` fires from the
  // timer, not from the effect body, which is why this is not a cascading render.
  useEffect(() => {
    if (ended || run === null) return;
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, [ended, run]);

  if (loading) {
    return (
      <div>
        <p className="eyebrow">Run</p>
        <Skeleton label="Loading this run" rows={6} />
      </div>
    );
  }

  if (failure) {
    return (
      <div>
        <p className="eyebrow">Run</p>
        <ErrorState error={failure.error} fix={failure.fix} detail={failure.detail} />
        <p style={{ marginTop: "var(--gap-4)" }}>
          <Link href="/runs">Back to all runs</Link>
        </p>
      </div>
    );
  }

  if (!run) return null;

  return (
    <div>
      <p className="eyebrow">
        <Link href="/runs" style={{ color: "inherit" }}>
          Runs
        </Link>{" "}
        / {run.ticket_id}
      </p>

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "var(--gap-4)",
          flexWrap: "wrap",
          marginBottom: "var(--gap-2)",
        }}
      >
        <h1 className="display">{run.ticket_id}</h1>
        <Mark mark={RUN_STATUS[run.status]} />
      </div>

      <p className="prose">{run.ticket_text}</p>

      <dl
        className="grid-2"
        style={{ margin: "var(--gap-6) 0", fontSize: "var(--step-small)" }}
      >
        <Pair label="Run id" value={run.run_id} mono />
        <Pair
          label="Started by"
          value={run.trigger === "issue" ? "an issue being opened" : run.trigger || "unknown"}
        />
        <Pair
          label="Agents answered from"
          value={run.model_provenance || "not recorded"}
          mono
        />
        {run.branch ? <Pair label="Branch" value={run.branch} mono /> : null}
        {run.pr_url ? (
          <div>
            <dt className="eyebrow">Pull request</dt>
            <dd style={{ margin: 0 }}>
              <a href={run.pr_url}>{run.pr_url.replace(/^https:\/\/github\.com\//, "")}</a>
            </dd>
          </div>
        ) : null}
        {run.poisoned ? (
          <Pair label="Ticket" value="deliberately carries a credential" />
        ) : null}
      </dl>

      {/* THE OPEN GATES. Above the spine, because a decision waiting on a person
          is the only thing on this page that needs acting on. */}
      {run.awaiting_gates.length > 0 ? (
        <div style={{ marginBottom: "var(--gap-8)" }}>
          {run.awaiting_gates.map((gate) => (
            <div key={gate} style={{ marginBottom: "var(--gap-4)" }}>
              <GateControls runId={run.run_id} gate={gate} onRecorded={reload} />
            </div>
          ))}
        </div>
      ) : null}

      <div className="grid-2" style={{ alignItems: "start", gap: "var(--gap-8)" }}>
        <section>
          <h2 className="title" style={{ marginBottom: "var(--gap-4)" }}>
            Stages
          </h2>
          <StageSpine
            stages={run.stages}
            runEnded={ended}
            awaitingGates={run.awaiting_gates}
          />
        </section>

        <section>
          <h2 className="title" style={{ marginBottom: "var(--gap-4)" }}>
            As it happens
          </h2>
          <StreamPanel stream={stream} ended={ended} now={now} />
        </section>
      </div>

      <div style={{ margin: "var(--gap-12) 0" }}>
        <SecurityPanel security={run.security} scoring={scoring} />
      </div>

      <section style={{ marginBottom: "var(--gap-12)" }}>
        <h2 className="title" style={{ marginBottom: "var(--gap-4)" }}>
          Decisions
        </h2>
        <DecisionLog decisions={run.decisions} />
      </section>

      <section>
        <h2 className="title" style={{ marginBottom: "var(--gap-4)" }}>
          Cost
        </h2>
        {cost ? (
          <CostPanel cost={cost} />
        ) : (
          <p className="prose" style={{ fontSize: "var(--step-small)" }}>
            No cost record for this run.
          </p>
        )}
      </section>
    </div>
  );
}

function Pair({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="eyebrow">{label}</dt>
      <dd
        style={{
          margin: 0,
          fontFamily: mono ? "var(--mono)" : "inherit",
          wordBreak: mono ? "break-all" : "normal",
        }}
      >
        {value}
      </dd>
    </div>
  );
}

/**
 * The event list, and the honest answer to "is this still live?".
 *
 * An ended run gets no stream and says so -- an idle "live" indicator on a run
 * that finished an hour ago is a claim about a connection that does not exist.
 */
function StreamPanel({
  stream,
  ended,
  now,
}: {
  stream: ReturnType<typeof useRunStream>;
  ended: boolean;
  now: Date;
}) {
  if (ended) {
    return (
      <p className="prose" style={{ fontSize: "var(--step-small)" }}>
        This run has finished, so there is nothing left to stream. Everything it did
        is on this page.
      </p>
    );
  }

  return (
    <div>
      <p
        style={{
          margin: `0 0 var(--gap-4)`,
          fontFamily: "var(--mono)",
          fontSize: "var(--step-caption)",
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: stream.phase === "dropped" ? "var(--refused)" : "var(--accent)",
        }}
      >
        {stream.phase === "connecting" ? "Connecting" : null}
        {stream.phase === "live"
          ? stream.lastHeard
            ? `Live · heard ${ago(stream.lastHeard, now)}`
            : "Live · nothing yet"
          : null}
        {stream.phase === "dropped" ? "Stream stopped" : null}
      </p>

      {stream.phase === "dropped" ? (
        <div style={{ marginBottom: "var(--gap-4)" }}>
          <ErrorState
            error="The live connection ended and did not come back."
            fix="Reopen it to pick up where it left off. Nothing was lost — the run kept going without this page."
            detail={stream.cursor ? `last cursor: ${stream.cursor}` : undefined}
          />
          <button
            type="button"
            className="btn"
            onClick={stream.reconnect}
            style={{ marginTop: "var(--gap-3)" }}
          >
            Reopen the stream
          </button>
        </div>
      ) : null}

      {stream.events.length === 0 ? (
        <p className="prose" style={{ fontSize: "var(--step-small)" }}>
          Nothing has moved since this page opened. A stage takes tens of seconds,
          and a gate waits until somebody decides.
        </p>
      ) : (
        <ol
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            maxHeight: "24rem",
            overflowY: "auto",
          }}
          aria-live="polite"
        >
          {[...stream.events].reverse().map((frame) => (
            <li
              key={`${frame.cursor}-${frame.stage}-${frame.status}`}
              style={{
                borderBottom: "1px solid var(--border)",
                padding: "var(--gap-2) 0",
                fontSize: "var(--step-small)",
              }}
            >
              <span style={{ fontFamily: "var(--mono)", color: "var(--accent)" }}>
                {frame.stage}
              </span>{" "}
              <span style={{ fontFamily: "var(--mono)", color: "var(--text-muted)" }}>
                {frame.status}
              </span>
              {frame.summary ? (
                <span style={{ display: "block", color: "var(--text-muted)" }}>
                  {frame.summary}
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
