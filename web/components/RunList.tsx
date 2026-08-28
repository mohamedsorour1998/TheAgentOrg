/**
 * THE RUN HISTORY TABLE. `GET /api/runs` -> one row per run.
 *
 * A CLIENT COMPONENT because it owns three states -- loading, refused, empty --
 * and a server component can only render the third. The fetch runs in the
 * browser through `getJson`, which is why every failure arrives as a value with
 * a `fix` attached rather than as a thrown error nobody catches.
 *
 * FOUR DISTINCTIONS THIS TABLE MUST NOT COLLAPSE, all four decided by
 * `vocabulary.ts` and none of them re-decided here:
 *
 *   verdict: null      NOT "pass". Security has not run. -> VERDICT_ABSENT
 *   scan_provenance "" NOT a scan. Nobody recorded it.   -> PROVENANCE[""]
 *   status "blocked"   NOT "failed". The rule WORKING.    -> RUN_STATUS
 *   blocking: null     NOT 0. `0` on a scanned run is a real zero.
 *
 * The first three are rendered by handing a table entry to `<Mark>`, so this
 * file cannot invent a label or a colour for any of them. The fourth is the one
 * with no table, so it is spelled out below: `null` becomes a word, a number
 * becomes a figure, and the two never share a shape.
 *
 * ORDERING: runs waiting for a person come FIRST, and the caption says so. A
 * paused run is the only row on this screen that is asking for something, and
 * burying it under thirty finished runs makes the list a log rather than a
 * queue. Within each group the API's newest-first order is preserved --
 * `Array.prototype.sort` is stable, so a partition is all this is.
 */

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { getJson, type Result } from "@/components/fetching";
import {
  EmptyState,
  ErrorState,
  Mark,
  Skeleton,
  Stat,
} from "@/components/primitives";
import {
  PROVENANCE,
  RUN_STATUS,
  VERDICT,
  VERDICT_ABSENT,
} from "@/components/vocabulary";
import type { RunSummary } from "@/lib/contract";
import type { RunListResponse } from "@/lib/endpoints";

/** Waiting for a human right now. `""` means the run is not paused. */
function isAwaiting(run: RunSummary): boolean {
  return run.awaiting_gate !== "";
}

/**
 * An ISO-8601 instant, readably.
 *
 * An unparseable value returns the RAW STRING rather than "Invalid Date": the
 * raw value is what somebody can act on, and `Invalid Date` names the browser's
 * problem instead of the data's. The machine-readable form stays in `<time
 * dateTime>` beside it, so the cell is still sortable by anything reading the
 * markup.
 */
function formatWhen(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * `41` reads as `#41`, `POISON-1` reads as itself.
 *
 * `[0-9]` and not `\d`, mirroring `github_ops._ISSUE_REF`: `\d` is
 * Unicode-aware, so it matches Arabic-Indic digits that are not an issue number.
 * Only a label here, but one spelling of this test is cheaper than two.
 */
function ticketLabel(id: string): string {
  return /^[0-9]+$/.test(id) ? `#${id}` : id;
}

export function RunList() {
  // `null` IS the loading state. A separate boolean would be a second
  // declaration of the same fact, free to disagree with this one.
  const [result, setResult] = useState<Result<RunListResponse> | null>(null);
  const [attempt, setAttempt] = useState(0);

  /**
   * NOTHING IS SET SYNCHRONOUSLY IN HERE, and that is a lint rule rather than a
   * preference: `react-hooks/set-state-in-effect` refuses a `setState` in an
   * effect body, so the reset-to-loading cannot live here. It lives in the retry
   * handler, which is an event and not an effect. Measured -- the first version
   * called a `load()` that began with `setResult(null)` and `npm run lint`
   * failed on this line.
   *
   * `cancelled` is not defensive noise: two retries in flight resolve in
   * whichever order the network chooses, and without this the older answer can
   * land last and replace the newer one.
   */
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const answer = await getJson<RunListResponse>("/api/runs");
      if (!cancelled) setResult(answer);
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setResult(null);
    setAttempt((n) => n + 1);
  }, []);

  if (result === null) return <Skeleton label="Loading runs" rows={5} />;

  if (!result.ok) {
    return (
      <div>
        <ErrorState
          error={result.error}
          fix={result.fix}
          detail={result.detail}
        />
        <button
          type="button"
          className="btn"
          onClick={retry}
          style={{ marginTop: "var(--gap-4)" }}
        >
          Try again
        </button>
      </div>
    );
  }

  const runs = result.value.runs;

  if (runs.length === 0) {
    return (
      <EmptyState
        headline="No runs yet"
        action={
          "Opening an issue on a repository in this tenant's scope starts one. " +
          "The webhook dispatches the pipeline and the run appears here — " +
          "nothing needs to be typed."
        }
      />
    );
  }

  const awaiting = runs.filter(isAwaiting);
  // `verdict === "block"` and not a count: the verdict IS the decision, and a
  // count is only its consequence.
  const blocked = runs.filter((r) => r.verdict === "block");
  const ordered = [...runs].sort(
    (a, b) => Number(isAwaiting(b)) - Number(isAwaiting(a)),
  );

  return (
    <div>
      <div className="grid-stats" style={{ marginBottom: "var(--gap-8)" }}>
        <Stat value={String(runs.length)} label="Runs" />
        {/* Cyan, because globals.css reserves it for structural marks and this
            is the number a person acts on. */}
        <Stat
          value={String(awaiting.length)}
          label="Waiting for a decision"
          tone="accent"
        />
        {/* Tone READ OFF the table rather than chosen, so this figure cannot
            drift from how the word "Blocked" is painted in the rows below. */}
        <Stat
          value={String(blocked.length)}
          label="Blocked by the rule"
          tone={RUN_STATUS.blocked.tone}
        />
      </div>

      <div className="table-scroll">
        <table className="data">
          <caption>
            Every run in this tenant, newest first, with the runs paused at a
            gate lifted to the top. A rose mark on the left edge is a change the
            deterministic security rule refused.
          </caption>
          <thead>
            <tr>
              <th scope="col">Ticket</th>
              <th scope="col">Waiting for</th>
              <th scope="col">Run</th>
              <th scope="col">Security</th>
              <th scope="col">Blocking</th>
              <th scope="col">Scan</th>
              <th scope="col">Started</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((run) => (
              <tr key={run.run_id} data-blocking={run.verdict === "block"}>
                <td>
                  <Link
                    href={`/runs/${run.run_id}`}
                    className="ident"
                    aria-label={`Ticket ${run.ticket_id} — open this run`}
                  >
                    {ticketLabel(run.ticket_id)}
                  </Link>
                  {/* The uuid in full. Truncated by the column, never by the
                      DOM, so a copy takes the whole thing. */}
                  <span
                    className="ident"
                    style={{
                      display: "block",
                      color: "var(--text-muted)",
                      fontSize: "var(--step-caption)",
                      marginTop: "var(--gap-1)",
                      maxWidth: "22ch",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {run.run_id}
                  </span>
                </td>
                <td>
                  {run.awaiting_gate === "" ? (
                    <span style={{ color: "var(--text-muted)" }}>
                      Not paused
                    </span>
                  ) : (
                    <span style={{ color: "var(--accent)" }}>
                      A decision
                      <span
                        className="ident"
                        style={{
                          display: "block",
                          color: "var(--accent)",
                          fontSize: "var(--step-caption)",
                        }}
                      >
                        {run.awaiting_gate}
                      </span>
                    </span>
                  )}
                </td>
                <td>
                  <Mark mark={RUN_STATUS[run.status]} />
                </td>
                <td>
                  <Mark
                    mark={
                      run.verdict === null ? VERDICT_ABSENT : VERDICT[run.verdict]
                    }
                  />
                </td>
                <td>
                  {run.blocking === null ? (
                    // A WORD, never `0`. `blocking: 0` on a scanned run is a
                    // real zero and must not share a shape with "not scanned".
                    <span style={{ color: "var(--text-muted)" }}>
                      not scanned
                    </span>
                  ) : (
                    <span className="ident">{run.blocking}</span>
                  )}
                </td>
                <td>
                  <Mark mark={PROVENANCE[run.scan_provenance]} />
                </td>
                <td>
                  <time
                    dateTime={run.created_at}
                    style={{ color: "var(--text-muted)" }}
                  >
                    {formatWhen(run.created_at)}
                  </time>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
