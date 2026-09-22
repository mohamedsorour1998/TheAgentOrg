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
  renderWhen,
} from "@/components/vocabulary";
import type { RunSummary } from "@/lib/contract";
import type { RunListResponse } from "@/lib/endpoints";

/**
 * The event `StartRun` fires when a dispatch is accepted. One spelling, exported,
 * so the two components cannot disagree about the name -- a listener on a string
 * nobody emits is silent, which is the failure this whole mechanism exists to fix.
 */
export const RUN_STARTED = "agentorg:run-started";

/** A run whose status can no longer change, so there is nothing to poll for. */
const ENDED = new Set(["blocked", "rejected", "promoted", "failed"]);

/**
 * How long to keep polling after a run is started, before anything is live.
 *
 * THREE MINUTES, because the run does not exist yet: `workflow_dispatch` answers
 * 204 with no body and the id is minted by the `plan` job, so the row appears only
 * once `run_index.record_run` writes it -- about a minute on a good day, and the
 * queue can make it longer.
 */
const START_POLL_MS = 3 * 60 * 1000;

/**
 * Waiting for a human WHO CAN ACT. `""` means the run is not paused.
 *
 * **`ci_linked` IS PART OF THE QUESTION, NOT A DETAIL.** A run with no Actions run
 * has no Environment for this application to release, so `approveRun` refuses it --
 * counting it under "waiting for a decision" asks somebody for a decision they
 * cannot make, and lifts it above the runs that genuinely need one. Reported from
 * the deployed app, where a stalled row from two days earlier sat at the top of the
 * list and held the counter at 1.
 *
 * The row still SAYS it is paused; it is the count and the ordering that change.
 * Hiding the pause would be the opposite error.
 */
function isAwaiting(run: RunSummary): boolean {
  return run.awaiting_gate !== "" && run.ci_linked;
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

  /**
   * THE LIST DID NOT MOVE ON ITS OWN, AND STARTING A RUN LOOKED LIKE NOTHING.
   *
   * Reported from the deployed app: *"I started new run but nothing happened ...
   * then I refreshed to see it. This is not right, I should see it without
   * refreshing"*. Two separate causes, and both are here:
   *
   *   1. `StartRun` accepts an `onStarted` callback and `app/(routes)/runs/page.tsx`
   *      NEVER PASSED ONE. The two are siblings under a server component, so there
   *      was no shared state to reload through -- a prop that exists, is optional,
   *      and is wired by nobody. This repository's second named pattern, in a
   *      callback: correct code reached by nothing.
   *   2. Nothing polled. A run takes about a minute to appear at all, because the
   *      `plan` job has to start and write the index row before it exists.
   *
   * **THE SIGNAL IS A DOM EVENT, NOT LIFTED STATE.** Lifting would mean making the
   * runs page a client component, shipping its static explanation as JavaScript for
   * the sake of one callback. `StartRun` announces on `window` and this listens --
   * the two stay independent, and a page that renders only one of them still works.
   */
  // A DEADLINE, NOT A TIMESTAMP TO COMPARE DURING RENDER. `Date.now()` in a render
  // body is impure and `react-hooks/purity` refuses it -- correctly, since it makes
  // the same props render differently. It is computed in the EVENT and read in the
  // timer, both of which are allowed to see a clock.
  const [pollUntil, setPollUntil] = useState(0);
  /**
   * THE RUN THAT HAS BEEN ASKED FOR AND DOES NOT EXIST YET.
   *
   * **THE LIST WAS CORRECT AND THE SCREEN WAS SILENT.** Reported from the deployed
   * app: *"when I click Start run, nothing happens; after a minute I refresh and
   * see it running"*. Polling was already in place and working -- there was simply
   * nothing to poll FOR. `workflow_dispatch` answers 204 with no body, the run id
   * is minted by the `plan` job, and the row appears only once
   * `run_index.record_run` writes it, about a minute later. So the honest render of
   * those sixty seconds was an unchanged list, and an unchanged list after a click
   * is indistinguishable from a button that did nothing.
   *
   * **WHAT IS KNOWN AT THAT MOMENT IS THE ISSUE NUMBER, AND ONLY THAT.** So the row
   * below claims only that: an issue was opened and a dispatch was accepted. It is
   * NOT drawn as a run -- no status mark, no verdict, no security columns -- because
   * inventing a `RUNNING` badge for something with no record would be this
   * repository's signature defect on the first screen anybody sees.
   */
  const [pending, setPending] = useState<{ issue: string; until: number } | null>(null);

  useEffect(() => {
    const onStarted = (event: Event) => {
      const issue = String((event as CustomEvent<{ issue?: string }>).detail?.issue ?? "");
      const until = Date.now() + START_POLL_MS;
      setPollUntil(until);
      if (issue) setPending({ issue, until });
    };
    window.addEventListener(RUN_STARTED, onStarted);
    return () => window.removeEventListener(RUN_STARTED, onStarted);
  }, []);

  const runs = result?.ok ? result.value.runs : [];
  // A run that can still change. `promoted`/`blocked`/`rejected`/`failed` cannot.
  const anyLive = runs.some((r) => !ENDED.has(r.status));
  // ARRIVED. The placeholder is dropped the moment the real row exists, so the two
  // are never on screen together -- which would read as the duplicate this product
  // spent a commit eliminating.
  // **DERIVED, NOT CLEARED IN AN EFFECT.** `react-hooks/set-state-in-effect` refuses
  // a `setState` in an effect body -- correctly, and this file already records
  // paying for that once. The placeholder is not "removed" when the run arrives; it
  // simply stops being shown, which needs no second source of truth to go stale.
  const showPending =
    pending !== null && !runs.some((r) => r.ticket_id === pending.issue);

  useEffect(() => {
    if (!anyLive && pollUntil === 0) return;
    const id = setInterval(() => {
      // STOPS WHEN THE TAB IS HIDDEN. A list left open overnight is otherwise
      // thousands of billed reads of a table nobody is looking at.
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      // AND STOPS once the window after a start has passed with nothing live. A run
      // that never appears means the dispatch failed, and polling for ever would
      // hide that behind a list that looks merely slow.
      if (!anyLive && Date.now() > pollUntil) return;
      setAttempt((n) => n + 1);
    }, 5000);
    return () => clearInterval(id);
  }, [anyLive, pollUntil]);


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

  /**
   * NO RUNS YET -- AND THE FIRST ONE MUST NOT LAND IN THIS BRANCH SILENTLY.
   *
   * **A NEW TENANT'S FIRST RUN IS EXACTLY THE CASE THE PLACEHOLDER EXISTS FOR**, and
   * the empty state short-circuited before the table, so pressing Start on an empty
   * list showed "No runs yet" for a full minute. That is worse than the original
   * silence: it is the screen actively asserting nothing has happened, immediately
   * after somebody made something happen.
   */
  if (runs.length === 0) {
    return showPending && pending ? (
      <PendingOnly issue={pending.issue} until={pending.until} />
    ) : (
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
            {/* THE RUN THAT HAS BEEN ASKED FOR AND DOES NOT EXIST YET.

                It is drawn in the ticket column and NOWHERE ELSE: no status mark,
                no verdict, no scan provenance. Every one of those is a measurement,
                and there is nothing to measure yet -- a `RUNNING` badge here would
                be a fabricated status on the first screen anybody sees, which is
                the defect this whole product is about.

                The issue number IS real, though: the issue was created before the
                dispatch, so `#62` is a link somebody can open on GitHub right now
                even while the pipeline has not started. */}
            {showPending && pending ? (
              <PendingRow issue={pending.issue} until={pending.until} />
            ) : null}
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
                  {/* WHICH REPOSITORY THE CHANGE WAS MADE TO, which this screen
                      did not say anywhere. Reported from the deployed app: the
                      list and the run page both showed a ticket, a verdict and a
                      cost without ever naming what was being changed.

                      IT REPLACED THE TRUNCATED RUN ID, which was a uuid clipped
                      to 22 characters -- too short to identify a run and too long
                      to ignore. The full id is on the run's own page, and the
                      link here already carries it.

                      `""` SAYS SO rather than naming the tenant's only repository:
                      that guess is right today and becomes wrong silently when a
                      second one is in scope. */}
                  <span
                    className="ident"
                    style={{
                      display: "block",
                      color: "var(--text-muted)",
                      fontSize: "var(--step-caption)",
                      marginTop: "var(--gap-1)",
                      // 34ch, NOT 26. `mohamedsorour1998/auth-service` is 30
                      // characters and was being clipped to
                      // `mohamedsorour1998/auth-se…` -- an owner in full and a
                      // repository truncated, which is the half that identifies
                      // it. The `title` carries the whole string either way.
                      maxWidth: "34ch",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {run.repository || "repository not recorded"}
                  </span>
                </td>
                <td>
                  {run.awaiting_gate === "" ? (
                    <span style={{ color: "var(--text-muted)" }}>
                      Not paused
                    </span>
                  ) : !run.ci_linked ? (
                    // PAUSED, AND NOBODY HERE CAN RELEASE IT. Muted rather than
                    // accent: the accent is reserved for things a person acts on,
                    // and offering this one as actionable is how a stalled run
                    // reads as work waiting for you.
                    <span style={{ color: "var(--text-muted)" }}>
                      Paused at {run.awaiting_gate}
                      <span
                        style={{
                          display: "block",
                          fontSize: "var(--step-caption)",
                        }}
                      >
                        not linked to Actions
                      </span>
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
                    {renderWhen(run.created_at)}
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

/**
 * One row for a run that has been dispatched and has not appeared.
 *
 * **THE SPINNER IS THE POINT, AND SO IS THE SENTENCE BESIDE IT.** A spinner alone
 * says "something is happening" and says nothing about what or for how long -- and
 * the run screen already refuses a bare spinner for that reason, showing when it
 * last heard from the run instead. Here there is nothing to have heard from yet, so
 * the honest pairing is motion plus the reason the wait exists.
 *
 * **IT GIVES UP, AND SAYING SO IS THE WHOLE VALUE OF THE STATE.** A placeholder
 * that spun for ever would turn a failed dispatch into a permanent "nearly there",
 * which is worse than the silence it replaced: silence at least ends when somebody
 * reloads. Past the window it becomes a refusal naming what to check.
 */
function PendingRow({ issue, until }: { issue: string; until: number }) {
  // A CLOCK IN AN INTERVAL, NEVER IN THE RENDER BODY. `react-hooks/purity` refuses
  // `Date.now()` during render, correctly: it makes identical props render
  // differently. The tick is the only thing that may see it.
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    const id = setInterval(() => setExpired(Date.now() > until), 2000);
    return () => clearInterval(id);
  }, [until]);

  return (
    <tr>
      <td colSpan={7}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--gap-3)",
            fontSize: "var(--step-small)",
          }}
        >
          {expired ? (
            <>
              <span className="ident" style={{ color: "var(--refused)" }}>
                #{issue}
              </span>
              <span style={{ color: "var(--refused)" }}>
                the issue was opened, and no run has appeared for it. The dispatch was
                accepted — check the workflow on GitHub.
              </span>
            </>
          ) : (
            <>
              {/* THE SAME MARK THE SPINE USES for a stage in flight: a ring with a
                  gap, turning. One spelling of "this is moving" across the product,
                  so a reader learns it once. */}
              <span
                className="spine-spinner"
                aria-hidden="true"
                style={{
                  width: "0.7rem",
                  height: "0.7rem",
                  borderRadius: "50%",
                  border: "2px solid var(--accent)",
                  borderTopColor: "transparent",
                  flexShrink: 0,
                }}
              />
              <span className="ident" style={{ color: "var(--accent)" }}>
                #{issue}
              </span>
              <span style={{ color: "var(--text-muted)" }}>
                issue opened, pipeline starting — the run appears here once the plan
                stage records it, usually within a minute
              </span>
            </>
          )}
        </span>
      </td>
    </tr>
  );
}

/**
 * The placeholder with no table under it: a tenant's FIRST run, mid-dispatch.
 *
 * A one-row table with no data rows would render a header for columns that have
 * nothing beneath them, so this is the same content in a card. It shares
 * `PendingRow`'s wording by construction rather than by copy -- one sentence, one
 * spelling, and the give-up state comes with it.
 */
function PendingOnly({ issue, until }: { issue: string; until: number }) {
  return (
    <div className="card">
      <table className="data" style={{ margin: 0 }}>
        <tbody>
          <PendingRow issue={issue} until={until} />
        </tbody>
      </table>
    </div>
  );
}
