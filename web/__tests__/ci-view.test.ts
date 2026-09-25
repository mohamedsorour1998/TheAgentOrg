/**
 * THE RUN AS GITHUB SEES IT — the translation both reported gate bugs lived in.
 *
 * **MEASURED ON RUN 35057681679, AND THIS FILE IS THAT MEASUREMENT AS A TEST.**
 * Every job `success`, the workflow `completed`, `promote` merged — and the stored
 * index row read `status: running` with `decisions: [gate1, gate2]`. Reported as
 * two separate bugs (*"when I approve gate3 nothing happened"* and *"when I approve
 * gate 2 it works but it takes 2 min"*), and they are one cause: a gate job holds
 * no AWS credential, so its decision reaches the record only when the NEXT
 * credentialled job rewrites it — and gate3's next job is `promote`, which holds
 * none either.
 *
 * Nothing here needs a table, a token or a network, which is the point: the
 * translation that decides what every viewer is shown used to be reachable only
 * through a DynamoDB row plus a GitHub call, so it was never checkable at all.
 */

import { describe, expect, it } from "vitest";

import { gatesAwaiting, reconcileStatus, stagesFromCi } from "../lib/ci-view";
import type { CiProgress } from "../lib/dispatch";

/** Every job `success`, the run completed — run 35057681679, verbatim. */
const ALL_GREEN: CiProgress = {
  status: "completed",
  conclusion: "success",
  jobs: {
    plan: { status: "completed", conclusion: "success" },
    gate1: { status: "completed", conclusion: "success" },
    develop: { status: "completed", conclusion: "success" },
    gate2: { status: "completed", conclusion: "success" },
    sre: { status: "completed", conclusion: "success" },
    gate3: { status: "completed", conclusion: "success" },
    promote: { status: "completed", conclusion: "success" },
    "gate1-rejected": { status: "completed", conclusion: "skipped" },
    "gate2-rejected": { status: "completed", conclusion: "skipped" },
    "gate3-rejected": { status: "completed", conclusion: "skipped" },
  },
  awaiting: [],
};

/**
 * Held at a gate: the gate `waiting`, and EVERYTHING AFTER IT SIMPLY ABSENT.
 *
 * **THE ABSENCE IS TRANSCRIBED, NOT ASSUMED — I had written `queued` and was
 * wrong.** Read live off run 35354213738 while it was held at gate1, GitHub listed
 * exactly two jobs:
 *
 *     run              status "waiting"   conclusion null
 *     plan             completed          success
 *     gate1            waiting            (null)
 *     pending_deployments -> environment "gate1", current_user_can_approve true
 *
 * A job GitHub has not created yet is not in the response at all. Both shapes end
 * up omitted from the spine, so the code was right either way — but a fixture that
 * disagrees with the API is a test asserting over a world that does not exist, and
 * the next person reading it would take `queued` for the documented behaviour.
 *
 * Note the RUN's own status is `waiting` too, not `in_progress`. `reconcileStatus`
 * treats anything that is not `completed` as running, which is why that distinction
 * costs nothing here — and it is the reason the check is written against
 * `completed` rather than against a list of in-flight spellings.
 */
const AT_GATE2: CiProgress = {
  status: "waiting",
  conclusion: "",
  jobs: {
    plan: { status: "completed", conclusion: "success" },
    gate1: { status: "completed", conclusion: "success" },
    develop: { status: "completed", conclusion: "success" },
    gate2: { status: "waiting", conclusion: "" },
  },
  awaiting: ["gate2"],
};

/** A finished develop stage, as the run's own record holds it. */
const PRODUCED = {
  status: "running",
  plan: { tasks: ["a"] },
  dev: { branch: "feat/x" },
  review: { verdict: "approve" },
  security: { verdict: "pass" },
};

function phaseOf(progress: CiProgress, state: Record<string, unknown> | null, stage: string) {
  return stagesFromCi(progress, state).find((s) => s.stage === stage)?.status ?? null;
}

describe("a run GitHub has finished but the record has not", () => {
  /**
   * THE gate3 BUG, EXACTLY. The approval worked; the page was reading a record
   * that could never be told. Without this correction the run shows `RUNNING` and
   * `gate3 · waiting for your decision` for ever.
   */
  it("reads as promoted, not as still running", () => {
    expect(reconcileStatus("running", ALL_GREEN, PRODUCED)).toBe("promoted");
  });

  it("is waiting at no gate, so no approve button is offered", () => {
    expect(gatesAwaiting(ALL_GREEN)).toEqual([]);
    expect(phaseOf(ALL_GREEN, PRODUCED, "gate3")).toBe("done");
    expect(phaseOf(ALL_GREEN, PRODUCED, "promote")).toBe("done");
  });
});

describe("a run held at a gate", () => {
  /**
   * THE gate2 LAG. `pending_deployments` names the environment the instant the job
   * pauses, where the stored record learns it only once `sre` has run — which is
   * the two minutes that were reported.
   */
  it("names the gate from GitHub's pending deployments", () => {
    expect(gatesAwaiting(AT_GATE2)).toEqual(["gate2"]);
    expect(phaseOf(AT_GATE2, PRODUCED, "gate2")).toBe("paused");
  });

  it("is still running, and the stages after the gate have not started", () => {
    expect(reconcileStatus("running", AT_GATE2, PRODUCED)).toBe("running");
    // OMITTED, NOT `ready`. Inventing a row for an unreached stage means inventing
    // the attempt and exit code it carries -- and those are QUEUE facts an Actions
    // run does not have.
    expect(phaseOf(AT_GATE2, PRODUCED, "sre")).toBeNull();
    expect(phaseOf(AT_GATE2, PRODUCED, "promote")).toBeNull();
  });

  it("treats a job GitHub HAS created but not started the same way", () => {
    // Both shapes occur: absent before GitHub creates the job, `queued` in the
    // window between creation and start. Neither is a stage that has run, and a
    // spine that drew them differently would be reporting an API detail as though
    // it were something about the pipeline.
    const queued: CiProgress = {
      ...AT_GATE2,
      jobs: { ...AT_GATE2.jobs, sre: { status: "queued", conclusion: "" } },
    };
    expect(phaseOf(queued, PRODUCED, "sre")).toBeNull();
  });

  it("shows a stage in flight as running, which is what the spinner reads", () => {
    const working: CiProgress = {
      ...AT_GATE2,
      jobs: { ...AT_GATE2.jobs, develop: { status: "in_progress", conclusion: "" } },
      awaiting: [],
    };
    expect(phaseOf(working, { status: "running", plan: {} }, "develop")).toBe("claimed");
  });
});

describe("the two stages that have no job of their own", () => {
  /**
   * `review` and `security` live INSIDE `develop`, because neither is a gate
   * boundary and the developer↔reviewer loop iterates an unknown number of times.
   * So their evidence is the result the run recorded, and a lookup that answered
   * from the job table would render the reviewer's objections and the scanners'
   * verdict as never having run.
   */
  it("reads them from the record, not from the jobs", () => {
    expect(phaseOf(AT_GATE2, PRODUCED, "review")).toBe("done");
    expect(phaseOf(AT_GATE2, PRODUCED, "security")).toBe("done");
  });

  it("leaves them unstarted while develop is still going", () => {
    const working: CiProgress = {
      ...AT_GATE2,
      jobs: { ...AT_GATE2.jobs, develop: { status: "in_progress", conclusion: "" } },
    };
    expect(phaseOf(working, { status: "running", plan: {} }, "review")).toBeNull();
    expect(phaseOf(working, { status: "running", plan: {} }, "security")).toBeNull();
  });
});

describe("what GitHub is NOT allowed to overwrite", () => {
  /**
   * **THE ONE SAFETY PROPERTY IN THIS FILE.** `develop` exits 3 on a BLOCK and
   * GitHub reports that as an ordinary `failure`, indistinguishable from a crash.
   * Exit code 3 is deliberately not 1 so the poisoned demo can be told apart from
   * a broken workflow — and a reconciler that re-derived the ending from the job's
   * conclusion would throw that distinction away at the last step, on the screen
   * where it matters most.
   */
  const BLOCKED_JOBS: CiProgress = {
    status: "completed",
    conclusion: "failure",
    jobs: {
      plan: { status: "completed", conclusion: "success" },
      gate1: { status: "completed", conclusion: "success" },
      develop: { status: "completed", conclusion: "failure" },
      gate2: { status: "completed", conclusion: "skipped" },
      sre: { status: "completed", conclusion: "skipped" },
      gate3: { status: "completed", conclusion: "skipped" },
      promote: { status: "completed", conclusion: "skipped" },
    },
    awaiting: [],
  };

  it("keeps a recorded block as a block", () => {
    expect(reconcileStatus("blocked", BLOCKED_JOBS, { security: { verdict: "block" } })).toBe(
      "blocked",
    );
  });

  it("derives a block from the verdict when the record is stale", () => {
    // The document is the only thing that knows a non-zero exit was the rule
    // working rather than the workflow breaking.
    expect(reconcileStatus("running", BLOCKED_JOBS, { security: { verdict: "block" } })).toBe(
      "blocked",
    );
    expect(reconcileStatus("running", BLOCKED_JOBS, { security: { verdict: "pass" } })).toBe(
      "failed",
    );
  });

  /**
   * **RUN 71, AS THE DEPLOYED APP DREW IT.** `develop` in rose, `review` and
   * `security` drawn as never having run, and "Stopped at develop. Nothing after it
   * ran" -- above a security panel showing the verdict those two stages produced.
   * The block is decided by security, inside the develop job; the mark goes there.
   */
  const BLOCKED_RECORD = {
    status: "blocked",
    dev: { branch: "feat/x" },
    review: { verdict: "changes_requested" },
    security: { verdict: "block" },
  };

  it("marks the block on security, and review and develop as having run", () => {
    expect(phaseOf(BLOCKED_JOBS, BLOCKED_RECORD, "develop")).toBe("done");
    expect(phaseOf(BLOCKED_JOBS, BLOCKED_RECORD, "review")).toBe("done");
    expect(phaseOf(BLOCKED_JOBS, BLOCKED_RECORD, "security")).toBe("blocked");
    expect(phaseOf(BLOCKED_JOBS, BLOCKED_RECORD, "gate2")).toBeNull();
  });

  it("marks the revision cap on review, with security having passed after it", () => {
    // `_stage_develop` exits 4 when the reviewer never approved: the scanners ran
    // AFTER the loop and cleared the diff, so security is done and review is where
    // the run stopped.
    const capped = {
      status: "failed",
      dev: { branch: "feat/x" },
      review: { verdict: "changes_requested" },
      security: { verdict: "pass" },
    };
    expect(phaseOf(BLOCKED_JOBS, capped, "develop")).toBe("done");
    expect(phaseOf(BLOCKED_JOBS, capped, "review")).toBe("failed");
    expect(phaseOf(BLOCKED_JOBS, capped, "security")).toBe("done");
  });

  it("leaves a crash on develop, because nothing recorded says otherwise", () => {
    // Mid-loop the review verdict is routinely `changes_requested` and the status
    // still `running`: that is a crash, not a reviewer's refusal.
    const crashed = {
      status: "running",
      dev: { branch: "feat/x" },
      review: { verdict: "changes_requested" },
    };
    expect(phaseOf(BLOCKED_JOBS, crashed, "develop")).toBe("failed");
    expect(phaseOf(BLOCKED_JOBS, crashed, "review")).toBe("done");
    expect(phaseOf(BLOCKED_JOBS, crashed, "security")).toBeNull();
  });

  it("does not read a skipped gate after a block as somebody refusing it", () => {
    // THE REJECTION RECORDERS' DISCRIMINATOR. A gate the run never reached is
    // skipped too, so the gate's own result reads identically for "a human refused
    // this" and "the run stopped earlier". This repository has already recorded a
    // BLOCK being overwritten as `rejected` and attributed to a named person who
    // never saw the gate.
    expect(phaseOf(BLOCKED_JOBS, { security: { verdict: "block" } }, "gate2")).toBeNull();
  });
});

describe("a gate a person refused", () => {
  const REFUSED: CiProgress = {
    status: "completed",
    conclusion: "failure",
    jobs: {
      plan: { status: "completed", conclusion: "success" },
      gate1: { status: "completed", conclusion: "skipped" },
      develop: { status: "completed", conclusion: "skipped" },
    },
    awaiting: [],
  };

  it("is a refusal, because the stage before it succeeded", () => {
    expect(phaseOf(REFUSED, { status: "running" }, "gate1")).toBe("rejected");
    expect(reconcileStatus("running", REFUSED, { status: "running" })).toBe("rejected");
  });
});

describe("when GitHub cannot be asked at all", () => {
  /**
   * The stored record is still a true account of everything the run did, so the
   * screen renders from it and says so (`RunDetail.live`). Answering `failed` here
   * — or blanking the run — would turn somebody else's outage into this run's
   * ending.
   */
  it("changes nothing", () => {
    expect(reconcileStatus("running", null, PRODUCED)).toBe("running");
    expect(reconcileStatus("promoted", null, PRODUCED)).toBe("promoted");
  });

  it("refuses a status it does not recognise rather than passing it through", () => {
    expect(reconcileStatus("nonsense", null, PRODUCED)).toBe("running");
  });
});

describe("a gate a person refused THROUGH THIS APPLICATION", () => {
  /**
   * **TRANSCRIBED FROM TWO RUNS THAT WERE RENDERED WRONG.** Runs 35678602890 and
   * 35676065361 were both refused with the app's own Reject button, and both showed
   * as FAILED:
   *
   *     approvals -> gate1: rejected by mohamedsorour1998 -- "stop"
   *     jobs      -> gate1: completed/failure        <- NOT skipped
   *
   * CLAUDE.md records that a rejected Environment SKIPS its job, and that is true of
   * the Actions UI. Rejecting through `POST .../pending_deployments` with a comment
   * -- which is what this application does, and therefore what every refusal in the
   * product looks like -- ends the job `failure` instead.
   *
   * So the reconciler read a person's decision as a crash: the exact
   * somebody-decided-versus-something-broke inversion this product exists to refuse,
   * on the demo's third beat.
   */
  const REFUSED_VIA_API: CiProgress = {
    status: "completed",
    conclusion: "failure",
    jobs: {
      plan: { status: "completed", conclusion: "success" },
      gate1: { status: "completed", conclusion: "failure" },
      "gate1-rejected": { status: "completed", conclusion: "failure" },
    },
    awaiting: [],
  };

  it("reads as rejected, not failed", () => {
    expect(reconcileStatus("running", REFUSED_VIA_API, { status: "running" })).toBe(
      "rejected",
    );
  });

  it("marks the gate itself as where the run stopped", () => {
    expect(phaseOf(REFUSED_VIA_API, { status: "running" }, "gate1")).toBe("rejected");
  });

  /**
   * THE DISCRIMINATOR STILL HOLDS, and it is the half that keeps this honest. A
   * gate that failed while the stage BEFORE it did not succeed is a run that
   * stopped earlier -- not a person's decision. `run-pipeline.yml`'s recorders
   * carry the same clause, and this repository has already recorded a BLOCK being
   * overwritten as `rejected` and attributed to somebody who never saw the gate.
   */
  it("is NOT a refusal when the stage before it did not succeed", () => {
    const stopped_earlier: CiProgress = {
      ...REFUSED_VIA_API,
      jobs: {
        plan: { status: "completed", conclusion: "failure" },
        gate1: { status: "completed", conclusion: "failure" },
      },
    };
    expect(reconcileStatus("running", stopped_earlier, { status: "running" })).toBe(
      "failed",
    );
    expect(phaseOf(stopped_earlier, { status: "running" }, "gate1")).toBe("failed");
  });

  /**
   * CANCELLED IS STILL NOT A REFUSAL, and that exclusion is load-bearing. A
   * cancelled run is one where NOBODY decided. MEASURED on run 32575709109: a
   * recorder fired on `cancelled` and posted "REJECTED by mohamedsorour1998" to an
   * issue, naming a human who never saw the gate -- which CLAUDE.md calls the
   * inverse of the defect that job exists to prevent.
   */
  it("is NOT a refusal when the gate was cancelled", () => {
    const cancelled: CiProgress = {
      ...REFUSED_VIA_API,
      jobs: {
        plan: { status: "completed", conclusion: "success" },
        gate1: { status: "completed", conclusion: "cancelled" },
      },
    };
    expect(reconcileStatus("running", cancelled, { status: "running" })).not.toBe(
      "rejected",
    );
  });

  /**
   * AND A BLOCK IS STILL A BLOCK. `develop` exits 3 on a refused change and GitHub
   * reports that as an ordinary `failure` -- the gate branch must not swallow it,
   * because exit 3 is deliberately not 1 so the poisoned demo is distinguishable
   * from a broken workflow.
   */
  it("does not turn a blocked develop into a rejection", () => {
    const blocked: CiProgress = {
      status: "completed",
      conclusion: "failure",
      jobs: {
        plan: { status: "completed", conclusion: "success" },
        gate1: { status: "completed", conclusion: "success" },
        develop: { status: "completed", conclusion: "failure" },
        gate2: { status: "completed", conclusion: "skipped" },
      },
      awaiting: [],
    };
    expect(reconcileStatus("running", blocked, { security: { verdict: "block" } })).toBe(
      "blocked",
    );
  });
});
