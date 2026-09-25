/**
 * THE SECURITY VERDICT, SUMMARISED FOR THE PANEL — per run and per scanner.
 *
 * Pure functions over the run's recorded `SecurityView`, so what the panel claims can
 * be driven directly by a test. Asked for from the deployed app: *"in both cases, run
 * blocked or passed, I want to see and compare our findings with the threshold ... and
 * against each of our 3 scanners"*. The panel used to show the comparison only for a
 * block, and never per scanner.
 *
 * ── THIS FILE DECIDES NOTHING ────────────────────────────────────────────────
 *
 * The `≥` or `<` beside a severity is read off `security.blocking` -- the list the
 * block rule itself returned -- and never computed here by comparing two severities.
 * CLAUDE.md is explicit about why: an audit view with its own `>=` is a second
 * decision path whose only job is to agree with the first, and when it does not, it
 * reads as proof of something that did not happen. `RANK` below orders severities to
 * pick which one to DISPLAY as the worst, and for nothing else.
 */

import type { Finding, ScannerTool, ScoreRow, SecurityView, Severity } from "@/lib/contract";
import type { ScoringResponse } from "@/lib/endpoints";

/**
 * The three scanners, in the order the panel shows them. A second declaration of
 * `agentorg/security/scoring.py`'s `POLICY` keys, so `security-summary.test.ts` reads
 * that file and fails if the two disagree -- a scanner added there and missing here
 * would never get a card, and its findings would appear under nobody.
 */
export const SCANNERS: readonly ScannerTool[] = ["gitleaks", "semgrep", "trivy"];

/** What each one looks for, in the words a reader would use. */
export const LOOKS_FOR: Readonly<Record<ScannerTool, string>> = {
  gitleaks: "credentials committed in the change",
  semgrep: "unsafe code patterns",
  trivy: "known vulnerabilities in dependencies",
};

/** DISPLAY ORDER ONLY -- which severity to show as the worst. Never a decision. */
const RANK: Readonly<Record<Severity, number>> = { low: 0, medium: 1, high: 2, critical: 3 };

/**
 * `config.SECURITY_BLOCK_THRESHOLD`'s default. NOTHING IN THE DEPLOYED PATH SETS THAT
 * VARIABLE (no workflow names it), so this is what applied -- but a run with no
 * findings writes no scoring row and so records no threshold at all, and the panel
 * says so rather than presenting the default as something the run measured.
 */
export const DEFAULT_THRESHOLD: Severity = "high";

export interface Threshold {
  value: Severity;
  /** False when this run carries no row naming it, and `value` is the default. */
  recorded: boolean;
}

export function thresholdOf(security: SecurityView, scoring: ScoringResponse | null): Threshold {
  const fromRow = security.scoring?.[0]?.threshold ?? scoring?.rows?.[0]?.threshold;
  if (fromRow) return { value: fromRow, recorded: true };
  // `ScoringResponse.threshold` is null on the wire for a run with no rows, whatever
  // its type says -- the reader reads it off the first row.
  if (scoring?.threshold) return { value: scoring.threshold, recorded: true };
  return { value: DEFAULT_THRESHOLD, recorded: false };
}

/** One side of the comparison: the worst severity shown, and which way it fell. */
export interface Comparison {
  /** `null` when there was nothing to compare -- no findings. */
  worst: Severity | null;
  /** From the rule's own `blocking` list, never from comparing `worst` to anything. */
  blocks: boolean;
  findings: number;
  blocking: number;
}

function worstOf(findings: readonly Finding[]): Severity | null {
  let worst: Severity | null = null;
  for (const f of findings) {
    if (worst === null || RANK[f.severity] > RANK[worst]) worst = f.severity;
  }
  return worst;
}

function compare(findings: readonly Finding[], blocking: readonly Finding[]): Comparison {
  // A BLOCKING FINDING IS SHOWN AS THE WORST when there is one, so the displayed
  // severity is always one the rule actually blocked on.
  return {
    worst: blocking.length > 0 ? worstOf(blocking) : worstOf(findings),
    blocks: blocking.length > 0,
    findings: findings.length,
    blocking: blocking.length,
  };
}

/** The whole run: the comparison the headline shows. */
export function runComparison(security: SecurityView): Comparison {
  return compare(security.findings ?? [], security.blocking ?? []);
}

export interface ScannerComparison extends Comparison {
  tool: ScannerTool;
  looksFor: string;
  /** The scanner itself failed and reported that as a blocking finding. */
  faulted: boolean;
}

/** One per scanner, all three always -- a scanner with no findings is an answer too. */
export function scannerComparisons(security: SecurityView): ScannerComparison[] {
  return SCANNERS.map((tool) => {
    const findings = (security.findings ?? []).filter((f) => f.tool === tool);
    const blocking = (security.blocking ?? []).filter((f) => f.tool === tool);
    return {
      tool,
      looksFor: LOOKS_FOR[tool],
      faulted: findings.some((f) => f.rule === `${tool}-scanner-error`),
      ...compare(findings, blocking),
    };
  });
}

/** One finding with its scoring row folded in -- one table where there were two. */
export interface FindingRow {
  finding: Finding;
  /** The scanner's own word. `""` = the scanner emits none; `null` = not recorded. */
  native: string | null;
  scored: Severity;
  blocks: boolean;
}

const key = (f: Finding) => `${f.tool}|${f.rule}|${f.file}|${f.line}`;

/**
 * THE JOIN IS BY POSITION, AND ONLY WHEN IT PROVABLY LINES UP. `score_findings` writes
 * one row per finding, in the order of `result.findings`, and `ScoreRow` carries no
 * file or line to key on. So the rows are paired only when the counts match AND every
 * pair names the same tool and rule; otherwise the scanner's own word is "not
 * recorded" rather than borrowed from a neighbour.
 */
export function findingRows(security: SecurityView): FindingRow[] {
  const findings = security.findings ?? [];
  const rows: readonly ScoreRow[] = security.scoring ?? [];
  const aligned =
    rows.length === findings.length &&
    findings.every((f, i) => rows[i]?.tool === f.tool && rows[i]?.rule === f.rule);
  const blocking = new Set((security.blocking ?? []).map(key));

  const out = findings.map((finding, i) => ({
    finding,
    native: aligned ? rows[i]!.native : null,
    scored: aligned ? rows[i]!.mapped : finding.severity,
    blocks: blocking.has(key(finding)),
  }));
  // The findings that stopped the run first; otherwise the scanners' own order.
  return [...out.filter((r) => r.blocks), ...out.filter((r) => !r.blocks)];
}

/** The scanner's own severity word, said plainly. */
export function nativeWord(native: string | null): string {
  if (native === null || native === "<not recorded>") return "not recorded";
  if (native === "") return "none — policy";
  return native.toLowerCase();
}
