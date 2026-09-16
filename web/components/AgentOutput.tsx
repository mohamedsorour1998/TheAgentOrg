/**
 * WHAT THE AGENTS SAID — the half of the product that was computed and never shown.
 *
 * Reported from the deployed app: *"i dont understand how it is running and we have
 * no info"*. The run screen carried a status, a stage spine and a security verdict,
 * so five agents' work rendered as five words. Everything below was already on the
 * index row — `run_index` denormalises the whole state document — so this is a
 * projection of data the screen was holding and not displaying.
 *
 * **`null` AND AN EMPTY RESULT ARE DIFFERENT FACTS AND ARE DRAWN DIFFERENTLY.** A
 * stage that has not run says so in words; a stage that ran and produced an empty
 * list says *that*. This is `scan_provenance`'s rule on a screen: rendering "no
 * objections" for a reviewer that never ran is the did-not-run-versus-passed
 * conflation this repository exists to refuse, and it is the more dangerous
 * direction because it reads as good news.
 *
 * **THE REVIEWER IS ADVISORY AND THE SCANNERS ARE BINDING, and the screen says which
 * is which.** `graph.py` loops on a non-approve verdict, it does not stop; only
 * `compute_security_verdict` ends a run. A judge reading `changes_requested` beside
 * a merged change should not have to ask why it merged — so the reviewer's panel
 * names its own authority rather than leaving the reader to infer it from a colour.
 *
 * **THE SRE'S VERDICT IS NOT THE MODEL'S.** `agents/sre.py` measures CI on the
 * runner and derives `verdict` in code (`"no_go" if ci == "failing" else "go"`); the
 * model contributes `slo_checks` and `notes` only, validated against `SREAdvice`,
 * which cannot even express a verdict. The measured row is therefore separated from
 * the advisory ones, because a model check named "CI" must not read as the real one.
 *
 * **NO JUMP-TO-LINE, ANYWHERE.** A reviewer comment's `line` is a position in the
 * diff the agent was shown, and `Finding.line` is the index of an ADDED line rather
 * than a file position. `SecurityPanel` already refuses that affordance for the same
 * reason; offering it here would reintroduce it one panel over.
 */

"use client";

import { useState } from "react";

import type { DevView, PlanView, ReviewView, SREView } from "@/lib/contract";

/** A stage that has not run, said in words rather than drawn as emptiness. */
function NotRun({ what }: { what: string }) {
  return (
    <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}>
      The {what} has not run. Nothing is recorded for it — this is not a result.
    </p>
  );
}

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="card" style={{ marginBottom: "var(--gap-6)" }}>
      <h3 className="eyebrow" style={{ margin: 0, marginBottom: note ? "var(--gap-1)" : "var(--gap-3)" }}>
        {title}
      </h3>
      {note ? (
        <p
          className="prose"
          style={{
            margin: 0,
            marginBottom: "var(--gap-3)",
            fontSize: "var(--step-caption)",
            color: "var(--text-muted)",
          }}
        >
          {note}
        </p>
      ) : null}
      {children}
    </section>
  );
}

/** A list, or a sentence saying the list came back empty. Never a blank. */
function Listed({ items, empty }: { items: string[] | undefined; empty: string }) {
  if (!items || items.length === 0) {
    return (
      <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}>
        {empty}
      </p>
    );
  }
  return (
    <ul className="prose" style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "var(--step-small)" }}>
      {items.map((item, i) => (
        <li key={i} style={{ marginBottom: "var(--gap-1)" }}>
          {item}
        </li>
      ))}
    </ul>
  );
}

export function AgentOutput({
  plan,
  dev,
  review,
  sre,
}: {
  plan: PlanView | null;
  dev: DevView | null;
  review: ReviewView | null;
  sre: SREView | null;
}) {
  // The diff is collapsed by DEFAULT. It is the longest thing on the page by an
  // order of magnitude, and a reader scrolling past hundreds of lines to reach the
  // security verdict is how the verdict stops being read.
  const [showDiff, setShowDiff] = useState(false);

  const approved = review?.verdict === "approve";

  return (
    <div>
      <h2 className="title" style={{ marginBottom: "var(--gap-4)" }}>
        What the agents said
      </h2>

      <Panel title="Planner" note="Reads the ticket. Chooses the files every later stage works from.">
        {plan === null ? (
          <NotRun what="planner" />
        ) : (
          <div style={{ display: "grid", gap: "var(--gap-4)" }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: "var(--gap-1)" }}>Tasks</div>
              <Listed items={plan.tasks} empty="The planner returned no tasks." />
            </div>
            <div>
              <div className="eyebrow" style={{ marginBottom: "var(--gap-1)" }}>
                Acceptance criteria
              </div>
              <Listed items={plan.acceptance_criteria} empty="No acceptance criteria were produced." />
            </div>
            {plan.target_files && plan.target_files.length > 0 ? (
              <div>
                <div className="eyebrow" style={{ marginBottom: "var(--gap-1)" }}>Target files</div>
                <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: "var(--step-small)" }}>
                  {plan.target_files.join("  ·  ")}
                </p>
              </div>
            ) : null}
            {plan.notes ? (
              <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)" }}>{plan.notes}</p>
            ) : null}
          </div>
        )}
      </Panel>

      <Panel title="Developer" note="Writes the change. Its diff is what the scanners and the reviewer read.">
        {dev === null ? (
          <NotRun what="developer" />
        ) : (
          <div style={{ display: "grid", gap: "var(--gap-3)" }}>
            {dev.summary ? (
              <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)" }}>{dev.summary}</p>
            ) : null}
            {dev.files_changed && dev.files_changed.length > 0 ? (
              <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: "var(--step-small)" }}>
                {dev.files_changed.join("  ·  ")}
              </p>
            ) : null}
            {dev.diff ? (
              <div>
                <button
                  type="button"
                  className="btn"
                  onClick={() => setShowDiff((v) => !v)}
                  aria-expanded={showDiff}
                >
                  {showDiff ? "Hide the diff" : `Show the diff (${dev.diff.split("\n").length} lines)`}
                </button>
                {showDiff ? (
                  // `overflow-x: auto` on its OWN container: a diff is the one thing
                  // on this page allowed to be wider than the screen, and letting it
                  // widen the page body instead is what breaks the phone layout.
                  <pre
                    style={{
                      marginTop: "var(--gap-3)",
                      marginBottom: 0,
                      overflowX: "auto",
                      maxHeight: "28rem",
                      overflowY: "auto",
                      padding: "var(--gap-3)",
                      background: "var(--surface-sunken)",
                      border: "1px solid var(--border)",
                      fontFamily: "var(--mono)",
                      fontSize: "var(--step-caption)",
                      lineHeight: 1.5,
                    }}
                  >
                    {dev.diff}
                  </pre>
                ) : null}
              </div>
            ) : (
              <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}>
                The developer recorded no diff.
              </p>
            )}
          </div>
        )}
      </Panel>

      <Panel
        title="Reviewer"
        note="ADVISORY. A refusal here sends the change back for another pass; it does not stop the run. Only the security verdict does that."
      >
        {review === null ? (
          <NotRun what="reviewer" />
        ) : (
          <div style={{ display: "grid", gap: "var(--gap-3)" }}>
            <p style={{ margin: 0, fontSize: "var(--step-small)" }}>
              <span
                style={{
                  fontWeight: 600,
                  color: approved ? "var(--shipped)" : "var(--refused)",
                  // FORM AS WELL AS COLOUR, so the distinction survives greyscale and
                  // a dim projector -- `vocabulary.ts`'s rule.
                  borderBottom: approved ? "none" : "2px solid var(--refused)",
                }}
              >
                {approved ? "APPROVED" : "CHANGES REQUESTED"}
              </span>
            </p>
            <div>
              <div className="eyebrow" style={{ marginBottom: "var(--gap-1)" }}>Must fix</div>
              <Listed
                items={review.must_fix}
                empty="The reviewer listed nothing that must be fixed."
              />
            </div>
            {review.comments && review.comments.length > 0 ? (
              <div>
                <div className="eyebrow" style={{ marginBottom: "var(--gap-1)" }}>Comments</div>
                <ul className="prose" style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "var(--step-small)" }}>
                  {review.comments.map((c, i) => (
                    <li key={i} style={{ marginBottom: "var(--gap-1)" }}>
                      {c.file ? (
                        <span style={{ fontFamily: "var(--mono)", color: "var(--text-muted)" }}>
                          {c.file}
                          {typeof c.line === "number" ? `:${c.line}` : ""}{" "}
                        </span>
                      ) : null}
                      {c.comment}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </Panel>

      <Panel
        title="SRE"
        note="The verdict is derived from MEASURED CI, not from the model. The model contributes the advisory checks below it and cannot set a verdict."
      >
        {sre === null ? (
          <NotRun what="SRE stage" />
        ) : (
          <div style={{ display: "grid", gap: "var(--gap-3)" }}>
            <p style={{ margin: 0, fontSize: "var(--step-small)" }}>
              <span style={{ fontWeight: 600, color: sre.verdict === "no_go" ? "var(--refused)" : "var(--shipped)" }}>
                {sre.verdict === "no_go" ? "NO GO" : "GO"}
              </span>{" "}
              <span style={{ color: "var(--text-muted)" }}>
                — CI {sre.ci_status || "unknown"}
                {sre.ci_status === "unknown"
                  ? " (nothing has run on this commit, which is not the same as passing)"
                  : ""}
              </span>
            </p>
            {sre.slo_checks && sre.slo_checks.length > 0 ? (
              <ul className="prose" style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "var(--step-small)" }}>
                {sre.slo_checks.map((c, i) => (
                  <li key={i} style={{ marginBottom: "var(--gap-1)" }}>
                    <strong style={{ fontWeight: 500 }}>{c.name}</strong>
                    {c.status ? ` — ${c.status}` : ""}
                    {c.detail ? `: ${c.detail}` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
            {sre.notes ? (
              <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)" }}>{sre.notes}</p>
            ) : null}
          </div>
        )}
      </Panel>
    </div>
  );
}
