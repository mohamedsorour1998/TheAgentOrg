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

/** Held at gate2: `develop` done, the gate `waiting`, everything after queued. */
const AT_GATE2: CiProgress = {
  status: "in_progress",
  conclusion: "",
  jobs: {
    plan: { status: "completed", conclusion: "success" },
    gate1: { status: "completed", conclusion: "success" },
    develop: { status: "completed", conclusion: "success" },
    gate2: { status: "waiting", conclusion: "" },
    sre: { status: "queued", conclusion: "" },
    gate3: { status: "queued", conclusion: "" },
    promote: { status: "queued", conclusion: "" },
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
    // OMITTED, NOT `ready`. A queued job is a stage the run has not reached, and
    // inventing a row for it means inventing the attempt and exit code it carries.
    expect(phaseOf(AT_GATE2, PRODUCED, "sre")).toBeNull();
    expect(phaseOf(AT_GATE2, PRODUCED, "promote")).toBeNull();
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
