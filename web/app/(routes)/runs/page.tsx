/**
 * `/runs` — THE RUN HISTORY. The app's landing screen.
 *
 * The `(routes)` group does not appear in the URL, so this file serves `/runs`,
 * which is `NAV[0]` in `components/Shell.tsx`.
 *
 * A SERVER COMPONENT that renders a client one. The heading and the standing
 * explanation are static text and ship no JavaScript; only `<RunList>` needs the
 * browser, because only it fetches and holds state. Splitting them this way is
 * also what lets the page's prose render immediately while the table is still a
 * skeleton -- a screen whose heading appears with its content is one a person can
 * orient in before the data lands.
 *
 * WHY THE PROSE UNDER THE HEADING IS NOT DECORATION. Two of this table's columns
 * mean the opposite of what a reader will assume on a first pass: an empty
 * security cell is "not scanned" rather than "clear", and `Blocked` is the
 * pipeline working rather than a crash. `<Mark>` carries each meaning in a
 * `title`, and a tooltip is unreachable by touch and by keyboard -- so the two
 * that would produce a WRONG CONCLUSION are also stated here, in the open, once.
 */

import { RunList } from "@/components/RunList";

export const metadata = {
  title: "Runs · The Agent Org",
  description:
    "Every run in this tenant, with its security verdict, how many findings " +
    "blocked it, and whether real scanners produced that verdict.",
};

export default function RunsPage() {
  return (
    <div>
      <p className="eyebrow">Run history</p>
      <h1 className="display" style={{ marginBottom: "var(--gap-4)" }}>
        Runs
      </h1>
      <p className="prose" style={{ margin: "0 0 var(--gap-8)" }}>
        Each row is one ticket walked through the pipeline. A run paused at a
        gate is waiting for a person, so those are lifted to the top.{" "}
        <strong style={{ color: "var(--text)", fontWeight: 500 }}>
          Blocked means the deterministic rule refused the change
        </strong>{" "}
        — that is the gate working, and it is not a crash. An empty security
        verdict means security has not run yet, which is not the same as a run
        that was cleared.
      </p>

      <RunList />
    </div>
  );
}
