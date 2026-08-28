/**
 * WHAT THE RENDERER MUST NOT COLLAPSE.
 *
 * Every test here pins a distinction whose loss makes a screen state something
 * false while rendering perfectly. None asserts a colour or a phrase for its own
 * sake -- a test over the exact word "Blocked" would fail on a copy edit and pin
 * nothing, so these assert that two things DIFFER rather than what either says.
 *
 * The three from the brief, in order: the three provenance values rendered
 * differently, `usd: null` not rendered as money, and `blocked` not rendered as
 * `failed`.
 */

import { describe, expect, it } from "vitest";

import type { ScanProvenance } from "@/lib/contract";
import {
  PROVENANCE,
  RUN_STATUS,
  VERDICT,
  VERDICT_ABSENT,
  costIsRecorded,
  renderRate,
  renderUsd,
} from "@/components/vocabulary";

/** Every member of `ScanProvenance`, restated so a missing key is caught. */
const ALL_PROVENANCE: readonly ScanProvenance[] = [
  "scanners",
  "fixture-fallback",
  "fixture-stub",
  "",
];

describe("scan provenance", () => {
  it("has a mark for every member of the union and nothing extra", () => {
    // Guard: if the table were empty every other test here would pass
    // vacuously, which is the "this test would pin nothing" shape.
    expect(Object.keys(PROVENANCE).length).toBe(ALL_PROVENANCE.length);
    for (const value of ALL_PROVENANCE) {
      expect(PROVENANCE[value].label, `no mark for ${JSON.stringify(value)}`)
        .toBeTruthy();
    }
  });

  it("gives all four values visibly different treatments", () => {
    // The claim is that no two render alike. Compared as whole tuples, because
    // two values may legitimately share a tone (both fixture modes are not
    // "shipped") as long as something else separates them.
    const shapes = ALL_PROVENANCE.map(
      (v) => `${PROVENANCE[v].tone}/${PROVENANCE[v].form}`,
    );
    expect(new Set(shapes).size).toBe(ALL_PROVENANCE.length);
  });

  it("separates a scanner FAULT from a deliberate CHOICE", () => {
    // CLAUDE.md: collapsing these "hides a broken gate behind a demo setting".
    // This is the single most important assertion in the file.
    const fault = PROVENANCE["fixture-fallback"];
    const choice = PROVENANCE["fixture-stub"];
    expect(fault.tone).not.toBe(choice.tone);
    // And the fault must read as a failure, not merely as different.
    expect(fault.tone).toBe("refused");
    expect(choice.tone).not.toBe("refused");
  });

  it("never presents an unrecorded provenance as a scan", () => {
    const unknown = PROVENANCE[""];
    const real = PROVENANCE.scanners;
    expect(unknown.tone).not.toBe(real.tone);
    expect(unknown.form).not.toBe(real.form);
    // The word itself must not claim a measurement happened.
    expect(unknown.label.toLowerCase()).not.toContain("ran");
  });

  it("says in words that a fault leaves the verdict unmeasured", () => {
    // The meaning line is what a person reads when the chip is ambiguous, so a
    // fault whose explanation does not mention the gate is a chip with no
    // recourse. Asserted on substance, not on phrasing.
    expect(PROVENANCE["fixture-fallback"].meaning).toMatch(/fixture|scanner/i);
    expect(PROVENANCE["fixture-fallback"].meaning.length).toBeGreaterThan(40);
  });
});

describe("run status", () => {
  it("does not paint a block the way it paints a failure", () => {
    // `contract.ts`: a UI that painted these alike "would show the demo's
    // central beat as a crash".
    expect(RUN_STATUS.blocked.tone).not.toBe(RUN_STATUS.failed.tone);
  });

  it("does not paint a block the way it paints a promotion", () => {
    expect(RUN_STATUS.blocked.tone).not.toBe(RUN_STATUS.promoted.tone);
  });

  it("explains that a block is the rule working", () => {
    expect(RUN_STATUS.blocked.meaning).toMatch(/working|not a crash/i);
  });
});

describe("security verdict", () => {
  it("keeps an absent verdict distinct from a pass", () => {
    // A default of "pass" would paint an unscanned run as a cleared one.
    expect(VERDICT_ABSENT.tone).not.toBe(VERDICT.pass.tone);
    expect(VERDICT_ABSENT.label.toLowerCase()).not.toContain("pass");
  });

  it("keeps a block distinct from a pass", () => {
    expect(VERDICT.block.tone).not.toBe(VERDICT.pass.tone);
  });
});

describe("cost", () => {
  it("does not render an unpriced run as money", () => {
    // THE DISTINCTION: `null` is not priced, `0.0` is priced and free.
    expect(renderUsd(null)).not.toContain("$");
    expect(renderUsd(null)).not.toContain("0.00");
  });

  it("renders a priced free run as a real figure", () => {
    expect(renderUsd(0)).toContain("$");
    // And it must differ from the unpriced answer, which is the whole point.
    expect(renderUsd(0)).not.toBe(renderUsd(null));
  });

  it("keeps enough decimals for a real stage cost to be visible", () => {
    // A stage costs on the order of $0.0085. Two decimal places renders that as
    // $0.01 or $0.00, and a real cost shown as zero reads as an unwired run.
    expect(renderUsd(0.0085)).not.toBe(renderUsd(0));
  });

  it("reads wiring off the stage count and never off the total", () => {
    // Lane E measured: unwired -> 0 rows, usd null. Wired, fell back -> 1 row,
    // usd 0.0. So a zero total with rows is still a recorded run.
    expect(costIsRecorded(0)).toBe(false);
    expect(costIsRecorded(1)).toBe(true);
  });

  it("does not render an unmeasured cache rate as zero percent", () => {
    expect(renderRate(null)).not.toContain("0.0%");
    expect(renderRate(0)).toContain("0.0%");
    expect(renderRate(null)).not.toBe(renderRate(0));
  });
});
