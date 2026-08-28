/**
 * WHAT ONE RUN COST. Reusable, and split in two on purpose.
 *
 * `CostPanel` is a pure renderer over a `CostView` the caller already has.
 * `CostPanelFor` fetches one and delegates. The split exists because the run
 * detail screen and the costs screen both need this panel and one of them has
 * already fetched -- a single fetching component would make that screen ask the
 * server for the same record twice, and the second answer could differ from the
 * one already on the page.
 *
 * THE THREE DISTINCTIONS THIS FILE EXISTS TO PRESERVE, none of them cosmetic:
 *
 *   1. `usd: null` is NOT PRICED; `usd: 0.0` is priced and free. Never
 *      `toFixed` here -- `renderUsd` owns that, and it answers "not priced"
 *      rather than "$0.0000" for the first. A missing price table displayed as
 *      a free run is the same defect class as an unscanned change reading as
 *      cleared.
 *   2. "Is cost wired at all?" is `costIsRecorded(stages_priced)`, NEVER `usd`.
 *      An unwired run has zero stage rows and `usd: null`; a wired run whose
 *      container fell back has a row per stage and `usd: 0.0`. `usd === 0`
 *      cannot separate them, so the unwired case gets WORDS, not a figure.
 *   3. `findings[]` sits ABOVE the numbers, not under them. `cache_hit_rate`
 *      renders `0.0%` for any rate below 0.05% while comparing unequal to
 *      zero, so the percentage cannot carry the alarm -- the prose does. Every
 *      string in `findings` is rendered; none is summarised or truncated.
 *
 * A CLIENT MODULE, because `CostPanelFor` holds state. `CostPanel` itself uses
 * no hook, so a server screen can still render it -- it simply becomes a client
 * boundary at that point, which costs one small bundle and no correctness.
 */

"use client";

import { useEffect, useState } from "react";

import { getJson } from "@/components/fetching";
import { EmptyState, ErrorState, Skeleton, Stat } from "@/components/primitives";
import { costIsRecorded, renderRate, renderUsd } from "@/components/vocabulary";
import type { CostView } from "@/lib/endpoints";

/**
 * A numeric cell: right-aligned, mono, unwrapped.
 *
 * Right-aligned because a column of token counts is compared digit by digit and
 * the units must line up; mono for the same reason.
 */
const NUM = {
  textAlign: "right",
  fontFamily: "var(--mono)",
  whiteSpace: "nowrap",
} as const;

/**
 * ONE fixed locale, not the reader's.
 *
 * `Number.toLocaleString()` reads the runtime's locale, which differs between
 * the server render and the browser -- React then reports a hydration mismatch
 * on a number that was never wrong. A named locale renders identically in both.
 */
const COUNT = new Intl.NumberFormat("en-US");

/** One run's cost, rendered from a record the caller already holds. */
export function CostPanel({ cost }: { cost: CostView }) {
  const recorded = costIsRecorded(cost.stages_priced);

  return (
    <section aria-label="Cost">
      <p className="eyebrow">Cost</p>

      {/* The findings first. See distinction 3 above. */}
      <Findings findings={cost.findings} />

      {recorded ? (
        <>
          <div
            className="grid-stats"
            style={{ margin: "0 0 var(--gap-6)" }}
          >
            <Stat value={renderUsd(cost.usd)} label="Priced total" />
            <Stat value={COUNT.format(cost.stages_priced)} label="Stages priced" />
            <Stat value={renderRate(cost.cache_hit_rate)} label="Cache hit rate" />
          </div>
          <StageTable stages={cost.stages} />
        </>
      ) : (
        /* EmptyState, not ErrorState: an unwired recorder is a gap in the
           measurement, not a fault in the run. Giving it a red border would
           teach a reader that a run nobody metered had failed. */
        <EmptyState
          headline="No model calls recorded for this run"
          action={
            "If this run did reach the agents, the usage recorder is not wired " +
            "on the path that ran it. Nothing here is a price of zero."
          }
        >
          <p className="prose" style={{ margin: 0 }}>
            A run that spent nothing still records one row per stage. This run
            has none, so there is nothing to price.
          </p>
        </EmptyState>
      )}
    </section>
  );
}

/** Fetch one run's cost, then render the panel above. */
export function CostPanelFor({ runId }: { runId: string }) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; cost: CostView }
    | { kind: "error"; error: string; fix: string; detail?: string }
  >({ kind: "loading" });

  useEffect(() => {
    // `cancelled` rather than an AbortController: the only thing that must not
    // happen is a setState after this panel has gone, and a run id can change
    // under it while a request is in flight.
    //
    // NO SYNCHRONOUS RESET TO `loading` HERE. Next 16's
    // `react-hooks/set-state-in-effect` refuses it, and correctly: an effect that
    // sets state on the render that scheduled it is a cascading render. The reset
    // is unnecessary anyway -- `runId` is the only input, so a caller changing it
    // should give this component a `key={runId}` and let React discard the old
    // state, which is stronger than resetting it by hand. Reset in an effect
    // still renders the previous run's figures for one frame; a key never does.
    let cancelled = false;

    void (async () => {
      const path = `/api/runs/${encodeURIComponent(runId)}/cost`;
      const result = await getJson<CostView>(path);
      if (cancelled) return;
      setState(
        result.ok
          ? { kind: "ready", cost: result.value }
          : {
              kind: "error",
              error: result.error,
              fix: result.fix,
              detail: result.detail,
            },
      );
    })();

    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (state.kind === "loading") return <Skeleton label="Loading cost" rows={3} />;
  if (state.kind === "error") {
    return (
      <ErrorState error={state.error} fix={state.fix} detail={state.detail} />
    );
  }
  return <CostPanel cost={state.cost} />;
}

/**
 * Every string in `findings`, as prose, above the numbers.
 *
 * Cyan and a 3px left rule rather than rose: this states a measured cost
 * finding, not a pipeline failure, and rose is reserved for a refusal so it
 * keeps meaning one.
 */
function Findings({ findings }: { findings: string[] }) {
  if (findings.length === 0) return null;
  return (
    <div
      role="note"
      style={{
        border: "1px solid var(--border-strong)",
        borderLeft: "3px solid var(--accent)",
        borderRadius: "4px",
        background: "var(--surface-raised)",
        padding: "var(--gap-4) var(--gap-6)",
        margin: "0 0 var(--gap-6)",
        maxWidth: "var(--measure)",
      }}
    >
      <p className="eyebrow" style={{ color: "var(--accent)" }}>
        What this record says
      </p>
      <ul
        style={{
          margin: 0,
          paddingLeft: "var(--gap-4)",
          fontSize: "var(--step-body)",
        }}
      >
        {findings.map((finding) => (
          <li key={finding} style={{ marginBottom: "var(--gap-2)" }}>
            {finding}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One row per stage that made a model call. */
function StageTable({ stages }: { stages: CostView["stages"] }) {
  if (stages.length === 0) return null;
  return (
    <div className="table-scroll">
      <table className="data">
        <caption>
          One row per stage that called the model. Token counts are what the
          provider reported, not an estimate.
        </caption>
        <thead>
          <tr>
            <th scope="col">Stage</th>
            <th scope="col">Model</th>
            <th scope="col" style={NUM}>
              Input
            </th>
            <th scope="col" style={NUM}>
              Output
            </th>
            <th scope="col" style={NUM}>
              Cached
            </th>
          </tr>
        </thead>
        <tbody>
          {stages.map((row, i) => (
            <tr key={`${row.stage}-${i}`}>
              <td className="ident">{row.stage}</td>
              <td className="ident">{row.model}</td>
              <td style={NUM}>{COUNT.format(row.input_tokens)}</td>
              <td style={NUM}>{COUNT.format(row.output_tokens)}</td>
              {/* `cached_reported: false` means the provider said NOTHING about
                  caching. Printing 0 there claims a measured miss. */}
              <td style={NUM}>
                {row.cached_reported ? (
                  COUNT.format(row.cached_tokens)
                ) : (
                  <span
                    style={{ color: "var(--text-muted)" }}
                    title="The provider reported no cache field for this call, which is not the same as reporting zero."
                  >
                    not reported
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
