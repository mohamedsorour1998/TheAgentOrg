/**
 * THE VERDICT, AND THE ARITHMETIC BEHIND IT -- for the run, and for each scanner.
 *
 * THE ONE RISK THIS DESIGN TAKES: the hero is not a banner saying "blocked" -- it is
 * the comparison that produced the verdict, `critical ≥ high`, at display size. A
 * block here is not an opinion: no model is involved, it is three scanners plus five
 * lines of Python comparing a finding's severity against a threshold. A judge's first
 * question is "how do I know this is deterministic?", and the honest answer is the
 * arithmetic.
 *
 * ── WHY IT WAS REORGANISED ───────────────────────────────────────────────────
 *
 * Asked for from the deployed app: *"in both cases, run blocked or passed, I want to
 * see and compare our findings with the threshold ... against each of our 3 scanners
 * ... reorganise this page to short, effective, smart"*. The comparison used to appear
 * only for a block, never per scanner, and the page carried two tables over the same
 * findings (what was found, then how it was scored) plus four stat tiles repeating
 * what the verdict already said. Now, in reading order:
 *
 *   1. the run's comparison -- worst finding against the threshold, pass or block
 *   2. the same comparison per scanner -- all three, including one that found nothing
 *   3. one table: each finding, the scanner's word, ours, and which way it fell
 *   4. the model's prose, folded, because it describes the verdict and did not make it
 *
 * Every `≥` and `<` is read off the rule's own `blocking` list (`security-summary.ts`);
 * nothing here compares two severities.
 *
 * WHAT THIS COMPONENT MUST NEVER DO
 * =================================
 * 1. Present `Finding.line` as a file position. It is the index of an ADDED LINE --
 *    a finding at `app/auth.py:3` means the third added line, not line 3. So there
 *    is NO link, no "jump to line", and the text says "added line".
 * 2. Collapse the provenance values. It renders `PROVENANCE` from the vocabulary,
 *    where a fault and a choice already differ.
 * 3. Treat an absent verdict as a pass. `security: null` gets `VERDICT_ABSENT`.
 */

import { Mark } from "@/components/primitives";
import {
  findingRows,
  nativeWord,
  runComparison,
  scannerComparisons,
  thresholdOf,
  type Comparison,
  type FindingRow,
  type ScannerComparison,
} from "@/components/security-summary";
import { PROVENANCE, VERDICT, VERDICT_ABSENT } from "@/components/vocabulary";
import type { ScoringResponse } from "@/lib/endpoints";
import type { SecurityView, Severity } from "@/lib/contract";

export function SecurityPanel({
  security,
  scoring,
}: {
  security: SecurityView | null;
  scoring: ScoringResponse | null;
}) {
  // An absent verdict is its own state. NOT a pass.
  if (!security) {
    return (
      <section>
        <h2 className="title">Security</h2>
        <div style={{ marginTop: "var(--gap-4)" }}>
          <Mark mark={VERDICT_ABSENT} explain />
        </div>
      </section>
    );
  }

  const threshold = thresholdOf(security, scoring);
  const measured = security.scan_provenance === "scanners";

  return (
    <section>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--gap-3)",
          flexWrap: "wrap",
        }}
      >
        <h2 className="title">Security</h2>
        <Mark mark={VERDICT[security.verdict]} />
        <Mark mark={PROVENANCE[security.scan_provenance]} />
      </div>

      {/* The provenance meaning is visible, not tooltip-only, when it is anything
          other than a real scan -- those are the cases where a reader would
          otherwise draw a wrong conclusion. */}
      {!measured ? (
        <div style={{ marginTop: "var(--gap-4)" }}>
          <Mark mark={PROVENANCE[security.scan_provenance]} explain />
        </div>
      ) : null}

      <Verdict run={runComparison(security)} threshold={threshold.value} recorded={threshold.recorded} />
      <Scanners scanners={scannerComparisons(security)} threshold={threshold.value} measured={measured} />
      <Findings rows={findingRows(security)} threshold={threshold.value} measured={measured} />

      {security.explanation ? (
        <details style={{ borderTop: "1px solid var(--border)", paddingTop: "var(--gap-4)" }}>
          <summary
            style={{
              cursor: "pointer",
              fontFamily: "var(--mono)",
              fontSize: "var(--step-small)",
            }}
          >
            What the security agent wrote{" "}
            <span style={{ color: "var(--text-muted)" }}>
              · a model&apos;s prose — it describes the verdict, it did not decide it
            </span>
          </summary>
          <p className="prose" style={{ margin: "var(--gap-3) 0 0", color: "var(--text)" }}>
            {security.explanation}
          </p>
        </details>
      ) : null}
    </section>
  );
}

/** `critical ≥ high` -- or `low < high`, or nothing to compare. */
function Sum({
  worst,
  blocks,
  threshold,
}: {
  worst: Severity | null;
  blocks: boolean;
  threshold: Severity;
}) {
  if (worst === null) {
    return (
      <>
        <span style={{ color: "var(--text-muted)" }}>no findings</span>
        <span style={{ color: "var(--text-muted)" }}>·</span>
        <span style={{ color: "var(--text-muted)" }}>threshold</span>
        <span style={{ color: "var(--text)" }}>{threshold}</span>
      </>
    );
  }
  return (
    <>
      <span style={{ color: blocks ? "var(--refused)" : "var(--text)" }}>{worst}</span>
      <span style={{ color: "var(--text-muted)" }}>{blocks ? "≥" : "<"}</span>
      <span style={{ color: "var(--text)" }}>{threshold}</span>
    </>
  );
}

/** The run's comparison, for a pass as well as a block. */
function Verdict({
  run,
  threshold,
  recorded,
}: {
  run: Comparison;
  threshold: Severity;
  recorded: boolean;
}) {
  const why = run.blocks
    ? `The worst finding reached the threshold, so the run stopped.`
    : run.findings > 0
      ? `Every finding is below the threshold, so nothing blocked.`
      : `No scanner reported anything, so there was nothing to compare.`;

  return (
    <div style={{ margin: "var(--gap-6) 0" }}>
      <p className="eyebrow">{run.blocks ? "Why it stopped" : "Why it passed"}</p>
      <p
        className="display"
        style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: "0.4em" }}
      >
        <Sum worst={run.worst} blocks={run.blocks} threshold={threshold} />
      </p>
      <p className="prose" style={{ fontSize: "var(--step-small)", margin: "var(--gap-2) 0 0" }}>
        {why} A fixed rule decides this, not a model — the same findings give the same
        answer every time.
        {recorded
          ? null
          : ` This run recorded no threshold, because no finding wrote a scoring row; ${threshold} is the pipeline's default.`}
      </p>
    </div>
  );
}

/** The same comparison once per scanner -- all three, always. */
function Scanners({
  scanners,
  threshold,
  measured,
}: {
  scanners: ScannerComparison[];
  threshold: Severity;
  measured: boolean;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 13rem), 1fr))",
        gap: "var(--gap-4)",
        marginBottom: "var(--gap-6)",
      }}
    >
      {scanners.map((s) => {
        const outcome = s.faulted
          ? "scanner failed — blocks"
          : s.blocks
            ? "blocks"
            : measured
              ? "clear"
              : "not measured";
        return (
          <div
            key={s.tool}
            className="card"
            data-blocks={s.blocks}
            style={{
              padding: "var(--gap-3) var(--gap-4)",
              borderColor: s.blocks ? "var(--refused)" : undefined,
            }}
          >
            <p style={{ margin: 0, fontFamily: "var(--mono)", color: "var(--text)" }}>{s.tool}</p>
            <p style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}>
              {s.looksFor}
            </p>
            <p
              style={{
                margin: "var(--gap-3) 0 var(--gap-1)",
                fontFamily: "var(--mono)",
                fontSize: "var(--step-title)",
                display: "flex",
                gap: "0.4em",
                flexWrap: "wrap",
                alignItems: "baseline",
              }}
            >
              {s.worst === null ? (
                <span style={{ color: "var(--text-muted)" }}>
                  {measured ? "nothing found" : "not measured"}
                </span>
              ) : (
                <Sum worst={s.worst} blocks={s.blocks} threshold={threshold} />
              )}
            </p>
            <p style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}>
              {s.findings} {s.findings === 1 ? "finding" : "findings"} ·{" "}
              <span
                style={{
                  color: s.blocks
                    ? "var(--refused)"
                    : measured
                      ? "var(--shipped)"
                      : "var(--text-muted)",
                }}
              >
                {outcome}
              </span>
            </p>
          </div>
        );
      })}
    </div>
  );
}

/** Every finding, with its score -- one table where there were two. */
function Findings({
  rows,
  threshold,
  measured,
}: {
  rows: FindingRow[];
  threshold: Severity;
  measured: boolean;
}) {
  if (rows.length === 0) {
    return (
      <p className="prose" style={{ marginBottom: "var(--gap-6)" }}>
        {measured
          ? "The scanners reported no findings on this change."
          : "No findings are recorded for this change."}
      </p>
    );
  }

  return (
    <div className="table-scroll" style={{ marginBottom: "var(--gap-6)" }}>
      <table className="data">
        <caption>
          {rows.some((r) => r.blocks)
            ? "Every finding. The rows marked rose reached the threshold — those are the ones that stopped the run."
            : "Every finding. None reached the threshold."}
        </caption>
        <thead>
          <tr>
            <th scope="col">Tool</th>
            <th scope="col">Rule</th>
            {/* NOT "Line". The number counts ADDED lines, not lines in the file,
                and this is where a reader learns that before using it. */}
            <th scope="col">File · added line</th>
            <th scope="col">Scanner said</th>
            <th scope="col">We scored</th>
            <th scope="col">Against {threshold}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ finding: f, native, scored, blocks }, i) => (
            <tr key={`${f.tool}-${f.rule}-${f.file}-${f.line}-${i}`} data-blocking={blocks}>
              <td style={{ fontFamily: "var(--mono)" }}>{f.tool}</td>
              <td style={{ fontFamily: "var(--mono)" }} title={f.description}>
                {f.rule}
              </td>
              <td className="ident">
                {f.file}
                <span style={{ color: "var(--text-muted)" }}> · {f.line}</span>
              </td>
              <td style={{ fontFamily: "var(--mono)", color: "var(--text-muted)" }}>
                {nativeWord(native)}
              </td>
              <td
                style={{
                  fontFamily: "var(--mono)",
                  color: blocks ? "var(--refused)" : "var(--text)",
                }}
              >
                {scored}
              </td>
              <td
                style={{
                  fontFamily: "var(--mono)",
                  color: blocks ? "var(--refused)" : "var(--text-muted)",
                }}
              >
                {blocks ? "≥ blocks" : "< clear"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p
        style={{
          marginTop: "var(--gap-3)",
          // NOT --step-caption: this is the one sentence that stops a reader taking
          // `app/auth.py · 3` for a file position, and the smallest type in the app
          // is below the floor for a screen share.
          fontSize: "var(--step-small)",
          color: "var(--text-muted)",
          maxWidth: "var(--measure)",
        }}
      >
        The added line counts the lines the change adds, not lines in the file, so it
        is not a position you can jump to.
        {rows.some((r) => r.native === "")
          ? " “None — policy” means gitleaks reports no severity: any credential it finds is critical by rule."
          : null}
      </p>
    </div>
  );
}
