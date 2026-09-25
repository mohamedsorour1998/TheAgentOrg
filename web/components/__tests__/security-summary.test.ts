/**
 * WHAT THE SECURITY PANEL CLAIMS, per run and per scanner.
 *
 * The findings below are RUN 71's, transcribed from the deployed page: two gitleaks
 * findings that blocked, one semgrep finding that did not, and nothing from trivy.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { SecurityView } from "@/lib/contract";

import {
  DEFAULT_THRESHOLD,
  SCANNERS,
  findingRows,
  nativeWord,
  runComparison,
  scannerComparisons,
  thresholdOf,
  whereLabel,
} from "../security-summary";

const KEY_ID = {
  tool: "gitleaks",
  rule: "aws-access-key-id",
  severity: "critical",
  file: "app/auth.py",
  line: 3,
  description: "AWS access key ID",
} as const;
const SECRET = { ...KEY_ID, rule: "aws-secret-access-key", line: 4, description: "AWS secret access key" };
const TIMEOUT = {
  tool: "semgrep",
  rule: "agentorg.security.python.flask.missing-timeout",
  severity: "low",
  file: "app/auth.py",
  line: 6,
  description: "Redis client created without a socket timeout.",
} as const;

const RUN_71: SecurityView = {
  verdict: "block",
  findings: [KEY_ID, SECRET, TIMEOUT],
  blocking: [KEY_ID, SECRET],
  explanation: "The change was blocked because it introduced hardcoded AWS credentials.",
  scan_provenance: "scanners",
  scoring: [
    { tool: "gitleaks", rule: "aws-access-key-id", native: "", mapped: "critical", threshold: "high", blocking: true },
    { tool: "gitleaks", rule: "aws-secret-access-key", native: "", mapped: "critical", threshold: "high", blocking: true },
    { tool: "semgrep", rule: TIMEOUT.rule, native: "<not recorded>", mapped: "low", threshold: "high", blocking: false },
  ],
};

/** The same change, cleared: one low semgrep finding and nothing else. */
const PASSED: SecurityView = {
  ...RUN_71,
  verdict: "pass",
  findings: [TIMEOUT],
  blocking: [],
  scoring: [RUN_71.scoring[2]!],
};

const CLEAN: SecurityView = { ...RUN_71, verdict: "pass", findings: [], blocking: [], scoring: [] };

describe("per scanner, on run 71", () => {
  const byTool = Object.fromEntries(scannerComparisons(RUN_71).map((s) => [s.tool, s]));

  it("gives all three scanners a line, including the one that found nothing", () => {
    expect(scannerComparisons(RUN_71).map((s) => s.tool)).toEqual(["gitleaks", "semgrep", "trivy"]);
  });

  it("gitleaks: critical, at or above the threshold, blocks", () => {
    expect(byTool.gitleaks).toMatchObject({ worst: "critical", blocks: true, findings: 2, blocking: 2 });
  });

  it("semgrep: low, below the threshold, does not block", () => {
    expect(byTool.semgrep).toMatchObject({ worst: "low", blocks: false, findings: 1, blocking: 0 });
  });

  it("trivy: nothing to compare", () => {
    expect(byTool.trivy).toMatchObject({ worst: null, blocks: false, findings: 0 });
  });

  it("names a scanner that failed as faulted", () => {
    const fault = {
      tool: "trivy",
      rule: "trivy-scanner-error",
      severity: "high",
      file: "<trivy scanner>",
      line: 0,
      description: "trivy timed out",
    } as const;
    const faulted = { ...RUN_71, findings: [fault], blocking: [fault], scoring: [] };
    const trivy = scannerComparisons(faulted).find((s) => s.tool === "trivy");
    expect(trivy).toMatchObject({ faulted: true, blocks: true, worst: "high" });
  });
});

describe("the whole run, blocked or passed", () => {
  it("a block compares the worst blocking finding", () => {
    expect(runComparison(RUN_71)).toMatchObject({ worst: "critical", blocks: true });
  });

  it("a pass still shows its worst finding against the threshold", () => {
    expect(runComparison(PASSED)).toMatchObject({ worst: "low", blocks: false, findings: 1 });
  });

  it("a clean run has nothing to compare", () => {
    expect(runComparison(CLEAN)).toMatchObject({ worst: null, blocks: false, findings: 0 });
  });
});

describe("the panel decides nothing", () => {
  /**
   * THE `≥` COMES FROM THE RULE'S OWN LIST. A record whose `critical` finding is NOT
   * in `blocking` is inconsistent, and the panel must show what the rule decided
   * rather than what a second comparison here would conclude. An audit view with its
   * own `>=` reads as proof of a decision nobody made.
   */
  it("reads 'blocks' off the blocking list, not off the severity", () => {
    const disagreeing = { ...RUN_71, blocking: [] };
    expect(runComparison(disagreeing).blocks).toBe(false);
    expect(scannerComparisons(disagreeing).find((s) => s.tool === "gitleaks")?.blocks).toBe(false);
  });
});

describe("the threshold", () => {
  it("is read off the run's own scoring rows", () => {
    expect(thresholdOf(RUN_71, null)).toEqual({ value: "high", recorded: true });
  });

  it("is the default, SAID to be the default, when the run recorded none", () => {
    // A run with no findings writes no scoring row, so nothing names the threshold.
    expect(thresholdOf(CLEAN, null)).toEqual({ value: DEFAULT_THRESHOLD, recorded: false });
  });
});

describe("one table where there were two", () => {
  it("folds each finding's scoring row in, blocking findings first", () => {
    const rows = findingRows(RUN_71);
    expect(rows.map((r) => [r.finding.rule, r.native, r.scored, r.blocks])).toEqual([
      ["aws-access-key-id", "", "critical", true],
      ["aws-secret-access-key", "", "critical", true],
      [TIMEOUT.rule, "<not recorded>", "low", false],
    ]);
  });

  it("borrows nothing from a neighbour when the rows do not line up", () => {
    const shuffled = { ...RUN_71, scoring: [...RUN_71.scoring].reverse() };
    expect(findingRows(shuffled).every((r) => r.native === null)).toBe(true);
  });

  it("says the scanner's own word plainly", () => {
    expect(nativeWord("")).toBe("none — policy");
    expect(nativeWord("<not recorded>")).toBe("not recorded");
    expect(nativeWord(null)).toBe("not recorded");
    expect(nativeWord("WARNING")).toBe("warning");
  });
});

describe("the scanner list", () => {
  /**
   * A SECOND DECLARATION, CHECKED AGAINST THE FIRST. `SCANNERS` restates
   * `scoring.py`'s `POLICY` keys; a scanner added there and not here would get no
   * card, and its findings would be listed under nobody.
   */
  it("is exactly the scanners the scoring policy covers", () => {
    const source = readFileSync(
      join(__dirname, "..", "..", "..", "agentorg", "security", "scoring.py"),
      "utf8",
    );
    const policy = [...source.matchAll(/^\s{4}"(\w+)": ScannerScoring\(/gm)].map((m) => m[1]);
    expect(policy.length, "no POLICY keys found; this test would pin nothing").toBeGreaterThan(0);
    expect([...policy].sort()).toEqual([...SCANNERS].sort());
  });
});

describe("where a finding is", () => {
  it("shows the added line when there is one", () => {
    expect(whereLabel("app/auth.py", 3)).toBe("app/auth.py · 3");
  });

  it("shows only the file for a package-level finding, which has no line", () => {
    // Trivy's CVE findings on a pinned requirement arrive with line 0.
    expect(whereLabel("requirements.txt", 0)).toBe("requirements.txt");
  });
});
