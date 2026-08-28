/**
 * HOW THIS PRODUCT SAYS THINGS. One declaration per fact, imported by every
 * screen.
 *
 * WHY A MODULE AND NOT A HELPER PER SCREEN
 * ========================================
 * Three of the distinctions below are load-bearing in CLAUDE.md's sense: getting
 * one wrong makes a screen state something false while rendering perfectly. If
 * each screen decided for itself, the run list could keep `fixture-fallback`
 * and `fixture-stub` apart while the detail screen collapsed them, and nothing
 * would be red. That is this repository's signature defect shape -- a check that
 * cannot distinguish "did not run" from "passed" -- arriving in a renderer.
 *
 * So: ONE table, and `components/__tests__/vocabulary.test.ts` asserts the
 * distinctions hold rather than trusting these comments.
 *
 * TYPES ONLY FROM `@/lib/contract`. No runtime import, so a client component can
 * use this freely.
 */

import type { RunStatus, ScanProvenance } from "@/lib/contract";

/**
 * A rendering decision, as data. `tone` is the semantic role, never a hex value:
 * the palette lives in `globals.css` and a hex here would be a second
 * declaration of a colour Lane I already owns.
 *
 * `form` exists because COLOUR ALONE CANNOT CARRY THESE DISTINCTIONS. Four hues
 * of one chip is exactly how `fixture-fallback` and `fixture-stub` collapse into
 * each other on a projector with the contrast turned down, or for a reader with
 * deuteranopia. So a fault gets a different SHAPE, not merely a different hue.
 */
export interface Mark {
  /** The word on screen. Sentence case, because it is read, not shouted. */
  label: string;
  /** What it means, for a title attribute or an adjacent line. */
  meaning: string;
  tone: "neutral" | "accent" | "refused" | "shipped" | "muted";
  form: "solid" | "dashed" | "struck";
}

/**
 * THE THREE PROVENANCE VALUES, PLUS THE FOURTH THAT HAS NO NAME.
 *
 * CLAUDE.md: "Collapsing the last two hides a broken gate behind a demo
 * setting." So `fixture-fallback` is a FAULT and reads as one -- rose, the word
 * "fault", the same tone a refusal gets -- while `fixture-stub` is a CHOICE and
 * reads as merely uncoloured. And `""` is rendered *unknown*, never as a scan:
 * a row written before the field existed must not be promoted to evidence.
 *
 * The `form` column is what survives a bad projector:
 *   scanners         solid   -- a measurement happened
 *   fixture-fallback solid   -- something happened, and it went wrong
 *   fixture-stub     dashed  -- nothing was asked for
 *   ""               struck  -- nobody recorded anything
 */
export const PROVENANCE: Readonly<Record<ScanProvenance, Mark>> = {
  scanners: {
    label: "Scanners ran",
    meaning: "Three real scanners produced this verdict.",
    tone: "shipped",
    form: "solid",
  },
  "fixture-fallback": {
    label: "Scanner fault",
    meaning:
      "A scanner raised and a fixture stood in. The gate did not measure this " +
      "change -- treat the verdict as unproven and check the scanners.",
    tone: "refused",
    form: "solid",
  },
  "fixture-stub": {
    label: "Scan not requested",
    meaning:
      "Nobody asked for a scan on this run. Not a failure, and not a scan " +
      "either.",
    tone: "muted",
    form: "dashed",
  },
  "": {
    label: "Provenance unknown",
    meaning:
      "This run recorded no provenance, so whether the scanners ran cannot be " +
      "answered from here.",
    tone: "muted",
    form: "struck",
  },
};

/**
 * A run's status. `blocked` is NOT `failed`, and the two must never share a
 * treatment: `contract.ts` says a UI painting them alike "would show the demo's
 * central beat as a crash". A block is the deterministic rule WORKING.
 */
export const RUN_STATUS: Readonly<Record<RunStatus, Mark>> = {
  running: {
    label: "Running",
    meaning: "Stages are still executing.",
    tone: "accent",
    form: "solid",
  },
  blocked: {
    label: "Blocked",
    meaning:
      "The deterministic security rule refused this change. This is the gate " +
      "working, not a crash.",
    tone: "refused",
    form: "solid",
  },
  rejected: {
    label: "Rejected",
    meaning: "A person refused a gate.",
    tone: "refused",
    form: "solid",
  },
  promoted: {
    label: "Promoted",
    meaning: "Merged, past all three gates.",
    tone: "shipped",
    form: "solid",
  },
  failed: {
    label: "Failed",
    meaning:
      "The run ended without a verdict -- a crash, or the revision cap ran out.",
    tone: "muted",
    form: "solid",
  },
};

/** The security verdict. Two values, and neither is ever a default. */
export const VERDICT: Readonly<Record<"pass" | "block", Mark>> = {
  pass: {
    label: "Pass",
    meaning: "No finding reached the blocking threshold.",
    tone: "shipped",
    form: "solid",
  },
  block: {
    label: "Block",
    meaning: "At least one finding is at or above the threshold.",
    tone: "refused",
    form: "solid",
  },
};

/**
 * A verdict that has not been produced. NOT "pass".
 *
 * `RunSummary.verdict` is `null` until security runs, and `contract.ts` says a
 * default of `"pass"` "would paint a run that has not been scanned as one that
 * was cleared". So the absent case gets its own words.
 */
export const VERDICT_ABSENT: Mark = {
  label: "Not scanned",
  meaning: "Security has not run on this run yet.",
  tone: "muted",
  form: "dashed",
};

/**
 * MONEY. `null` means NOT PRICED; `0.0` means priced and free.
 *
 * Rendering `null` as `$0.00` makes a missing price table look like a free run,
 * which is the same defect as an unscanned run reading as cleared. So the two
 * answers do not share a shape: one is a dash and a word, the other is a
 * figure.
 *
 * Four decimal places because a single stage of this pipeline costs on the order
 * of $0.0085 -- `toFixed(2)` renders that as `$0.01` at best and `$0.00` at
 * worst, and a real cost displayed as zero is indistinguishable from an unwired
 * one.
 */
export function renderUsd(usd: number | null): string {
  if (usd === null) return "not priced";
  return `$${usd.toFixed(4)}`;
}

/**
 * Is the cost record WIRED? Read `stages_priced`, never `usd`.
 *
 * Lane E measured all three cases: an unwired run has zero rows and `usd: null`;
 * a wired run whose container fell back has one row and `usd: 0.0`. `usd === 0`
 * cannot tell them apart, so this function refuses to look at it.
 */
export function costIsRecorded(stagesPriced: number): boolean {
  return stagesPriced > 0;
}

/**
 * A cache hit rate. `null` is a zero denominator -- nothing was measured -- and
 * is not `0.0%`.
 *
 * One decimal place, matching Lane E's `_pct`. Lane E also measured that a rate
 * of `1e-06` renders `0.0%` while comparing unequal to zero, which is why the
 * findings list beside this number is what carries the alarm and this function
 * only formats.
 */
export function renderRate(rate: number | null): string {
  if (rate === null) return "not measured";
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * A recorded timestamp, as a person reads it.
 *
 * LIVES HERE BECAUSE TWO SCREENS RENDER THE SAME FIELD. `RunSummary.created_at`
 * appeared as a formatted date on the run list and as a bare ISO-8601 string on
 * the costs screen -- the same timestamp reading as two different things depending
 * on which screen you were looking at. That is the drift this module exists to
 * prevent, arriving in a formatter rather than in a colour.
 *
 * A FIXED LOCALE, not the reader's. `toLocaleString(undefined, ...)` reads the
 * runtime's locale, which differs between the server render and the browser, and
 * React then reports a hydration mismatch on a date that was never wrong --
 * `CostPanel`'s own number formatter carries the same note for the same reason.
 *
 * AN UNPARSEABLE VALUE RETURNS ITSELF rather than `Invalid Date`. The raw string
 * is what somebody can act on; `Invalid Date` names the browser's problem instead
 * of the data's. Callers keep the machine-readable form in `<time dateTime>`
 * beside it.
 */
const WHEN = new Intl.DateTimeFormat("en-GB", {
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
  timeZoneName: "short",
});

export function renderWhen(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  // UTC, and it SAYS so. Every other timestamp in this product is the recorded
  // UTC value, and a local-time render makes two people reading the same run
  // disagree about when it happened.
  return WHEN.format(new Date(t));
}
