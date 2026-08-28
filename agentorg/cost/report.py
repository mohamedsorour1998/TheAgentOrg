"""Rendering a `CostRecord` for a human. Text only.

THE RULE HERE IS THAT THIS RENDERER NEVER FLATTERS. Three distinctions the layers
below it work to preserve all collapse at exactly this point if the renderer is
careless, because a reader only ever sees the rendering:

  * `usd is None` prints *not priced*, never `$0.00`.
  * an unmeasurable cache prints *not measured*, never `0%`.
  * a partially priced run says HOW MANY stages were priced, so an understated
    total is never presented as a complete one.

Precedent for taking that seriously: `timeline._outcome` read its banner off the
last log row instead of off `RunState.status`, and a recorder's explanatory row
silently downgraded `BLOCKED` to `INCOMPLETE` while every state-reading assertion
stayed green. The record was right and the rendering was wrong, and the rendering
is what anybody looked at.

Text and not HTML, deliberately. The product UI is Lanes I/J, and they will render
`RunState.cost` themselves from the model -- a second HTML renderer here would be a
second place the same figures are formatted, free to disagree with the first.
"""

from __future__ import annotations

from ..state import CostRecord
from .prices import price_for
from .record import cache_hit_rate, price_stage

# What an unpriced figure says. Named rather than spelled at three call sites,
# because the whole point is that these two strings never become "$0.00".
_NOT_PRICED = "not priced"
_NOT_MEASURED = "not measured"


def _usd(value: float | None) -> str:
    """Format a dollar figure, or say it was not priced.

    THE ONE PLACE `None` COULD BECOME `$0.00`, so it is the one place to get right.
    Written as `f"${value or 0:.4f}"` this would print `$0.0000` for None -- and for
    a genuinely free run too, making a stale price table indistinguishable from a
    run that cost nothing. That is the defect `CostRecord.usd`'s nullability exists
    to prevent, arriving one layer later.

    Four decimal places because this pipeline's runs are cents-scale: a Nova 2.0
    Lite run of a few thousand tokens rounds to $0.00 at two places, which reads as
    free rather than as cheap.
    """
    if value is None:
        return _NOT_PRICED
    return f"${value:.4f}"


def _pct(rate: float | None) -> str:
    """Format a cache hit rate, or say it was not measured.

    Same trap as `_usd`, same reason. `f"{rate or 0:.1%}"` would print `0.0%` for
    None, asserting a measured miss where nothing was measured at all.
    """
    if rate is None:
        return _NOT_MEASURED
    return f"{rate:.1%}"


def render(cost: CostRecord | None) -> str:
    """One block of text: per-stage tokens, the total, and the cache finding.

    A None record is a run written before this instrumentation existed, or a run in
    which no model call happened at all. It reports as *no cost recorded* rather
    than as a zero row -- the fourth unnameable state, handled the way
    `scan_provenance`'s `""` is handled by the timeline renderer.
    """
    if cost is None or not cost.stages:
        return "cost: no model calls recorded for this run"

    lines = ["| stage | model | input | cached | output | cost |",
             "|---|---|---:|---:|---:|---:|"]
    for row in cost.stages:
        lines.append(
            f"| {row.stage} | {row.model or '(fixture)'} | {row.input_tokens} "
            f"| {row.cached_tokens} | {row.output_tokens} | {_usd(price_stage(row))} |"
        )

    total_in = sum(r.input_tokens for r in cost.stages)
    total_out = sum(r.output_tokens for r in cost.stages)
    total_cached = sum(r.cached_tokens for r in cost.stages)
    rate = cache_hit_rate(cost.stages)

    lines.append("")
    lines.append(
        f"**total** {total_in + total_cached} input ({total_cached} cached) "
        f"+ {total_out} output = {_usd(cost.usd)}"
    )

    # HOW MANY STAGES WERE PRICED, so a partial total is never read as complete.
    # `total_usd` deliberately understates rather than refusing when one stage's
    # model is unknown, and an understatement presented without this line is a
    # wrong number rather than a partial one.
    priced = sum(1 for row in cost.stages if price_stage(row) is not None)
    if priced != len(cost.stages):
        unpriced = sorted({row.model for row in cost.stages
                           if price_stage(row) is None and row.model})
        lines.append(
            f"_priced {priced} of {len(cost.stages)} stages; the total EXCLUDES "
            f"{len(cost.stages) - priced}. Unpriced model(s): "
            f"{', '.join(unpriced) or 'unnamed'}_"
        )

    # THE DATE THE PRICES WERE READ. A dollar figure with no date behind it is a
    # claim about the present that nobody checked -- see prices.py.
    dates = sorted({p.read_on for p in
                    (price_for(row.model) for row in cost.stages) if p is not None})
    if dates:
        lines.append(f"_prices read {', '.join(dates)} from the AWS Pricing API_")

    lines.append(f"_cache hit rate: {_pct(rate)}_")

    # THE MOST ACTIONABLE LINE IN THE WHOLE RECORD, and the reason E6 exists. Five
    # agents re-send a repository snapshot on every call; a zero here means that
    # cost is paid in full every time. Stated as a finding rather than left for a
    # reader to infer from a 0.0% in the line above, because nobody reads a
    # percentage as an alarm.
    #
    # THE CONDITION IS ON THE RENDERED STRING, NOT ON `rate == 0.0`, and that is a
    # measured fix rather than a stylistic one. `_pct` formats to one decimal
    # place, so every rate below 0.05% renders as `0.0%` while comparing unequal to
    # zero:
    #
    #     rate=1e-06   renders 0.0%   == 0.0? False
    #     rate=0.0004  renders 0.0%   == 0.0? False
    #     rate=0.0005  renders 0.1%   == 0.0? False
    #
    # Against `rate == 0.0` a run with one cached token in a million printed
    # `cache hit rate: 0.0%` with NO finding beside it -- so two runs showing the
    # reader an identical number got different verdicts, and the one that looked
    # fine was the one nobody was warned about. A rate that DISPLAYS as zero is a
    # rate the reader treats as zero, so it is the display that must decide.
    displays_as_zero = rate is not None and _pct(rate) == _pct(0.0)
    if displays_as_zero:
        # TWO CAUSES, AND THEY SEND YOU TO DIFFERENT PLACES. `StageCost` gained
        # `cached_reported` once the integrator added it (Lane E specified the field
        # and could not add it -- `state.py` is the frozen contract), so this layer
        # can finally tell them apart:
        #
        #   reported, zero    the provider measured caching and the answer was zero.
        #                     WE set no cache point. The fix is prompt assembly, in
        #                     the agents, and it is ours.
        #   never reported    no call in the run carried a cache field at all. The
        #                     suspect is our SDK reading or the provider's support,
        #                     and the fix is to go and look rather than to change
        #                     prompt code that may already be correct.
        #
        # Both used to render the first message, which sent a reader to change prompt
        # assembly on the strength of a measurement that had never been taken.
        # `any`, matching `build_cost_record`: one call reporting the field proves
        # the provider CAN report it.
        if any(row.cached_reported for row in cost.stages):
            lines.append(
                "_NO CACHED READS: the provider measured caching and reported zero. "
                "Every call paid full price for the repository snapshot it re-sent. "
                "Nothing in agentorg/ sets a cache point._"
            )
        else:
            lines.append(
                "_CACHING NEVER REPORTED: no model call in this run carried a cache "
                "field, so this 0.0% is an absence rather than a measurement. Check "
                "how usage is read before changing prompt assembly._"
            )
    elif rate is None:
        lines.append(
            "_CACHE NOT MEASURED: no input tokens were recorded, so a hit rate "
            "cannot be computed. This is not a zero._"
        )

    return "\n".join(lines)
