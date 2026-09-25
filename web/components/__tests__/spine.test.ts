/**
 * THE SPINE A READER SEES, driven end to end from GitHub's jobs and the run's record.
 *
 * **RUN 71, AS THE DEPLOYED APP DREW IT:** `develop` in rose, `review` and `security`
 * as never having run, and "Stopped at develop. Nothing after it ran" -- directly
 * above the security verdict those two stages produced. Two causes, one per layer:
 * `ci-view` put the block on the develop JOB, and `phases` then drew every stage
 * after the first stop as dead, whatever it had recorded.
 *
 * So this file goes through BOTH layers, the way the page does: `stagesFromCi` into
 * `phases` into `spineSentence`. A test of either layer alone passed while the screen
 * was wrong.
 */

import { describe, expect, it } from "vitest";

import { stagesFromCi } from "../../lib/ci-view";
import type { CiProgress } from "../../lib/dispatch";
import { phases, spineSentence } from "../StageSpine";

/** `develop` failed and nothing after it ran -- the shape of exits 3 and 4 alike. */
const DEVELOP_FAILED: CiProgress = {
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

function spine(record: Record<string, unknown>) {
  const rows = phases(stagesFromCi(DEVELOP_FAILED, record), true);
  return {
    phase: Object.fromEntries(rows.map((r) => [r.stage, r.phase])),
    sentence: spineSentence(rows, [], true),
  };
}

describe("a run the security rule blocked", () => {
  const { phase, sentence } = spine({
    status: "blocked",
    plan: { tasks: ["a"] },
    dev: { branch: "feat/x" },
    review: { verdict: "changes_requested" },
    security: { verdict: "block" },
  });

  it("draws develop and review as done and the stop on security", () => {
    expect(phase.plan).toBe("done");
    expect(phase.develop).toBe("done");
    expect(phase.review).toBe("done");
    expect(phase.security).toBe("refused");
  });

  it("draws only the stages after security as never having run", () => {
    for (const stage of ["gate2", "sre", "gate3", "promote"]) {
      expect(phase[stage], stage).toBe("never");
    }
  });

  it("says where it stopped, and that the stop was security", () => {
    expect(sentence).toBe(
      "Stopped at security. Nothing after it ran — those stages are not waiting, they will never start.",
    );
  });
});

describe("a run that hit the revision cap", () => {
  const { phase, sentence } = spine({
    status: "failed",
    plan: { tasks: ["a"] },
    dev: { branch: "feat/x" },
    review: { verdict: "changes_requested" },
    security: { verdict: "pass" },
  });

  it("stops at review, and still shows the scanners that ran after the loop", () => {
    expect(phase.review).toBe("refused");
    // THE SECOND HALF OF THE BUG. `phases` drew every stage after the first stop as
    // dead, so a security stage with a recorded pass rendered "did not run".
    expect(phase.security).toBe("done");
    expect(phase.gate2).toBe("never");
  });

  it("names security as the last stage that ran", () => {
    expect(sentence).toBe(
      "Stopped at review. Nothing after security ran — those stages are not waiting, they will never start.",
    );
  });
});
