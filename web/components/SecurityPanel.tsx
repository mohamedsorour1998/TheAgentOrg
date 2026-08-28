/**
 * THE VERDICT, AND THE ARITHMETIC BEHIND IT.
 *
 * THE ONE RISK THIS DESIGN TAKES: when a run blocks, the page's hero is not a
 * banner saying "blocked" -- it is the comparison that produced the block,
 * `critical >= high`, at display size, with the verdict beneath it.
 *
 * The justification is the product's whole thesis. A block here is not an
 * opinion: no model is involved, it is three scanners plus five lines of Python
 * comparing a finding's severity against a threshold. A judge's first question is
 * "how do I know this is deterministic?", and the honest answer is the
 * arithmetic. Rendering the sum rather than captioning it means the claim is on
 * screen instead of in a sentence beside it.
 *
 * WHAT THIS COMPONENT MUST NEVER DO
 * =================================
 * 1. Present `Finding.line` as a file position. It is the index of an ADDED LINE
 *    -- a finding at `app/auth.py:3` means the third added line, not line 3. So
 *    there is NO link, no "jump to line", and the column is labelled as what it
 *    is. Building navigation on it would send a reader to the wrong place with
 *    total confidence.
 * 2. Collapse the provenance values. It renders `PROVENANCE` from the vocabulary,
 *    where a fault and a choice already differ.
 * 3. Treat an absent verdict as a pass. `security: null` gets `VERDICT_ABSENT`.
 */

import { Mark, Stat } from "@/components/primitives";
import { PROVENANCE, VERDICT, VERDICT_ABSENT } from "@/components/vocabulary";
import type { ScoringResponse } from "@/lib/endpoints";
import type { SecurityView } from "@/lib/contract";

/**
 * THE ARITHMETIC. Rendered only for a block, because only a block has a sum worth
 * showing: a pass is the absence of any finding reaching the threshold, and
 * `nothing >= high` is not an illuminating equation.
 */
function Arithmetic({ worst, threshold }: { worst: string; threshold: string }) {
  return (
    <div>
      <p className="eyebrow">Why it stopped</p>
      <p
        className="display"
        style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: "0.4em" }}
      >
        <span style={{ color: "var(--refused)" }}>{worst}</span>
        <span style={{ color: "var(--text-muted)" }}>&ge;</span>
        <span style={{ color: "var(--text)" }}>{threshold}</span>
      </p>
      <p className="prose" style={{ fontSize: "var(--step-small)", marginTop: "var(--gap-2)" }}>
        A finding reached the blocking threshold, so the run stopped. No model took
        part in this decision — it is a severity compared against a threshold, and
        it gives the same answer every time.
      </p>
    </div>
  );
}

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

  const blocked = security.verdict === "block";
  const threshold = scoring?.threshold ?? "high";
  // The worst severity among the blocking findings, by the shipped ranking. Read
  // off the findings rather than assumed: `blocking` is the list the rule
  // returned, so its own severities are the ones that produced the verdict.
  const ORDER = ["low", "medium", "high", "critical"] as const;
  const worst =
    security.blocking.length > 0
      ? security.blocking.reduce(
          (acc, f) => (ORDER.indexOf(f.severity) > ORDER.indexOf(acc) ? f.severity : acc),
          security.blocking[0]!.severity,
        )
      : null;

  return (
    <section>
      <h2 className="title">Security</h2>

      {blocked && worst ? (
        <div style={{ margin: "var(--gap-6) 0" }}>
          <Arithmetic worst={worst} threshold={threshold} />
        </div>
      ) : null}

      <div className="grid-stats" style={{ margin: "var(--gap-6) 0" }}>
        <div>
          <p className="eyebrow">Verdict</p>
          <Mark mark={VERDICT[security.verdict]} />
        </div>
        <Stat
          value={String(security.blocking.length)}
          label="At or above threshold"
          tone={security.blocking.length > 0 ? "refused" : "shipped"}
        />
        <Stat value={String(security.findings.length)} label="Findings in total" />
        <div>
          <p className="eyebrow">Provenance</p>
          <Mark mark={PROVENANCE[security.scan_provenance]} />
        </div>
      </div>

      {/* The provenance meaning is visible, not tooltip-only, when it is anything
          other than a real scan -- those are the cases where a reader would
          otherwise draw a wrong conclusion. */}
      {security.scan_provenance !== "scanners" ? (
        <div style={{ marginBottom: "var(--gap-6)" }}>
          <Mark mark={PROVENANCE[security.scan_provenance]} explain />
        </div>
      ) : null}

      {security.explanation ? (
        <div className="card" style={{ marginBottom: "var(--gap-6)" }}>
          <p className="eyebrow">What the security agent wrote</p>
          <p className="prose" style={{ margin: 0, color: "var(--text)" }}>
            {security.explanation}
          </p>
          <p
            style={{
              margin: "var(--gap-3) 0 0",
              fontSize: "var(--step-caption)",
              color: "var(--text-muted)",
            }}
          >
            Prose from a model. It describes the verdict; it did not decide it.
          </p>
        </div>
      ) : null}

      <FindingsTable security={security} />
      <ScoringTable scoring={scoring} />
    </section>
  );
}

/** Every finding, with the line column labelled honestly. */
function FindingsTable({ security }: { security: SecurityView }) {
  if (security.findings.length === 0) {
    return (
      <p className="prose" style={{ marginBottom: "var(--gap-8)" }}>
        The scanners returned no findings on this change.
      </p>
    );
  }

  const blockingKeys = new Set(
    security.blocking.map((f) => `${f.tool}|${f.rule}|${f.file}|${f.line}`),
  );

  return (
    <div className="table-scroll" style={{ marginBottom: "var(--gap-8)" }}>
      <table className="data">
        <caption>
          Every finding on this change. A rose mark on the left is a finding at or
          above the threshold — those are the ones that stopped the run.
        </caption>
        <thead>
          <tr>
            <th scope="col">Tool</th>
            <th scope="col">Rule</th>
            <th scope="col">Severity</th>
            <th scope="col">File</th>
            {/* NOT "Line". The number is the index of an added line, not a
                position in the file, and the header is the only place a reader
                learns that before drawing a conclusion from it. */}
            <th scope="col">Added line #</th>
            <th scope="col">Description</th>
          </tr>
        </thead>
        <tbody>
          {security.findings.map((f, i) => {
            const isBlocking = blockingKeys.has(
              `${f.tool}|${f.rule}|${f.file}|${f.line}`,
            );
            return (
              <tr key={`${f.tool}-${f.rule}-${f.file}-${f.line}-${i}`} data-blocking={isBlocking}>
                <td style={{ fontFamily: "var(--mono)" }}>{f.tool}</td>
                <td style={{ fontFamily: "var(--mono)" }}>{f.rule}</td>
                <td
                  style={{
                    fontFamily: "var(--mono)",
                    color: isBlocking ? "var(--refused)" : "var(--text-muted)",
                  }}
                >
                  {f.severity}
                </td>
                <td className="ident">{f.file}</td>
                <td style={{ fontFamily: "var(--mono)", textAlign: "right" }}>{f.line}</td>
                <td style={{ color: "var(--text-muted)", minWidth: "16rem" }}>
                  {f.description}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p
        style={{
          marginTop: "var(--gap-3)",
          fontSize: "var(--step-caption)",
          color: "var(--text-muted)",
          maxWidth: "var(--measure)",
        }}
      >
        The line number counts added lines in the diff, not lines in the file, so
        it is not a position you can navigate to. Read the file and the rule
        instead.
      </p>
    </div>
  );
}

/**
 * J7 — THE SCORING TABLE. The arithmetic per finding, not per run.
 *
 * `native` is the scanner's own word and `""` means the scanner emits no severity
 * at all (gitleaks emits none — a secret scanner's finding is assigned
 * `critical` by policy, because a committed credential has no lesser grade). So
 * an empty `native` is DATA about that scanner, not a gap, and it is rendered as
 * a stated absence rather than left blank.
 *
 * `threshold` is echoed at the top level as well as per row, deliberately: a run
 * with no findings has no rows, and the threshold that produced that empty table
 * is still a fact — otherwise a clean run and an unscanned one show the same
 * blank.
 */
function ScoringTable({ scoring }: { scoring: ScoringResponse | null }) {
  if (!scoring) return null;

  return (
    <div className="table-scroll">
      <h3 className="title" style={{ fontSize: "var(--step-body)" }}>
        How each finding was scored
      </h3>
      <p className="prose" style={{ fontSize: "var(--step-small)" }}>
        Threshold in force: <code>{scoring.threshold}</code>. Every row compares one
        finding against it. The blocking column is not computed here — it is the
        same rule the pipeline&apos;s verdict came from, asked once per finding.
      </p>

      {scoring.rows.length === 0 ? (
        <p className="prose" style={{ fontSize: "var(--step-small)" }}>
          No findings to score. The threshold above is still the one that was
          applied.
        </p>
      ) : (
        <table className="data">
          <caption>
            The scanner&apos;s own severity, ours, and whether the row blocked.
          </caption>
          <thead>
            <tr>
              <th scope="col">Tool</th>
              <th scope="col">Rule</th>
              <th scope="col">Scanner said</th>
              <th scope="col">We scored</th>
              <th scope="col">Threshold</th>
              <th scope="col">Blocked</th>
            </tr>
          </thead>
          <tbody>
            {scoring.rows.map((r, i) => (
              <tr key={`${r.tool}-${r.rule}-${i}`} data-blocking={r.blocking}>
                <td style={{ fontFamily: "var(--mono)" }}>{r.tool}</td>
                <td style={{ fontFamily: "var(--mono)" }}>{r.rule}</td>
                <td style={{ fontFamily: "var(--mono)", color: "var(--text-muted)" }}>
                  {r.native === "" ? "reports no severity" : r.native}
                </td>
                <td style={{ fontFamily: "var(--mono)" }}>{r.mapped}</td>
                <td style={{ fontFamily: "var(--mono)", color: "var(--text-muted)" }}>
                  {r.threshold}
                </td>
                <td
                  style={{
                    fontFamily: "var(--mono)",
                    color: r.blocking ? "var(--refused)" : "var(--text-muted)",
                  }}
                >
                  {r.blocking ? "yes" : "no"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div style={{ marginTop: "var(--gap-3)" }}>
        <Mark mark={PROVENANCE[scoring.scan_provenance]} />
      </div>
    </div>
  );
}
