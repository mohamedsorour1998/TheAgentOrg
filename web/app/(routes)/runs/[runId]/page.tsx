/**
 * THE RUN VIEW. One header, one spine, one open stage. THE SCREEN THE DEMO LIVES ON.
 *
 * ── WHY IT WAS REDESIGNED ────────────────────────────────────────────────────
 *
 * Reported from the deployed app: *"the run page is super unorganized and messy
 * ... make it smart and elegant and shorter ... rethink the whole page in terms of
 * UI and logic"*. It was eight stacked sections with four large ones open at once,
 * so the security verdict -- the beat the product exists for -- sat in the middle
 * of three screens of scrolling. And half of one column was an event list that is
 * STRUCTURALLY EMPTY for these runs, because it reads the queue and a run on the
 * GitHub Actions path never enters the queue.
 *
 * A run IS a sequence of stages, each of which produced something. So the spine
 * became the page's index: pick a stage, see what it produced, one at a time. The
 * decision a person owes is lifted out of that sequence entirely, into its own card
 * above -- it is the only thing on this page that is an ACTION rather than a record.
 *
 * ── AND WHY THE LOGIC CHANGED UNDER IT ───────────────────────────────────────
 *
 * Two bugs were reported together: *"when I approve gate3 nothing happened"* and
 * *"when I approve gate 2 it works but it takes 2 min"*. **Neither approval
 * failed.** Both are one cause, measured on run 35057681679 -- a gate job holds no
 * AWS credential, so a gate decision reaches the stored record only when the NEXT
 * credentialled job rewrites it, and `gate3`'s next job is `promote`, which holds
 * none either. The full table is in `lib/dispatch.ts:runProgress`.
 *
 * So the spine, the gate control and the status now come from GITHUB -- the jobs
 * and `pending_deployments`, which are what the pipeline actually is -- while the
 * stage panels come from the stored document, which is the only thing that knows
 * what the run produced. `run.live` says which of the two the reader is looking at.
 */

"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { CostPanel } from "@/components/CostPanel";
import { AgentOutput } from "@/components/AgentOutput";
import { DecisionLog, GateControls } from "@/components/GateControls";
import { ErrorState, Mark, Skeleton } from "@/components/primitives";
import { SecurityPanel } from "@/components/SecurityPanel";
import { PHASE_WORD, StageSpine, phases, spineSentence } from "@/components/StageSpine";
import { useRunStream } from "@/components/useRunStream";
import { RUN_STATUS } from "@/components/vocabulary";
import { getJson } from "@/components/fetching";
import type { Gate, RunDetail, Stage } from "@/lib/contract";
import type { CostView, ScoringResponse } from "@/lib/endpoints";

type Failure = { error: string; fix: string; detail?: string };

/** A run whose status can no longer change. Nothing left to poll for. */
const ENDED = new Set(["blocked", "rejected", "promoted", "failed"]);

export default function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params);

  const [run, setRun] = useState<RunDetail | null>(null);
  const [scoring, setScoring] = useState<ScoringResponse | null>(null);
  const [cost, setCost] = useState<CostView | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);
  const [readAt, setReadAt] = useState<Date | null>(null);
  const [now, setNow] = useState(() => new Date());
  /**
   * THE STAGE THE READER CHOSE, or `null` for "follow the run".
   *
   * Two states, not one, and collapsing them breaks one of the two behaviours: a
   * page that always follows the run yanks the panel away while somebody is reading
   * the diff, and a page that never follows opens on `plan` for a run that is three
   * stages further on. `null` follows; a choice sticks.
   */
  const [picked, setPicked] = useState<Stage | null>(null);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((n) => n + 1), []);

  const ended = run !== null && ENDED.has(run.status);
  // The queue's stream. It carries nothing for an Actions run -- see the header --
  // so it is kept ONLY as a faster trigger for the poll below on the self-hosted
  // path, and renders nothing. A frame is a dependency of the read, not a cascade.
  const stream = useRunStream(runId, run !== null && !ended);
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
      setReadAt(new Date());
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

  /**
   * RE-READS THE RUN WHILE IT IS LIVE, so the page moves on its own.
   *
   * FIVE SECONDS. A stage takes tens of seconds and a gate waits for a person, so
   * anything faster is load without information; anything slower and a stage
   * completes, is replaced by the next, and is never seen.
   *
   * **IT STOPS WHEN THE TAB IS HIDDEN.** A run left open in a background tab
   * overnight would otherwise be thousands of reads of a table and of GitHub, each
   * one billed, for a screen nobody is looking at.
   */
  useEffect(() => {
    if (ended || run === null) return;
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      setNow(new Date());
      reload();
    }, 5000);
    return () => clearInterval(id);
  }, [ended, run, reload]);

  const rows = useMemo(() => (run ? phases(run.stages, ended) : []), [run, ended]);

  /**
   * WHICH STAGE THE PAGE OPENS ON, in the order a reader would look.
   *
   * A gate awaiting a decision, then whatever is running, then wherever it stopped
   * -- the block, which is the demo's beat -- then the last stage that finished.
   * Never `plan` by default on a run that has moved past it.
   */
  const following = useMemo<Stage>(() => {
    if (!run) return "plan";
    const open = rows.find((r) => run.awaiting_gates.includes(r.stage as Gate));
    if (open) return open.stage;
    const running = rows.find((r) => r.phase === "running");
    if (running) return running.stage;
    const stopped = rows.find((r) => r.phase === "refused");
    if (stopped) return stopped.stage;
    return rows.filter((r) => r.phase === "done").at(-1)?.stage ?? "plan";
  }, [run, rows]);

  const selected = picked ?? following;

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

  const phase = rows.find((r) => r.stage === selected)?.phase ?? "pending";
  const openGate = run.awaiting_gates[0];

  return (
    <div>
      <p className="eyebrow">
        <Link href="/runs" style={{ color: "inherit" }}>
          Runs
        </Link>{" "}
        / {run.ticket_id}
      </p>

      {/* ── WHAT THIS RUN IS ─────────────────────────────────────────────── */}
      <header style={{ marginBottom: "var(--gap-6)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: "var(--gap-4)",
            flexWrap: "wrap",
          }}
        >
          <h1 className="display">{run.ticket_id}</h1>
          <Mark mark={RUN_STATUS[run.status]} />
        </div>
        <p className="prose" style={{ margin: "var(--gap-2) 0 var(--gap-3)" }}>
          {run.ticket_text}
        </p>
        <Facts run={run} readAt={readAt} now={now} live={!ended} />
      </header>

      {/* ── THE DECISION, IF ONE IS OWED ─────────────────────────────────────
          ABOVE THE SPINE, AND THAT PLACEMENT IS WHAT FREED THE SPINE TO GO
          HORIZONTAL. It is the only thing on this page that is an action rather
          than a record, so it is never something a reader has to find. */}
      {openGate ? (
        <div style={{ marginBottom: "var(--gap-6)" }}>
          {/* **THE CONTROLS NEED AN ACTIONS RUN, AND OFFERING THEM WITHOUT ONE IS A
              BUTTON THAT CANNOT WORK.** A gate on this pipeline IS a GitHub
              Environment, released by `POST .../pending_deployments` and by nothing
              else -- so `approveRun` refuses a run carrying no `ci_run_id`, and
              correctly. Reported from the deployed app: a stale run offered
              "Approve gate1" and "Reject gate1" whose only possible outcome was an
              error message. Saying why up front is the same choice `approveRun`
              already made in its refusal text, moved to where somebody sees it
              BEFORE clicking. */}
          {run.ci_run_id ? (
            <GateControls runId={run.run_id} gate={openGate} onRecorded={reload} />
          ) : (
            <div className="card" style={{ borderColor: "var(--border-strong)" }}>
              <p className="eyebrow" style={{ margin: 0 }}>
                {openGate} · waiting, and not decidable here
              </p>
              <p
                className="prose"
                style={{ margin: "var(--gap-2) 0 0", fontSize: "var(--step-small)" }}
              >
                This run carries no GitHub Actions run, so there is no Environment
                for this application to release. A gate is held by GitHub itself and
                can only be opened there. Runs started from this screen record the
                link, so this affects older runs only.
              </p>
            </div>
          )}
        </div>
      ) : null}

      {/* ── THE PIPELINE, AND THE PAGE'S INDEX ───────────────────────────── */}
      <section className="card" style={{ padding: "var(--gap-3) var(--gap-4)" }}>
        <StageSpine
          stages={run.stages}
          runEnded={ended}
          awaitingGates={run.awaiting_gates}
          selected={selected}
          onSelect={setPicked}
        />
        <p
          className="prose"
          aria-live="polite"
          style={{
            margin: "var(--gap-3) 0 0",
            paddingTop: "var(--gap-3)",
            borderTop: "1px solid var(--border)",
            fontSize: "var(--step-small)",
          }}
        >
          {spineSentence(rows, run.awaiting_gates, run.live)}
        </p>
      </section>

      {/* ── WHAT THE SELECTED STAGE PRODUCED ─────────────────────────────── */}
      <section style={{ margin: "var(--gap-6) 0 var(--gap-8)" }}>
        <h2
          className="eyebrow"
          style={{ display: "flex", gap: "var(--gap-3)", alignItems: "baseline" }}
        >
          <span style={{ color: "var(--text)" }}>{selected}</span>
          <span>{openGate === selected ? "your decision" : PHASE_WORD[phase]}</span>
        </h2>

        <AgentOutput
          stage={selected}
          // `?? null` IS NOT DEFENSIVE NOISE -- this page has already died once on
          // exactly this shape. An omitted key is `undefined`, not `null`, and
          // `undefined` SKIPS the "has not run" branch instead of taking it.
          plan={run.plan ?? null}
          dev={run.dev ?? null}
          review={run.review ?? null}
          sre={run.sre ?? null}
        />

        {selected === "security" ? (
          <SecurityPanel security={run.security} scoring={scoring} />
        ) : null}

        {selected === "gate1" || selected === "gate2" || selected === "gate3" ? (
          <GateStage gate={selected} run={run} phase={phase} />
        ) : null}

        {selected === "promote" ? <PromoteStage run={run} phase={phase} /> : null}
      </section>

      {/* ── THE RECORD ───────────────────────────────────────────────────────
          Two things that belong to the whole run rather than to any one stage, so
          they sit below the sequence and are closed by default. `<details>` and
          not a tab: a reader who wants them is looking for them. */}
      <Fold summary="Every decision on this run" count={run.decisions.length}>
        <DecisionLog decisions={run.decisions} />
      </Fold>

      <Fold
        summary="What this run cost"
        count={cost?.stages?.length ?? 0}
        note={costLine(cost)}
      >
        {cost ? (
          <CostPanel cost={cost} />
        ) : (
          <p className="prose" style={{ fontSize: "var(--step-small)" }}>
            No cost record for this run.
          </p>
        )}
      </Fold>
    </div>
  );
}

/**
 * The run's identifying facts, on one line rather than in a six-cell grid.
 *
 * A `<dl>` of six label/value pairs was a quarter of the old page for information
 * a reader consults once. Inline, separated by middots, it is one line and the
 * three LINKS in it -- the pull request, the branch, the Actions run -- stand out
 * because they are the only coloured things in it.
 */
function Facts({
  run,
  readAt,
  now,
  live,
}: {
  run: RunDetail;
  readAt: Date | null;
  now: Date;
  live: boolean;
}) {
  const bits: React.ReactNode[] = [
    <span key="id" className="ident" title="This run's id">
      {run.run_id.slice(0, 8)}
    </span>,
    // WHAT IS BEING CHANGED, and it was on no screen at all. A run named a ticket,
    // a verdict and a cost without ever saying which repository the change was made
    // to -- which is the first thing anybody approving a gate needs to know.
    <span key="repo" className="ident" style={{ color: "var(--text)" }}>
      {run.repository || "repository not recorded"}
    </span>,
    <span key="trigger">
      {run.trigger === "issue"
        ? "started by an issue"
        : run.trigger === "ui"
          ? "started here"
          : run.trigger
            ? `started ${run.trigger}`
            : "started unknown"}
    </span>,
    // `""` MEANS NOBODY RECORDED IT, which is not the same as the agents having
    // used a fixture -- the distinction `model_provenance` exists to keep.
    <span key="model">
      {run.model_provenance ? `agents: ${run.model_provenance}` : "agents: not recorded"}
    </span>,
  ];
  if (run.poisoned) {
    bits.push(
      <span key="poisoned" style={{ color: "var(--refused)" }}>
        ticket carries a credential on purpose
      </span>,
    );
  }
  // THE ISSUE AND THE PULL REQUEST ARE TWO HALVES OF ONE RECORD, so both are
  // offered. The plan, the gate decisions and the outcome are commented onto the
  // ISSUE; the diff, the review and the security verdict onto the PULL REQUEST.
  // Linking only to the second sent a reader to half of what the run wrote.
  if (run.issue_url) {
    bits.push(
      <a key="issue" href={run.issue_url} target="_blank" rel="noreferrer">
        issue ↗
      </a>,
    );
  }
  if (run.pr_url) {
    bits.push(
      <a key="pr" href={run.pr_url} target="_blank" rel="noreferrer">
        pull request ↗
      </a>,
    );
  }
  if (run.ci_run_id) {
    bits.push(
      <a key="ci" href={run.ci_run_id} target="_blank" rel="noreferrer">
        Actions run ↗
      </a>,
    );
  }

  return (
    <p
      style={{
        margin: 0,
        display: "flex",
        flexWrap: "wrap",
        gap: "var(--gap-1) var(--gap-3)",
        fontSize: "var(--step-small)",
        color: "var(--text-muted)",
      }}
    >
      {bits.map((bit, i) => (
        <span key={i} style={{ display: "inline-flex", gap: "var(--gap-3)" }}>
          {i > 0 ? <span aria-hidden="true">·</span> : null}
          {bit}
        </span>
      ))}
      {/* WHEN THIS WAS LAST READ, rather than a spinner. A run spends most of its
          wall clock waiting, so "quiet and current" and "quiet and stuck" are the
          two states a viewer needs told apart -- and a spinner says neither.

          **THREE ANSWERS, NOT TWO, AND THE FIRST VERSION COLLAPSED THEM.** It read
          `run.live` alone and rendered "GitHub could not be reached" for a run that
          simply HAS no Actions run -- reported immediately, on a stale row whose
          `ci_run_id` is empty. Nothing had failed and the screen reported a fault.

          That is `scan_provenance`'s rule arriving in my own new field: a CHOICE
          (there is nothing to ask) and a FAULT (the ask failed) must not share a
          spelling, because they want different reactions -- one is "this run
          predates the link", the other is "try again". */}
      {live ? (
        <span key="read" style={{ display: "inline-flex", gap: "var(--gap-3)" }}>
          <span aria-hidden="true">·</span>
          <span
            style={{
              color: run.live || !run.ci_run_id ? "var(--text-muted)" : "var(--refused)",
            }}
          >
            {run.live
              ? `checked ${secondsAgo(readAt, now)}`
              : run.ci_run_id
                ? "stored record — GitHub could not be reached"
                : "stored record — this run has no Actions run to follow"}
          </span>
        </span>
      ) : null}
    </p>
  );
}

function secondsAgo(at: Date | null, now: Date): string {
  if (!at) return "just now";
  const seconds = Math.max(0, Math.round((now.getTime() - at.getTime()) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.round(seconds / 60)}m ago`;
}

/**
 * A gate, as a stage: the decision made there, or why there is not one.
 *
 * **THE THREE ANSWERS ARE KEPT APART.** Decided, waiting, and never reached want
 * different words -- rendering "no decision" for all three tells somebody a gate
 * was skipped when the run simply has not got there, which is the same
 * did-not-run-versus-passed conflation this repository refuses everywhere else.
 */
function GateStage({ gate, run, phase }: { gate: Gate; run: RunDetail; phase: string }) {
  const decision = run.decisions.find((d) => d.gate === gate);
  if (decision) {
    return <DecisionLog decisions={[decision]} />;
  }
  if (run.awaiting_gates.includes(gate)) {
    return (
      <p className="prose" style={{ fontSize: "var(--step-small)" }}>
        This gate is holding the run. The decision controls are at the top of this
        page.
      </p>
    );
  }
  /**
   * **APPROVED, BUT THE RECORD HAS NOT CAUGHT UP.** This branch is the one the
   * deployed app got wrong: a gate whose job GitHub reports as `success`, with no
   * `HumanDecision` in the stored document yet, rendered "the run has not reached
   * this gate" directly under the word DONE. Two statements on one screen
   * contradicting each other, and the wrong one was the sentence.
   *
   * It happens because a gate job holds no AWS credential, so its decision reaches
   * the record only when the next credentialled job rewrites it — the same lag that
   * made gate3 look like a dead button. The honest thing is to say the gate was
   * released and that the name of whoever released it is not here YET, rather than
   * to deny it happened.
   */
  if (phase === "done") {
    return (
      <p className="prose" style={{ fontSize: "var(--step-small)" }}>
        This gate was approved and the run moved past it. Who decided is not in this
        run&rsquo;s record yet — a gate job cannot write to the run index, so the
        name arrives when the next stage does.
      </p>
    );
  }
  if (phase === "refused") {
    return (
      <p className="prose" style={{ fontSize: "var(--step-small)" }}>
        The run stopped at this gate.
      </p>
    );
  }
  return (
    <p className="prose" style={{ fontSize: "var(--step-small)" }}>
      No decision is recorded here. The run has not reached this gate — that is not
      the same as it having been skipped.
    </p>
  );
}

/** The last stage: what merged, or why nothing did. */
function PromoteStage({ run, phase }: { run: RunDetail; phase: string }) {
  if (phase === "done") {
    return (
      <p className="prose" style={{ fontSize: "var(--step-small)" }}>
        The change was merged.{" "}
        {run.pr_url ? (
          <a href={run.pr_url} target="_blank" rel="noreferrer">
            Open the pull request ↗
          </a>
        ) : null}
      </p>
    );
  }
  return (
    <p className="prose" style={{ fontSize: "var(--step-small)" }}>
      Nothing has been merged. `promote` is the only stage that writes to the
      default branch, and it runs after all three gates.
    </p>
  );
}

/** Cost as one sentence, so the fold says something without being opened. */
function costLine(cost: CostView | null): string {
  if (!cost) return "not recorded";
  // `stages_priced`, NEVER `usd`. Lane E measured that an unwired run has ZERO
  // rows with `usd: null`, while a run whose container fell back to a fixture has
  // a row per stage with `usd: 0.0` -- so a zero total cannot tell the two apart
  // and the row count can.
  const priced = cost.stages_priced ?? 0;
  if (priced === 0) return "no model calls recorded";
  // `usd: null` IS "NOT PRICED", NOT ZERO. An unknown model or a stale price table
  // answers null, and rendering it as $0.0000 would make a missing price table read
  // as a free run -- the distinction `CostView.usd` is declared to keep.
  return typeof cost.usd === "number"
    ? `$${cost.usd.toFixed(4)} over ${priced} priced ${priced === 1 ? "stage" : "stages"}`
    : `${priced} stages recorded, none priced`;
}

/** A closed section that still states what is inside it. */
function Fold({
  summary,
  count,
  note,
  children,
}: {
  summary: string;
  count: number;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <details style={{ borderTop: "1px solid var(--border)", padding: "var(--gap-4) 0" }}>
      <summary
        style={{
          cursor: "pointer",
          fontFamily: "var(--mono)",
          fontSize: "var(--step-small)",
          display: "flex",
          gap: "var(--gap-3)",
          flexWrap: "wrap",
        }}
      >
        <span>{summary}</span>
        <span style={{ color: "var(--text-muted)" }}>{note ?? count}</span>
      </summary>
      <div style={{ marginTop: "var(--gap-4)" }}>{children}</div>
    </details>
  );
}
