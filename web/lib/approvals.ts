/**
 * MEASURING A RUN, AND RECORDING A DECISION. The I/O half of I5.
 *
 * `web/lib/authz.ts` decides; this file measures the facts it decides over, and
 * performs the one write. Split so that every refusal is testable with no database,
 * and so a route physically cannot hand `decide` a fact it did not measure here.
 */

import type { Permit } from "./authz";
import type { ApprovalResponse } from "./endpoints";
import type { Gate, RunStatus } from "./contract";
import { readPipeline } from "./pipeline";
import type { RunFacts } from "./authz";

/** What the reader answers for one run. */
interface RunFactsPayload {
  run_id: string;
  tenant_id: string;
  repository_full_name: string;
  status: string;
  awaiting_gates: string[];
}

/** The five statuses `RunState.status` may hold. Mirrors `state.py:307`. */
const RUN_STATUSES: readonly string[] = [
  "running",
  "blocked",
  "rejected",
  "promoted",
  "failed",
];

const GATES: readonly string[] = ["gate1", "gate2", "gate3"];

/**
 * The run's facts, or `null` when it does not exist OR is another tenant's.
 *
 * THE TWO CASES ARE COLLAPSED HERE, at the measurement, not at the decision. The
 * reader answers "no such run" for both, so this function cannot tell them apart
 * even if a future edit tried to — which is stronger than a `decide` that chose to
 * report them identically, because a fact that was never measured cannot leak.
 *
 * A STATUS THIS LAYER DOES NOT RECOGNISE BECOMES `failed`, NOT `running`. That is
 * the fail-closed direction and it decides an approval: `failed` is terminal, so an
 * unrecognised status refuses the decision, while `running` would permit it. A run
 * whose status we cannot read is not a run to approve.
 */
export async function runFacts(
  tenantId: string,
  runId: string,
): Promise<RunFacts | null> {
  let payload: RunFactsPayload;
  try {
    payload = await readPipeline<RunFactsPayload>("runs", {
      action: "run_facts",
      tenant_id: tenantId,
      run_id: runId,
    });
  } catch {
    // A REFUSED READ IS `null`, WHICH IS A REFUSAL. The reader reports a
    // cross-tenant attempt and an absent run as `{"error": "no such run"}`, and
    // `pipeline.ts` turns that into a thrown `PipelineError`. Swallowed to `null`
    // here so the two remain one answer; the route then refuses.
    //
    // A read that failed for an UNRELATED reason — the interpreter missing, a
    // timeout — also lands here, and that is the right direction: it refuses the
    // approval rather than permitting one over facts nobody measured.
    return null;
  }

  if (payload.tenant_id !== tenantId) {
    // Defence in depth. The reader already scopes by tenant through Lane B's
    // accessor, so this cannot fire today — and it is here because the cost of
    // being wrong is a cross-tenant approval, and the check is one comparison.
    return null;
  }

  return {
    runId: payload.run_id,
    tenantId: payload.tenant_id,
    repositoryFullName: payload.repository_full_name,
    status: (RUN_STATUSES.includes(payload.status)
      ? payload.status
      : "failed") as RunStatus,
    // A gate the vocabulary does not name is DROPPED rather than carried. `decide`
    // requires the attempted gate to appear in this list, so dropping an
    // unrecognised value refuses a decision on it — carrying it would let a
    // malformed row authorise one.
    awaitingGates: payload.awaiting_gates.filter((gate) =>
      GATES.includes(gate),
    ) as Gate[],
  };
}

/**
 * Record the decision. THE ONLY WRITE IN THIS LANE.
 *
 * ONE WRITE, TO THE QUEUE, AND NOT TO `gates.resume`. That is `scripts/worker.py`'s
 * measured lesson and it is worth quoting because getting it wrong is invisible:
 * its first version called `gates.resume` here as well, and the state then carried
 * the decision TWICE —
 *
 *     decisions:
 *        gate1 approved by tester
 *        gate1 approved by github-environment-reviewer
 *
 * — two rows for one click, the second attributed to a reviewer who does not exist
 * on this path, because the gate STAGE is the recorder and hardcodes that `by`. On
 * a timeline a judge reads, one human decision renders as two.
 *
 * So `queue.resume` decides WHEN the gate stage may run, and the stage writes WHAT
 * it recorded. `gates.py`'s "one writer" rule, one layer up.
 *
 * `by` TRAVELS TO THE STAGE. `queue.resume(approver=...)` puts it on the row
 * because "the person clicks in one process and the stage that records their name
 * runs in another, minutes later" — without it every queued approval reaches
 * `_stage_gate`'s default and is recorded as `github-environment-reviewer`, naming
 * a GitHub Environment that never held this job, on the one field whose whole
 * purpose is attributing a decision to a human.
 */
export async function recordDecision(permit: Permit): Promise<ApprovalResponse> {
  const answer = await readPipeline<{ status: string; by: string }>("approve", {
    run_id: permit.runId,
    gate: permit.gate,
    decision: permit.decision,
    // FROM THE PERMIT, which built it from the session. Never from a request body.
    by: permit.by,
    reason: permit.reason,
    tenant_id: permit.tenantId,
  });

  return {
    recorded: true,
    run_id: permit.runId,
    gate: permit.gate,
    decision: permit.decision,
    // READ BACK from the writer rather than echoed from the permit, so a screen
    // shows what was actually recorded. If the two ever disagreed, the response
    // would say so instead of reassuring.
    by: answer.by,
    status: answer.status,
  };
}
