"""Turning recorded model calls into a run's `CostRecord`.

THE ONE RULE IN THIS FILE: `usd` is None until something is genuinely priced.

`CostRecord.usd` is `float | None` on purpose, and Phase 0's docstring in
`state.py` spells out why: None means "not priced" -- an unknown model, or a price
table that has not been updated -- while `0.0` means "priced, and it was free".
Defaulting to zero would make a missing price table look like a free run, which is
this repository's signature defect shape: a value that reads as a legitimate
answer when the question was never asked.

Every function below is written so that shape survives. `total_usd` returns None
when NOTHING was priceable and skips unpriced rows otherwise; `price_stage`
returns None for a model the table does not know; `merge_cost_records` propagates
None rather than treating it as zero. The tests in `tests/test_cost.py` pin each
of those separately, because a single one of them collapsing is enough to turn an
unpriced run into a free-looking one.
"""

from __future__ import annotations

from ..common import llm
from ..state import CostRecord, StageCost
from .prices import price_for

# The stage a usage row is filed under when nobody stamped one. `StageCost.stage`
# is a `Stage` Literal that pydantic enforces, so this must be a real member of it
# -- see `_stage_or_fallback` for why it is not `""`.
_FALLBACK_STAGE = "plan"


def _stage_or_fallback(stage: str) -> str:
    """A `Stage`-valid value for a usage row whose stage nobody stamped.

    An unattributed row is a real case: `llm.attribute_usage_to` is called by the
    pipeline layer, and a probe or a unit test may call the model without one.
    `StageCost.stage` is a `Stage` Literal, so pydantic REFUSES `""` -- which would
    turn a missing label into a raised exception in the cost path, and this module
    must never be the thing that fails a run.

    Falling back to `plan` rather than inventing a `Stage` member: adding one would
    touch `state.py`, which this lane does not own, and every consumer's Literal
    would have to learn a value that means "unknown". The row is still counted, and
    its tokens still reach the total, which is what matters for a cost figure.
    """
    return stage or _FALLBACK_STAGE


def price_stage(row: StageCost) -> float | None:
    """What one stage's tokens cost, or None when its model is not in the table.

    NONE RATHER THAN ZERO FOR AN UNKNOWN MODEL. This is the load-bearing line of
    the whole lane: a stage whose model nobody priced has an unknown cost, and
    `0.0` is a specific, wrong, plausible answer to that question.

    CACHED TOKENS ARE PRICED AT THE CACHE-READ RATE AND NOT AT THE INPUT RATE, and
    they are NOT also charged as input. On Nova 2.0 Lite that is $0.0825/1M against
    $0.33/1M -- a 4x difference on the largest single input this pipeline sends, the
    repository snapshot. Adding them into `input_tokens` as well would double-count
    every cached token AND price it at 4x, which is the direction that flatters
    nobody: it would make caching look like it increased the bill.

    A fixture stage prices to exactly 0.0, not None, PROVIDED its model is known --
    and a fixture row carries `model=""`, which is not in the table, so it prices
    to None. That is correct and deliberate: nothing was spent, but nothing was
    priced either, and `total_usd` treats a zero-token row as contributing nothing
    to a total it can still compute from its priced siblings.
    """
    if row.input_tokens == 0 and row.output_tokens == 0 and row.cached_tokens == 0:
        # A zero-token stage costs zero whatever the model is -- including a fixture
        # fallback, whose `model` is deliberately blank. Answering None here would
        # make a whole run unpriceable the moment one agent fell back, which is the
        # common case rather than the edge one.
        return 0.0

    price = price_for(row.model)
    if price is None:
        return None

    return (
        row.input_tokens / 1_000_000 * price.input_per_million
        + row.output_tokens / 1_000_000 * price.output_per_million
        + row.cached_tokens / 1_000_000 * price.cache_read_per_million
    )


def total_usd(stages: list[StageCost]) -> float | None:
    """The run's cost, or None when not one stage could be priced.

    THE ASYMMETRY IS DELIBERATE AND IT RUNS THE SAFE WAY. A run with three priced
    stages and one unpriced one reports the sum of the three -- an UNDERSTATEMENT,
    and understating a cost you are told is partial is honest, while refusing to
    report anything because one row was unknown would throw away three real
    measurements.

    A run where NOTHING could be priced reports None, never 0.0. That is the case
    the whole nullable-`usd` design exists for: a stale price table, or a model
    nobody added, must not render as a free run.

    `report.render` names how many stages were priced, so an understated total is
    never presented as complete.
    """
    priced = [usd for usd in (price_stage(row) for row in stages) if usd is not None]
    if not priced:
        return None
    return sum(priced)


def build_cost_record(stage: str = "") -> CostRecord:
    """Fold every model call `llm` has recorded into one `CostRecord`.

    `stage` attributes any call that has not been attributed yet, which is the
    normal case: an agent calls the model without knowing which pipeline stage it
    is serving, and the caller knows. Passing it here rather than requiring a
    separate `llm.attribute_usage_to` call keeps the pipeline's integration to one
    line.

    CALLS ARE GROUPED PER STAGE, NOT PER CALL, matching `StageCost`'s own
    docstring: the questions asked of this data are "what did this run cost" and
    "which stage is expensive", and both are answered by a stage row. The
    developer/reviewer loop makes an unknown number of calls inside one `develop`
    stage, so a per-call record could not answer the second question without the
    reader summing rows themselves.

    THE MODEL ID IS TAKEN FROM THE LAST CALL IN THE STAGE THAT NAMED ONE, never
    from config. A fixture row carries `model=""`, and letting that blank overwrite
    a real id would unprice a stage that genuinely called the model -- the
    fixture-then-model ordering happens on every revision loop.
    """
    if stage:
        llm.attribute_usage_to(stage)

    grouped: dict[str, list[llm.Usage]] = {}
    for entry in llm.usage():
        grouped.setdefault(_stage_or_fallback(entry.stage), []).append(entry)

    stages: list[StageCost] = []
    for stage_name, entries in grouped.items():
        # `or model` keeps the last NON-BLANK id. See the docstring.
        model = ""
        for entry in entries:
            model = entry.model or model
        stages.append(StageCost(
            stage=stage_name,
            model=model,
            input_tokens=sum(e.input_tokens for e in entries),
            output_tokens=sum(e.output_tokens for e in entries),
            cached_tokens=sum(e.cached_tokens for e in entries),
            # `any`, not `all`: one call in the stage reporting a cache field is
            # enough to establish that the provider CAN report it, which is the
            # question this flag answers. `all` would let a single fixture row --
            # which reports nothing, by construction -- mask a real measurement, and
            # the fixture-then-model ordering happens on every revision loop.
            cached_reported=any(e.cached_reported for e in entries),
        ))

    return CostRecord(stages=stages, usd=total_usd(stages))


def merge_cost_records(before: CostRecord | None, after: CostRecord) -> CostRecord:
    """Add a stage's cost to what earlier stages already recorded.

    THE CLOUD PATH NEEDS THIS AND THE LOCAL PATH DOES NOT. On the cloud path each
    of the seven jobs is a separate PROCESS, so `llm`'s module state starts empty
    every time and a stage that simply assigned `state.cost` would erase every
    earlier stage's row -- the same shape as the rejection recorder that overwrote
    a block with a rejection, and just as invisible, because the surviving record
    would look complete.

    NONE PROPAGATES AS NONE, and this is the trap in this function. `total_usd` is
    recomputed over the MERGED stage list rather than added from the two `usd`
    fields, because `None + 0.4` raises and `(before.usd or 0) + (after.usd or 0)`
    would silently turn an unpriced half into a free half -- the exact collapse
    this whole module is written to prevent.

    Rows are appended, never merged by stage name. Two rows for one stage is a real
    signal -- a stage that ran twice, which the revision loop and a re-dispatched
    job both produce -- and merging them by name would hide a duplicate run behind
    a plausible single total.
    """
    if before is None:
        return after

    stages = [*before.stages, *after.stages]
    return CostRecord(stages=stages, usd=total_usd(stages))


def cache_hit_rate(stages: list[StageCost]) -> float | None:
    """Cached reads as a fraction of all input tokens, or None when unmeasurable.

    THE HEADLINE NUMBER OF THIS LANE, and the reason it returns `float | None`
    rather than a float. The five agents each re-send a repository snapshot on
    every call -- up to ~120KB -- so if this is zero across a whole run, the largest
    cost in the entire design is being paid in full, every single call, silently.
    An unmeasured cache is not a cache.

    None means THE DENOMINATOR WAS ZERO: no input tokens were recorded at all, so
    there is no rate to compute. Returning 0.0 there would report a 0% hit rate --
    a measured miss -- for a run in which caching was never measured. That is the
    same distinction `Usage.cached_reported` draws one layer down, and the same one
    `usd` draws between None and 0.0.

    0.0 IS ALSO A REAL ANSWER, and it is the answer this pipeline currently gives:
    nothing in `agentorg/` sets a Bedrock cache point, so Nova reports no
    `cacheReadInputTokens` at all. `report.render` prints that as an explicit
    finding rather than a blank, because a zero here is the single most actionable
    number the cost record contains.

    The denominator is fresh input PLUS cached reads -- the total the model was
    shown -- not fresh input alone. Against fresh input alone the rate could exceed
    1.0, and a "142% cache hit rate" on a slide is the kind of number that ends a
    presentation early.

    ── THE ONE DISTINCTION THAT DOES NOT REACH THIS LAYER, AND IT IS A GAP ──

    `llm.Usage` separates "the provider said nothing about caching"
    (`cached_reported=False`) from "the provider said zero" (`cached_tokens=0`,
    `cached_reported=True`), and that separation survives the remote seam --
    `usage_payload` carries the flag and a test pins it. IT STOPS HERE.
    `StageCost` declares no `cached_reported` field, so both cases arrive as a row
    with `cached_tokens=0` and this function answers 0.0 for each. MEASURED:

        provider SAID NOTHING   usage.cached_reported=False  -> rate=0.0
        provider SAID ZERO      usage.cached_reported=True   -> rate=0.0

    Both readings lead to "no caching is happening", so no reported number is
    WRONG -- but the two want different fixes, and a reader cannot tell which they
    have. Silent means our SDK reading or the provider's support is the suspect;
    zero means the provider measured and we simply set no cache point.

    NOT CLOSED HERE BECAUSE `state.py` IS THE FROZEN CONTRACT AND ANOTHER LANE'S
    FILE. The fix is one optional field -- `StageCost.cached_reported: bool =
    False` -- which is exactly the additive shape the freeze permits, plus one line
    in `build_cost_record` to carry `any(e.cached_reported for e in entries)`.
    Recorded rather than worked around: inferring the flag from anything available
    here would be a guess presented as a measurement.
    """
    shown = sum(row.input_tokens + row.cached_tokens for row in stages)
    if shown == 0:
        return None
    return sum(row.cached_tokens for row in stages) / shown
