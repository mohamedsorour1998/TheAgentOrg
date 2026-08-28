/**
 * THE PRIMITIVES. A mark, an error, an empty state, a skeleton, a stat.
 *
 * Server components -- none holds state or an event handler, so none needs
 * `"use client"` and none ships JavaScript to the browser. A screen that only
 * displays a fetched run should send no script at all.
 *
 * WHY THESE FIVE AND NOT A COMPONENT LIBRARY
 * ==========================================
 * Every one exists because the same thing is stated on four screens and must be
 * stated identically. `Mark` is the visible half of `vocabulary.ts` -- the tone
 * and form tables there decide, this renders. Splitting the decision from the
 * rendering means a screen can only be wrong by importing the wrong table entry,
 * never by inventing a colour for a fault.
 */

import type { CSSProperties } from "react";

import type { Mark as MarkValue } from "@/components/vocabulary";

/** Semantic tone -> the palette custom property. THE ONE mapping. */
const TONE: Readonly<Record<MarkValue["tone"], string>> = {
  neutral: "var(--text)",
  accent: "var(--accent)",
  refused: "var(--refused)",
  shipped: "var(--shipped)",
  muted: "var(--text-muted)",
};

/**
 * A labelled state: a verdict, a status, a provenance.
 *
 * `form` is rendered as a real border style, so the four provenance values differ
 * in SHAPE and not only in hue -- `struck` also strikes the text, which reads as
 * "this is not an answer" without needing colour at all.
 *
 * The meaning travels in `title` AND, when `explain` is set, as visible text.
 * A tooltip alone is unreachable by touch and by keyboard, so anything a person
 * must know to avoid a wrong conclusion is never tooltip-only.
 */
export function Mark({
  mark,
  explain = false,
}: {
  mark: MarkValue;
  explain?: boolean;
}) {
  const colour = TONE[mark.tone];
  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.4em",
    padding: "0.15em 0.5em",
    border: `1px ${mark.form === "dashed" ? "dashed" : "solid"} ${colour}`,
    borderRadius: "3px",
    color: colour,
    fontFamily: "var(--mono)",
    fontSize: "var(--step-caption)",
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    textDecoration: mark.form === "struck" ? "line-through" : "none",
    whiteSpace: "nowrap",
  };

  if (!explain) {
    return (
      <span style={style} title={mark.meaning}>
        {mark.label}
      </span>
    );
  }

  return (
    <span
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--gap-2)",
        alignItems: "flex-start",
      }}
    >
      <span style={style}>{mark.label}</span>
      <span
        style={{
          color: "var(--text-muted)",
          fontSize: "var(--step-small)",
          maxWidth: "var(--measure)",
        }}
      >
        {mark.meaning}
      </span>
    </span>
  );
}

/**
 * AN ERROR SAYS WHAT HAPPENED AND HOW TO FIX IT.
 *
 * Every route answers `ApiError { error, detail? }`, so this is the one error
 * component in the app. Three rules it enforces by shape:
 *
 * 1. `error` is a sentence about what happened. It is NOT prefixed with
 *    "Error:" -- the heading already says so, and the doubling reads as panic.
 * 2. `fix` is what the reader does next. Required, not optional: an error with
 *    no recourse leaves a person re-clicking the same button. Where genuinely
 *    nothing can be done, the honest text is "this needs an operator", which is
 *    still direction.
 * 3. `detail` is machine context and is rendered mono, smaller, last. It is the
 *    thing pasted into a bug report, so it must be selectable and never
 *    truncated with an ellipsis.
 *
 * No apology and no exclamation mark. The interface states the fact.
 */
export function ErrorState({
  error,
  fix,
  detail,
}: {
  error: string;
  fix: string;
  detail?: string;
}) {
  return (
    <div
      role="alert"
      style={{
        border: "1px solid var(--refused)",
        borderLeftWidth: "3px",
        borderRadius: "4px",
        padding: "var(--gap-4) var(--gap-6)",
        background: "var(--surface-raised)",
        maxWidth: "var(--measure)",
      }}
    >
      <p className="eyebrow" style={{ color: "var(--refused)" }}>
        Did not complete
      </p>
      <p style={{ margin: `0 0 var(--gap-3)`, fontSize: "var(--step-body)" }}>
        {error}
      </p>
      <p
        style={{
          margin: 0,
          color: "var(--text-muted)",
          fontSize: "var(--step-small)",
        }}
      >
        {fix}
      </p>
      {detail ? (
        <pre
          style={{
            margin: "var(--gap-4) 0 0",
            padding: "var(--gap-2) var(--gap-3)",
            background: "var(--surface-sunken)",
            border: "1px solid var(--border)",
            borderRadius: "3px",
            color: "var(--text-muted)",
            fontSize: "var(--step-caption)",
            overflowX: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {detail}
        </pre>
      ) : null}
    </div>
  );
}

/**
 * AN EMPTY SCREEN IS AN INVITATION TO ACT, so `action` is required.
 *
 * Deliberately not the same component as `ErrorState`: nothing has gone wrong
 * here, and giving emptiness a red border would teach a person that a new
 * account is a fault.
 */
export function EmptyState({
  headline,
  action,
  children,
}: {
  headline: string;
  action: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px dashed var(--border-strong)",
        borderRadius: "4px",
        padding: "var(--gap-8) var(--gap-6)",
        maxWidth: "var(--measure)",
      }}
    >
      <p className="title" style={{ marginBottom: "var(--gap-3)" }}>
        {headline}
      </p>
      <p
        style={{
          margin: `0 0 var(--gap-4)`,
          color: "var(--text-muted)",
          fontSize: "var(--step-body)",
        }}
      >
        {action}
      </p>
      {children}
    </div>
  );
}

/**
 * A loading placeholder.
 *
 * `aria-busy` and the visually hidden label are what make this reachable: a
 * screen reader announces "Loading runs" rather than silence. The pulse is CSS
 * animation, so `globals.css`'s reduced-motion block already stills it -- which
 * is why no component here checks the media query itself.
 */
export function Skeleton({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div aria-busy="true" aria-live="polite">
      <span
        style={{
          position: "absolute",
          width: "1px",
          height: "1px",
          overflow: "hidden",
          clip: "rect(0 0 0 0)",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          style={{
            height: "1.25rem",
            marginBottom: "var(--gap-3)",
            // Descending widths: uniform bars read as a table, which promises a
            // shape the real content may not have.
            width: `${92 - i * 14}%`,
            background: "var(--surface-raised)",
            borderRadius: "3px",
            animation: "pulse 1.6s ease-in-out infinite",
          }}
        />
      ))}
    </div>
  );
}

/**
 * One measured figure with its label BELOW it, not above.
 *
 * The figure is what a person came for and the label is how they check they read
 * the right one, so the figure leads. `unit` is separated so `$0.0085` and
 * `2 findings` do not need two components.
 */
export function Stat({
  value,
  label,
  tone = "neutral",
}: {
  value: string;
  label: string;
  tone?: MarkValue["tone"];
}) {
  return (
    <div>
      <p
        className="display"
        style={{ color: TONE[tone], fontSize: "var(--step-title)" }}
      >
        {value}
      </p>
      <p className="eyebrow" style={{ margin: "var(--gap-1) 0 0" }}>
        {label}
      </p>
    </div>
  );
}
