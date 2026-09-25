/**
 * THE RUN AS GITHUB SEES IT, TRANSLATED INTO THE NINE STAGES.
 *
 * Pure functions over a `CiProgress` and the stored state document. No fetch, no
 * AWS, no `next/*` — so the translation that decides what every viewer is shown can
 * be driven directly by a test, which is the half of this that was never checkable
 * while the answer came out of a DynamoDB row nobody could construct in a unit test.
 *
 * ── WHY THERE ARE NINE STAGES AND SEVEN JOBS ─────────────────────────────────
 *
 * `review` and `security` are not jobs. `develop` contains the developer↔reviewer
 * loop, the pull request and the security verdict, because none of those is a gate
 * boundary and the loop iterates an unknown number of times — which Actions cannot
 * express as "repeat until". So two of the nine stages have no job to read, and
 * their evidence is the RESULT the run recorded rather than a job's status.
 *
 * That asymmetry is the reason this file exists instead of a `Record<Stage, string>`
 * at the call site: the mapping is not one-to-one, and a lookup that silently
 * answered `undefined` for two stages would render the two most important ones —
 * the reviewer's objections and the scanners' verdict — as never having run.
 *
 * ── WHAT IS DELIBERATELY NOT DERIVED FROM GITHUB ─────────────────────────────
 *
 * The run's OUTPUT. GitHub knows a job succeeded; it does not know the verdict, the
 * findings, the diff or the cost. Those come from the state document and only from
 * there. **GitHub answers "what is happening"; the document answers "what it
 * produced."** Mixing the two — inferring a pass from a green job — would be the
 * did-not-run-versus-passed conflation this repository exists to refuse: a
 * `develop` job exits 3 on a BLOCK and GitHub reports that as an ordinary failure,
 * indistinguishable from a crash.
 */

import type { Gate, JobStatus, RunStatus, Stage, StageView } from "@/lib/contract";
import type { CiProgress } from "@/lib/dispatch";

/** The nine, in order. One declaration; `StageSpine` imports this. */
export const STAGE_ORDER: readonly Stage[] = [
  "plan",
  "gate1",
  "develop",
  "review",
  "security",
  "gate2",
  "sre",
  "gate3",
  "promote",
];

/** The three that hold for a person. */
export const GATES: readonly Gate[] = ["gate1", "gate2", "gate3"];

/**
 * Stage → the workflow job that runs it, `""` where none does.
 *
 * The job names are the job KEYS in `run-pipeline.yml`; no job declares a `name:`,
 * so GitHub reports the key. Verified against run 35057681679, which listed exactly
 * `plan gate1 develop gate2 sre gate3 promote` plus the three rejection recorders.
 */
const JOB: Readonly<Record<Stage, string>> = {
  plan: "plan",
  gate1: "gate1",
  develop: "develop",
  review: "",
  security: "",
  gate2: "gate2",
  sre: "sre",
  gate3: "gate3",
  promote: "promote",
};

/**
 * The stage that must have SUCCEEDED before a gate can be refused by a person.
 *
 * **THIS IS THE REJECTION RECORDERS' DISCRIMINATOR, AND IT IS NOT REDUNDANT.** A
 * gate the run never reached is `skipped` too, so a gate job's own result reads
 * identically for "a human refused this" and "the run stopped earlier". If the
 * preceding stage succeeded, the only remaining reason the gate did not run is the
 * person. `run-pipeline.yml`'s three recorder jobs carry the same clause, and this
 * repository has already paid for its absence: run 32509257195 recorded a BLOCK and
 * then overwrote it with `rejected` attributed to somebody who never saw the gate.
 */
const BEFORE: Readonly<Record<Gate, Stage>> = {
  gate1: "plan",
  gate2: "develop",
  gate3: "sre",
};

/** A run whose status can no longer change. */
const TERMINAL: readonly string[] = ["blocked", "rejected", "failed", "promoted"];

/** Whatever the document records a stage produced, by the field that proves it. */
export type RunStateDoc = Record<string, unknown>;

function produced(state: RunStateDoc | null, key: string): boolean {
  return state != null && state[key] != null;
}

/** GitHub's word for one job, or `null` when the run has no such job. */
function jobOf(progress: CiProgress, stage: Stage) {
  const name = JOB[stage];
  if (!name) return null;
  return progress.jobs[name] ?? null;
}

/**
 * WHICH GATE IS WAITING RIGHT NOW — from `pending_deployments`, the only authority.
 *
 * An Environment is released by `POST .../pending_deployments` and by nothing else,
 * so the set of gates that can be decided is exactly the set GitHub reports. Reading
 * it anywhere else is how a button appears for a gate the server then refuses.
 */
export function gatesAwaiting(progress: CiProgress): Gate[] {
  const open = new Set(progress.awaiting);
  return GATES.filter((gate) => open.has(gate));
}

/**
 * The nine stages, from the jobs plus the results the run recorded.
 *
 * A stage the run has not reached is OMITTED rather than reported as pending: the
 * spine renders an absent stage as not started, which is true, and inventing a row
 * for it would mean inventing the `attempt`, `exit_code` and `enqueued_at` that row
 * carries. Those are QUEUE facts and an Actions run never enters the queue — a
 * fabricated `exit_code: 0` would be the one field on the screen able to contradict
 * the run it describes.
 */
export function stagesFromCi(progress: CiProgress, state: RunStateDoc | null): StageView[] {
  const open = new Set(progress.awaiting);
  const out: StageView[] = [];

  for (const stage of STAGE_ORDER) {
    const status = statusOf(stage, progress, state, open);
    if (status === null) continue;
    out.push({
      stage,
      status,
      attempt: 1,
      // NOT ZERO. `exit_code: 0` would assert the stage ended cleanly, which this
      // cannot know — the workflow's exit codes live in the job log, not the API.
      exit_code: null,
      enqueued_at: "",
      updated_at: "",
      reclaimed_from: "",
    });
  }
  return out;
}

/** One stage's phase, or `null` for "the run has not reached it". */
function statusOf(
  stage: Stage,
  progress: CiProgress,
  state: RunStateDoc | null,
  open: Set<string>,
): JobStatus | null {
  // The two stages that live INSIDE `develop`. Their evidence is the result the
  // run recorded, because there is no job to ask. While `develop` is still running
  // they are honestly unknown: the document gains `dev`, `review` and `security`
  // together, when the job finishes.
  if (!JOB[stage]) {
    if (!produced(state, stage === "review" ? "review" : "security")) return null;
    if (stoppedInsideDevelop(state) !== stage) return "done";
    return stage === "security" ? "blocked" : "failed";
  }

  const job = jobOf(progress, stage);
  if (!job) return null;

  // A GATE HELD BY AN ENVIRONMENT. GitHub's own job status is `waiting`, and
  // `pending_deployments` names the environment — either is sufficient, and both
  // are checked because a run can report `waiting` in the instant before the
  // deployment row appears.
  if (job.status === "waiting" || open.has(stage)) return "paused";

  if (job.status === "in_progress") return "claimed";
  if (job.status !== "completed") return null;

  switch (job.conclusion) {
    case "success":
      return "done";
    case "skipped":
      // A GATE SKIPPED AFTER ITS PREDECESSOR SUCCEEDED IS A REFUSAL -- a reviewer
      // clicking Reject in the Actions UI. Anything else skipped is a stage the run
      // never reached, which is not a status at all.
      return isRefusedGate(stage, progress) ? "rejected" : null;
    case "cancelled":
      return "failed";
    case "failure":
      // A GATE ENDING `failure` IS A REFUSAL BEFORE IT IS A FAULT. Rejecting through
      // the REST API -- which is what this application's Reject button does -- ends
      // the gate job `failure`, not `skipped`. Checked first, because `failed` here
      // would paint a person's decision as a crash.
      if (isRefusedGate(stage, progress)) return "rejected";
      // `develop` EXITS NON-ZERO WHEN A STAGE INSIDE IT STOPPED THE RUN, and GitHub
      // cannot tell that from a crash. The document can -- and when it names the
      // stage that stopped, the developer itself did its work and the mark goes on
      // that stage instead. See `stoppedInsideDevelop`.
      if (stage === "develop" && stoppedInsideDevelop(state) !== null) return "done";
      return "failed";
    default:
      return null;
  }
}

/**
 * WHICH STAGE INSIDE THE `develop` JOB ENDED THE RUN, from the run's own record.
 *
 * **THE BLOCK WAS DRAWN ON `develop`, AND THE SPINE THEN ERASED THE TWO STAGES THAT
 * DECIDED IT.** Reported from the deployed app on run 71: the spine read `develop`
 * in rose, `review` and `security` as never having run, and "Stopped at develop.
 * Nothing after it ran" -- directly above a security panel showing the verdict those
 * scanners produced. Both stages had run; the job they run inside is what GitHub
 * reports, so the block landed on the job and everything after it was drawn dead.
 *
 * `scripts/run_stage.py:_stage_develop` has exactly two deliberate non-zero exits,
 * and each is decided by one stage:
 *
 *     security verdict `block`                      exit 3   -> "security"
 *     reviewer never approved, scanners passed      exit 4   -> "review"
 *
 * Anything else -- a crash, or a record that says neither -- answers `null`, and
 * the job's failure stays on `develop`, because that is all anybody knows.
 *
 * The cap case reads `status === "failed"` as well as the verdicts: mid-loop the
 * review verdict is routinely `changes_requested`, so the verdict alone would call
 * a crash during the second developer pass a reviewer's refusal.
 */
export function stoppedInsideDevelop(state: RunStateDoc | null): "security" | "review" | null {
  if (verdictIsBlock(state)) return "security";
  const security = (state?.security ?? null) as Record<string, unknown> | null;
  const review = (state?.review ?? null) as Record<string, unknown> | null;
  if (
    state?.status === "failed" &&
    security?.verdict === "pass" &&
    review != null &&
    review.verdict !== "approve"
  ) {
    return "review";
  }
  return null;
}

function verdictIsBlock(state: RunStateDoc | null): boolean {
  const security = (state?.security ?? null) as Record<string, unknown> | null;
  return security?.verdict === "block";
}

/**
 * True when a person refused this gate: the stage before it SUCCEEDED and the gate
 * itself did not.
 *
 * **IT USED TO TEST FOR `skipped` AND ONLY `skipped`, AND THAT MISSED EVERY
 * REJECTION THE APPLICATION ITSELF MAKES.** CLAUDE.md records that "a rejected
 * GitHub Environment SKIPS its job", which is true when a reviewer clicks Reject in
 * the Actions UI. MEASURED on runs 35678602890 and 35676065361, both refused
 * through this application's own button -- which rejects through
 * `POST .../pending_deployments` with a comment:
 *
 *     approvals -> gate1: rejected by mohamedsorour1998 -- "stop"
 *     jobs      -> gate1: completed/failure        <- NOT skipped
 *
 * So both runs rendered as **FAILED** when a person had **REFUSED** them. That is
 * the one distinction this product exists to draw, inverted: "somebody decided" read
 * as "something broke", on the demo's third beat.
 *
 * **THE RULE IS NOW THE RECORDERS' RULE, VERBATIM.** `run-pipeline.yml`'s three
 * `gate*-rejected` jobs fire on `result != 'success' && result != 'cancelled'` --
 * they never enumerate the passing spellings, which is exactly why they got this
 * right and this did not. Cancelled stays excluded for its own recorded reason: a
 * cancelled run is one where NOBODY decided, and attributing a decision to a person
 * who never saw the gate is the inverse of the defect the recorders prevent.
 */
function isRefusedGate(stage: Stage, progress: CiProgress): boolean {
  if (!(GATES as readonly string[]).includes(stage)) return false;
  const outcome = progress.jobs[JOB[stage]]?.conclusion ?? "";
  if (outcome === "success" || outcome === "cancelled" || outcome === "") return false;
  // THE DISCRIMINATOR: a gate the run never REACHED is also not-success, so the
  // stage before it must have succeeded for this to be a person's decision.
  const before = BEFORE[stage as Gate];
  return progress.jobs[JOB[before]]?.conclusion === "success";
}

/**
 * The run's status, with GitHub allowed to CORRECT a stale document and nothing else.
 *
 * ── THE ONE DIRECTION THIS MAY MOVE ──────────────────────────────────────────
 *
 * A stored TERMINAL status always wins. The document is written by the pipeline
 * itself, which knows why it stopped; GitHub knows only that a job failed. So this
 * can turn a stale `running` into an ending, and can never overwrite an ending with
 * a different one — `blocked` must not become `failed` because the `develop` job
 * exited non-zero, which is exactly what a block looks like from outside.
 *
 * **THAT ASYMMETRY IS THE WHOLE SAFETY PROPERTY.** Exit code 3 is deliberately not
 * 1 so the poisoned demo is distinguishable from a broken workflow; a reconciler
 * that re-derived the ending from the job's conclusion would throw that distinction
 * away at the last step, on the screen where it matters most.
 */
export function reconcileStatus(
  stored: string,
  progress: CiProgress | null,
  state: RunStateDoc | null,
): RunStatus {
  const known = ["running", "blocked", "rejected", "failed", "promoted"];
  const safe = (known.includes(stored) ? stored : "running") as RunStatus;
  if (TERMINAL.includes(safe)) return safe;
  if (!progress) return safe;
  if (progress.status !== "completed") return "running";

  // COMPLETED ON GITHUB, STILL `running` IN THE DOCUMENT. This is the gap the
  // credential-free gate and promote jobs leave, and the order below is the order
  // the pipeline's own exit codes are defined in.
  if (progress.jobs.promote?.conclusion === "success") return "promoted";
  // A PERSON'S REFUSAL, BEFORE ANY FAULT. `isRefusedGate` now carries the whole
  // rule -- not-success, not-cancelled, predecessor succeeded -- so this no longer
  // pre-filters on a spelling, which is how `failure` (every rejection this
  // application makes) was missed.
  for (const gate of GATES) {
    if (isRefusedGate(gate, progress)) return "rejected";
  }
  if (progress.jobs.develop?.conclusion === "failure" && verdictIsBlock(state)) {
    return "blocked";
  }
  return "failed";
}
