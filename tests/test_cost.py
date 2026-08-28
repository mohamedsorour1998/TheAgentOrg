"""Cost and token instrumentation. Owner: Lane E.

WHAT THIS FILE IS DEFENDING. Before this lane, `agentorg/common/llm.py` recorded no
usage of any kind -- measured on the baseline -- so "what did that run cost" had no
answer. Two judge requirements hung on it. The tests below are grouped by the four
distinctions the instrumentation must preserve, because every one of them collapses
into a plausible-looking wrong answer if it is not pinned:

  1. `usd is None` (not priced) versus `usd == 0.0` (priced, free).
  2. `cached_reported is False` (the provider said nothing) versus
     `cached_tokens == 0` (the provider said zero).
  3. A fixture fallback recording a ZERO ROW versus recording NOTHING.
  4. A cache hit rate of `None` (unmeasurable) versus `0.0` (measured miss).

Each pair is one `if` away from collapsing, and in every case the collapsed
version reads as a legitimate answer -- a free run, a cache miss, an unmeasured
stage -- which is this repository's signature defect shape.

ON THE CONFTEST GUARDS. Guard 1 sets `config.LLM_DISABLED = True` and replaces
`llm._complete` with a `pytest.fail` raiser, so no test here may make a live model
call. Tests that need the model PATH opt in with all three lines in their own body,
per tests/conftest.py, and their `_complete` stub is a local function -- never the
real one. Tests that only exercise the recording layer call `llm._record_usage`
directly, which needs no opt-in at all because it touches no seam.
"""

from __future__ import annotations

import json

import pytest

from agentorg import cost, graph
from agentorg.common import agent_client, config, llm
from agentorg.cost import prices, record, report
from agentorg.state import CostRecord, RunState, StageCost

# The model this repository actually calls -- config.BEDROCK_MODEL's default. Read
# through the module rather than restated, so a config change makes these tests
# fail loudly rather than quietly pricing a model nobody runs.
MODEL = config.BEDROCK_MODEL


@pytest.fixture(autouse=True)
def _usage_is_per_test():
    """Clear llm's usage accumulator around every test in this file.

    Module state, exactly like `_LAST_SOURCE`, so a row recorded by one test would
    otherwise be counted by the next -- and a stale row looks precisely like a
    measurement. Cleared on BOTH sides for the reason conftest's scanner-cache
    fixture gives: one side alone leaves the other direction open.
    """
    llm.reset_usage()
    yield
    llm.reset_usage()


# ── the instrument exists at all ─────────────────────────────────────────────

def test_the_baseline_defect_is_closed_usage_is_recorded_somewhere():
    """The starting position was ZERO instrumentation. Pin that it is no longer.

    Deliberately the weakest assertion in the file, and first: everything below
    tests the SHAPE of the recording, and all of it would pass vacuously against a
    module that recorded nothing at all.
    """
    assert hasattr(llm, "usage"), "llm.usage() is gone; nothing can report a cost"
    assert llm.usage() == [], "the accumulator must start empty after a reset"

    llm._record_usage(llm.Usage(stage="plan", model=MODEL, input_tokens=10))

    assert len(llm.usage()) == 1, "a recorded call did not reach the accumulator"


def test_usage_returns_a_copy_so_a_caller_cannot_append():
    """`usage()` hands out a copy, not the live list.

    A caller appending to the returned list would be a second writer for the one
    fact this module owns, and the extra row would be indistinguishable from a real
    model call.
    """
    llm._record_usage(llm.Usage(stage="plan", model=MODEL, input_tokens=10))

    llm.usage().append(llm.Usage(stage="sre", model=MODEL, input_tokens=999))

    assert len(llm.usage()) == 1, "a caller mutated llm's own accumulator"


def test_reset_usage_forgets_the_previous_run():
    """A laptop runs several stages in one process. A run must not inherit tokens."""
    llm._record_usage(llm.Usage(stage="plan", model=MODEL, input_tokens=10))
    llm.reset_usage()

    assert llm.usage() == [], "reset_usage left rows behind; a run would inherit them"


# ── E1: the real model call is what gets measured ────────────────────────────

def test_a_model_call_records_the_token_counts_strands_reported(monkeypatch):
    """E1, through the REAL `text()` path with a stubbed `_complete`.

    THE MEASUREMENT HAS TO HAPPEN INSIDE `_complete`, because that is the only line
    in the repository holding an `AgentResult` -- `str(result)` discards
    `result.metrics`, which is exactly what the pre-instrumentation version did.

    So this test substitutes `_complete` with one that records the way the real one
    does, driving `llm.text()` for real. It does NOT assert that the production
    `_complete` body is correct -- no local test can, since that needs Bedrock --
    which is why `test_usage_is_read_off_a_real_strands_shaped_metrics_object`
    below feeds `_usage_from_metrics` a genuine strands-shaped object.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)

    def _stub(system_prompt, user_prompt):
        llm._record_usage(llm._usage_from_metrics(
            _FakeMetrics({"inputTokens": 1200, "outputTokens": 340,
                          "totalTokens": 1540}),
            MODEL,
        ))
        return '{"tasks": []}'

    monkeypatch.setattr(llm, "_complete", _stub)

    reply = llm.text("sys", "user")

    assert reply == '{"tasks": []}', "the stub's reply did not survive text()"
    rows = llm.usage()
    assert len(rows) == 1, f"expected one recorded call, got {len(rows)}"
    assert rows[0].input_tokens == 1200
    assert rows[0].output_tokens == 340
    assert rows[0].model == MODEL, "the model id was not recorded with the counts"


class _FakeMetrics:
    """A stand-in for strands' `EventLoopMetrics`, carrying only what we read.

    A DICT, WITH CAMELCASE KEYS, because that is what strands actually hands back:
    `accumulated_usage` is a TypedDict carrying Bedrock's own key names
    (`strands/types/event_loop.py`), not an object with snake_case attributes. A
    double using `input_tokens` here would let a bug that reads the wrong key pass
    every test in this file -- the exact "a test double that cannot express the
    failing case" pattern this repository has found nine times.
    """

    def __init__(self, accumulated_usage):
        self.accumulated_usage = accumulated_usage


def test_usage_is_read_off_a_real_strands_shaped_metrics_object():
    """The keys are camelCase and the reader must use those exact names.

    Pinned separately from the test above because this is the one that would catch
    a snake_case typo in `_usage_from_metrics`. Written with the literal key names
    strands emits, verified against the installed strands-agents 1.52.0.
    """
    usage = llm._usage_from_metrics(
        _FakeMetrics({
            "inputTokens": 500,
            "outputTokens": 60,
            "totalTokens": 560,
            "cacheReadInputTokens": 400,
            "cacheWriteInputTokens": 5,
        }),
        MODEL,
    )

    assert usage.input_tokens == 500, "inputTokens was not read"
    assert usage.output_tokens == 60, "outputTokens was not read"
    assert usage.cached_tokens == 400, "cacheReadInputTokens was not read"


def test_metrics_that_are_missing_entirely_record_a_zero_row_not_a_crash():
    """A substituted `_complete`, or a strands version that moved the attribute.

    Recorded as a zero-token row rather than skipped or raised: a call that happened
    is a call that happened, and this module must never be the thing that fails a
    run over bookkeeping.
    """
    usage = llm._usage_from_metrics(None, MODEL)

    assert usage.input_tokens == 0
    assert usage.model == MODEL, "the model id is still knowable without metrics"


# ── E6 / distinction 2: an absent cache key is NOT a zero ────────────────────

def test_an_absent_cache_key_is_recorded_as_unreported_not_as_zero():
    """THE DISTINCTION E6 TURNS ON, and it is a property of strands' own types.

    `cacheReadInputTokens` is declared OPTIONAL in strands' `Usage` TypedDict
    (`total=False`), and its accumulator only creates the key
    `if "cacheReadInputTokens" in source` -- so a provider that does not report
    caching leaves the key genuinely ABSENT. The OpenAI-compatible path is sharper
    still: it sets the key behind `if cached := ...`, so a real zero is falsy and
    omitted.

    Recording that absence as `cached_tokens=0` with nothing else would assert that
    the cache was measured and missed. It was not measured. An unmeasured cache is
    not a cache.
    """
    absent = llm._usage_from_metrics(
        _FakeMetrics({"inputTokens": 100, "outputTokens": 10, "totalTokens": 110}),
        MODEL,
    )
    measured_zero = llm._usage_from_metrics(
        _FakeMetrics({"inputTokens": 100, "outputTokens": 10, "totalTokens": 110,
                      "cacheReadInputTokens": 0}),
        MODEL,
    )

    assert absent.cached_reported is False, (
        "an ABSENT cacheReadInputTokens key was recorded as reported; a provider "
        "that never mentioned caching now looks like one that measured a miss"
    )
    assert measured_zero.cached_reported is True, (
        "a PRESENT cacheReadInputTokens key of 0 was recorded as unreported; a "
        "real measured miss is now indistinguishable from no measurement"
    )
    assert absent.cached_tokens == measured_zero.cached_tokens == 0, (
        "both cases carry zero cached tokens; only `cached_reported` separates them"
    )


def test_the_reported_flag_survives_the_fold_onto_a_stage_row():
    """Distinction 2 now reaches `StageCost`. It used to stop one layer short.

    THIS TEST REPLACES A GAP-PINNING TEST, and the replacement is the interesting part.
    Lane E wrote `test_the_reported_flag_does_NOT_survive_the_fold_and_that_gap_is_
    pinned`, which asserted `cached_reported` was ABSENT from `StageCost` and carried a
    message naming the three steps to finish the job. `state.py` is the frozen contract,
    so the lane could not add the field itself.

    The integrator added it, that test went red exactly as designed, and its message was
    the specification for this one. A gap recorded only in a comment gets closed halfway
    -- the field added, nothing reading it -- and nothing says so.

    `any`, not `all`. One call in the stage reporting a cache field establishes that the
    provider CAN report it, which is the question the flag answers. `all` would let a
    single fixture row -- which reports nothing, by construction -- mask a real
    measurement, and the fixture-then-model ordering happens on every revision loop.
    """
    llm.reset_usage()
    llm._record_usage(llm.Usage(stage="develop", model=MODEL, input_tokens=100,
                                output_tokens=10, cached_tokens=0,
                                cached_reported=False))   # a fixture-shaped row
    llm._record_usage(llm.Usage(stage="develop", model=MODEL, input_tokens=900,
                                output_tokens=90, cached_tokens=0,
                                cached_reported=True))    # the provider measured zero

    built = record.build_cost_record()
    row = next(r for r in built.stages if r.stage == "develop")

    assert row.cached_reported is True, (
        "one call in the stage reported a cache field and the folded row says nobody "
        "did; `all` was used where `any` is required, so a fixture row can mask a real "
        "measurement"
    )
    assert row.cached_tokens == 0, "the flag must not invent cached tokens"


def test_a_stage_where_nothing_reported_caching_stays_unreported():
    """The other half. Without this, `cached_reported=True` could be hardcoded.

    A test asserting only the True case passes against `cached_reported=True` written as
    a literal, which would make every run claim the provider measured caching -- and send
    a reader to change prompt assembly on the strength of a measurement never taken.
    """
    llm.reset_usage()
    llm._record_usage(llm.Usage(stage="plan", model=MODEL, input_tokens=500,
                                output_tokens=50, cached_tokens=0,
                                cached_reported=False))

    row = next(r for r in record.build_cost_record().stages if r.stage == "plan")

    assert row.cached_reported is False, (
        "no call reported a cache field, yet the row claims one did"
    )


def test_the_two_zero_cache_causes_render_differently():
    """The reader-facing half, and the reason the field was worth adding.

    Both cases display `0.0%`. Before the split they also produced the SAME finding --
    "every call paid full price... nothing sets a cache point" -- which sends a reader to
    change prompt assembly. That advice is right for one cause and wrong for the other:
    if no call ever carried a cache field, the suspect is how usage is read, and the
    prompt code may already be correct.

    Asserted on the RENDERED text, because the finding is the whole deliverable here. A
    field nobody renders is a field nobody reads.
    """
    reported = CostRecord(stages=[StageCost(stage="develop", model=MODEL,
                                            input_tokens=1000, output_tokens=100,
                                            cached_tokens=0, cached_reported=True)])
    silent = CostRecord(stages=[StageCost(stage="develop", model=MODEL,
                                          input_tokens=1000, output_tokens=100,
                                          cached_tokens=0, cached_reported=False)])

    reported_text = report.render(reported)
    silent_text = report.render(silent)

    assert "0.0%" in reported_text and "0.0%" in silent_text, (
        "both cases must still display the same rate; the field explains the zero, "
        "it does not change it"
    )
    assert "NO CACHED READS" in reported_text, (
        "a provider-measured zero must still name the actionable cause: we set no "
        "cache point"
    )
    assert "CACHING NEVER REPORTED" in silent_text, (
        "a run where nothing reported a cache field must say so rather than assert a "
        "measurement that was never taken"
    )
    assert reported_text != silent_text, (
        "the two causes render identically, so the distinction reaches the data and "
        "stops before the reader -- which is where it matters"
    )

    def _folded(cached_reported: bool) -> float | None:
        llm.reset_usage()
        llm._record_usage(llm.Usage(stage="plan", model=MODEL, input_tokens=20000,
                                    output_tokens=500, cached_tokens=0,
                                    cached_reported=cached_reported))
        return record.cache_hit_rate(record.build_cost_record().stages)

    silent, measured = _folded(False), _folded(True)

    assert silent == measured == 0.0, (
        f"the fold's behaviour changed: silent={silent}, measured={measured}. If "
        "these now differ, the gap has been closed somewhere else and this test "
        "is stale"
    )


def test_the_cache_hit_rate_reports_the_zero_this_pipeline_actually_has():
    """E6's headline number, and today it is ZERO.

    Nothing in `agentorg/` sets a Bedrock cache point, so Nova reports no
    `cacheReadInputTokens` at all -- which means the five agents each pay full price
    for the repository snapshot they re-send on every call. That is the largest
    silent cost in the current design, and this test is what makes it a number
    rather than a suspicion.
    """
    stages = [
        StageCost(stage="plan", model=MODEL, input_tokens=20000, output_tokens=500),
        StageCost(stage="develop", model=MODEL, input_tokens=40000, output_tokens=900),
    ]

    assert record.cache_hit_rate(stages) == 0.0, (
        "with no cached reads the hit rate must be exactly 0.0 -- a measured miss"
    )


def test_a_cache_hit_rate_is_none_when_there_were_no_input_tokens_at_all():
    """`None` means unmeasurable. It must never render as 0%.

    A zero denominator is not a zero rate. Returning 0.0 here would report a
    measured miss for a run in which caching was never measured -- distinction 4,
    and the same shape as `usd`'s None-versus-0.0.
    """
    assert record.cache_hit_rate([]) is None, (
        "an empty stage list produced a numeric hit rate; there is nothing to divide"
    )
    assert record.cache_hit_rate([StageCost(stage="plan")]) is None, (
        "a run with zero input tokens produced a numeric hit rate"
    )


def test_the_hit_rate_denominator_includes_cached_reads_so_it_cannot_exceed_one():
    """Cached / (fresh + cached), not cached / fresh.

    Against fresh input alone a well-cached run exceeds 1.0, and a "142% cache hit
    rate" on a slide ends a presentation early.
    """
    stages = [StageCost(stage="plan", model=MODEL,
                        input_tokens=1000, cached_tokens=9000)]

    rate = record.cache_hit_rate(stages)

    assert rate == pytest.approx(0.9), f"expected 0.9 of 10000 shown tokens, got {rate}"
    assert rate <= 1.0, "a hit rate above 1.0 means the denominator is wrong"


# ── E4: the price table, and the date it was read ────────────────────────────

def test_every_price_row_carries_the_date_it_was_read():
    """E4. An undated price becomes a confident wrong number a presenter quotes.

    Asserted over every row rather than a sampled one, so a row added later without
    a date fails here rather than reaching a slide.
    """
    assert prices.PRICES, "the price table is empty; this test would pin nothing"

    for model, row in prices.PRICES.items():
        assert row.read_on, f"{model} has no read_on date"
        # An ISO date, not a free-form note -- "recently" is not auditable.
        assert len(row.read_on) == 10 and row.read_on.count("-") == 2, (
            f"{model}'s read_on is {row.read_on!r}, not an ISO YYYY-MM-DD date"
        )
        assert row.source, f"{model} does not say what command produced its numbers"


def test_the_model_this_repository_calls_is_actually_in_the_table():
    """The one row that must exist, or every real run reports `usd=None`.

    `config.BEDROCK_MODEL` is read through the module rather than hardcoded, so
    changing the default model fails this test instead of silently unpricing runs.
    """
    row = prices.price_for(MODEL)

    assert row is not None, (
        f"{MODEL} -- the model config.BEDROCK_MODEL defaults to -- is not in the "
        f"price table, so every real run would report an unpriced cost"
    )
    assert row.input_per_million > 0, "an input rate of zero is not a measured price"
    assert row.output_per_million > 0, "an output rate of zero is not a measured price"


def test_nova_2_lite_is_priced_at_the_rate_the_aws_pricing_api_reported():
    """The measured numbers, pinned so a later edit cannot quietly change them.

    Measured 2026-08-28 via `aws pricing get-products --service-code AmazonBedrock
    --region us-east-1`, model `Nova 2.0 Lite`, feature `On-demand Inference`,
    STANDARD tier (not flex, not priority):

        Input tokens                    0.00033   / 1K = $0.33   /1M
        Output tokens                   0.00275   / 1K = $2.75   /1M
        Prompt cache read input tokens  0.0000825 / 1K = $0.0825 /1M
    """
    row = prices.price_for("us.amazon.nova-2-lite-v1:0")

    assert row.input_per_million == pytest.approx(0.33)
    assert row.output_per_million == pytest.approx(2.75)
    assert row.cache_read_per_million == pytest.approx(0.0825)


def test_nova_lite_and_nova_2_lite_are_not_the_same_price():
    """TRAP 2 FROM prices.py, pinned. Reading the wrong row understates output 11x.

    They are different models with confusingly similar ids, and the older one is an
    order of magnitude cheaper on output. A future edit that collapsed them would
    make every cost figure wrong in the flattering direction.
    """
    old = prices.price_for("us.amazon.nova-lite-v1:0")
    new = prices.price_for("us.amazon.nova-2-lite-v1:0")

    assert old.output_per_million < new.output_per_million, (
        "Nova Lite and Nova 2.0 Lite now price identically; one of the two rows "
        "was read from the wrong catalogue entry"
    )


def test_an_unknown_model_has_no_price_and_no_fallback_row():
    """`price_for` must MISS, loudly, rather than guess from a similar id.

    A `nova-3-lite` that quietly matched the nova-2 row would report a confident
    figure computed from the wrong model's rates, and nothing downstream could tell.
    """
    assert prices.price_for("us.amazon.nova-3-lite-v1:0") is None
    assert prices.price_for("") is None, "an empty model id must not resolve a price"


# ── E2 / distinction 1: unpriced is NOT free ─────────────────────────────────

def test_an_unpriced_model_costs_none_not_zero():
    """THE LOAD-BEARING ASSERTION OF THE WHOLE LANE.

    `0.0` means priced and free. `None` means nobody could price it. Defaulting to
    zero would make a stale price table look like a free run -- a value that reads
    as a legitimate answer to a question that was never asked.
    """
    unknown = StageCost(stage="plan", model="some-model-nobody-added",
                        input_tokens=5000, output_tokens=500)

    assert record.price_stage(unknown) is None, (
        "a stage whose model is not in the price table reported a number; an "
        "unknown cost must be None, never 0.0"
    )


def test_a_run_where_nothing_could_be_priced_reports_usd_none():
    """The record-level form of the same rule."""
    stages = [StageCost(stage="plan", model="unknown-model", input_tokens=1000,
                        output_tokens=100)]

    assert record.total_usd(stages) is None, (
        "a wholly unpriced run reported a total; it must be None"
    )


def test_a_priced_run_reports_a_real_number_and_it_is_not_zero():
    """The other half: the None path must not be reachable for a KNOWN model.

    Without this, a mutation making `price_stage` always return None would satisfy
    every unpriced-is-None test in this file.
    """
    stages = [StageCost(stage="plan", model=MODEL,
                        input_tokens=1_000_000, output_tokens=1_000_000)]

    total = record.total_usd(stages)

    assert total is not None, "a known model produced no price"
    # 1M input at $0.33 + 1M output at $2.75.
    assert total == pytest.approx(0.33 + 2.75), f"expected $3.08, got {total}"


def test_cached_tokens_are_priced_at_the_cache_rate_and_not_also_as_input():
    """Cached reads cost 4x less on Nova 2.0 Lite, and must not be double-counted.

    Adding them into `input_tokens` as well would charge every cached token twice
    AND at 4x the right rate -- making caching look like it INCREASED the bill,
    which is the one direction that would discredit E6's finding.
    """
    fresh = StageCost(stage="plan", model=MODEL, input_tokens=1_000_000)
    cached = StageCost(stage="plan", model=MODEL, cached_tokens=1_000_000)

    assert record.price_stage(fresh) == pytest.approx(0.33)
    assert record.price_stage(cached) == pytest.approx(0.0825), (
        "cached tokens were not priced at the cache-read rate"
    )
    assert record.price_stage(cached) < record.price_stage(fresh), (
        "a cached token costs at least as much as a fresh one; the rates are swapped"
    )


def test_a_partially_priced_run_reports_the_priced_part_and_says_so():
    """Understating a partial total is honest; refusing to report three real
    measurements because a fourth was unknown is not.

    The rendering is what makes the understatement safe, so both halves are pinned
    together: the number, and the sentence that qualifies it.
    """
    stages = [
        StageCost(stage="plan", model=MODEL, input_tokens=1_000_000),
        StageCost(stage="sre", model="unknown-model", input_tokens=1_000_000),
    ]
    rendered = report.render(CostRecord(stages=stages, usd=record.total_usd(stages)))

    assert record.total_usd(stages) == pytest.approx(0.33), (
        "the priced stage's cost was lost because a sibling was unpriced"
    )
    assert "priced 1 of 2 stages" in rendered, (
        f"the rendering does not say the total is partial:\n{rendered}"
    )
    assert "unknown-model" in rendered, "the rendering does not name what it could not price"


# ── E7 / distinction 3: a fixture fallback records ZERO, not NOTHING ─────────

def test_a_fixture_fallback_records_a_zero_row_rather_than_nothing():
    """E7. A stage that fell back must be distinguishable from an unmeasured one.

    Same reasoning as `scan_provenance` in the security lane: "the check did not
    run" and "the check passed" must never produce identical records. With a row
    present and zero, a reader knows the stage ran and spent nothing; with no row,
    the honest reading is that nothing was instrumented at all.
    """
    llm.record_fixture_fallback()

    rows = llm.usage()
    assert len(rows) == 1, (
        "a fixture fallback recorded nothing; a stage that fell back is now "
        "indistinguishable from a stage nobody measured"
    )
    assert rows[0].fixture is True, "the row does not say a fixture stood in"
    assert rows[0].input_tokens == 0 and rows[0].output_tokens == 0, (
        "a fixture fallback recorded a non-zero token count"
    )


def test_a_fixture_row_is_added_alongside_a_real_call_not_instead_of_it():
    """Both facts are true when the model answered and the CALLER rejected the reply.

    The tokens were really spent and a fixture was really served. Dropping either
    row would misreport one of them -- and `structured()` reaching
    `record_fixture_fallback` after a successful `text()` is the common case, not an
    edge one.
    """
    llm._record_usage(llm.Usage(stage="plan", model=MODEL,
                                input_tokens=900, output_tokens=80))
    llm.record_fixture_fallback()

    rows = llm.usage()
    assert len(rows) == 2, f"expected the real call AND the fixture row, got {len(rows)}"
    assert rows[0].input_tokens == 900, "the real call's tokens were overwritten"
    assert rows[1].fixture is True


def test_a_fixture_only_stage_prices_to_zero_and_does_not_unprice_the_run():
    """A fixture row carries no model, and that must not make the run unpriceable.

    An agent falling back is the COMMON case, so answering None for its zero-token
    stage would report an unpriced total for most real runs -- which would make the
    None-means-unpriced signal useless by crying wolf.
    """
    fixture_stage = StageCost(stage="review", model="", input_tokens=0, output_tokens=0)

    assert record.price_stage(fixture_stage) == 0.0, (
        "a zero-token fixture stage did not price to exactly 0.0"
    )
    assert record.total_usd([
        StageCost(stage="plan", model=MODEL, input_tokens=1_000_000),
        fixture_stage,
    ]) == pytest.approx(0.33), "a fixture stage changed the run's total"


# ── E2 / E5: building the record, and summing it ─────────────────────────────

def test_usage_is_grouped_per_stage_and_the_totals_sum():
    """E5, and `StageCost`'s own "per stage rather than per call" decision.

    The developer/reviewer loop calls the model repeatedly inside ONE `develop`
    stage, so the record must fold those into one row whose numbers add up.
    """
    llm._record_usage(llm.Usage(stage="develop", model=MODEL,
                                input_tokens=1000, output_tokens=100))
    llm._record_usage(llm.Usage(stage="develop", model=MODEL,
                                input_tokens=1500, output_tokens=200))
    llm._record_usage(llm.Usage(stage="plan", model=MODEL,
                                input_tokens=800, output_tokens=50))

    built = cost.build_cost_record()

    by_stage = {row.stage: row for row in built.stages}
    assert set(by_stage) == {"develop", "plan"}, (
        f"expected one row per stage, got {sorted(by_stage)}"
    )
    assert by_stage["develop"].input_tokens == 2500, (
        f"the two develop calls did not sum: {by_stage['develop'].input_tokens}"
    )
    assert by_stage["develop"].output_tokens == 300
    assert by_stage["plan"].input_tokens == 800


def test_building_a_record_attributes_unstamped_calls_to_the_given_stage():
    """An agent does not know its stage; the caller does.

    `attribute_usage_to` fills only BLANK stages, so a call already attributed to
    an earlier stage is settled history and must not be relabelled.
    """
    llm._record_usage(llm.Usage(stage="plan", model=MODEL, input_tokens=100))
    llm._record_usage(llm.Usage(model=MODEL, input_tokens=200))   # nobody said

    built = cost.build_cost_record("develop")

    by_stage = {row.stage: row.input_tokens for row in built.stages}
    assert by_stage == {"plan": 100, "develop": 200}, (
        f"attribution relabelled a settled row: {by_stage}"
    )


def test_a_stages_model_id_is_not_erased_by_a_later_fixture_row():
    """A fixture row carries `model=""`, and it must not unprice the stage.

    The fixture-then-model ordering happens on every revision loop, so a version
    taking the LAST model id unconditionally would blank a stage that genuinely
    called the model -- turning a priced run into an unpriced one.
    """
    llm._record_usage(llm.Usage(stage="develop", model=MODEL,
                                input_tokens=1000, output_tokens=100))
    llm.attribute_usage_to("develop")
    llm.record_fixture_fallback()
    llm.attribute_usage_to("develop")

    built = cost.build_cost_record()

    assert len(built.stages) == 1, "the two rows did not group into one stage"
    assert built.stages[0].model == MODEL, (
        f"a blank fixture model erased the real one: {built.stages[0].model!r}. "
        f"The stage would now report usd=None despite having called the model."
    )
    assert built.usd is not None, "the stage lost its price"


def test_a_record_built_from_nothing_is_empty_and_unpriced():
    """No model calls means no rows and `usd=None` -- not a zero-dollar run.

    This is the state every pre-instrumentation run on disk is in, and it must not
    render as free.
    """
    built = cost.build_cost_record()

    assert built.stages == []
    assert built.usd is None, "a run with no model calls reported a dollar figure"


# ── E5: merging across the seven-job cloud path ──────────────────────────────

def test_merging_appends_a_stage_rather_than_replacing_the_earlier_ones():
    """THE CLOUD PATH'S REQUIREMENT. Each of seven jobs is a separate PROCESS.

    `llm`'s module state starts empty in every job, so a stage that simply assigned
    `state.cost` would erase every earlier stage's row -- the same shape as the
    rejection recorder that overwrote a block, and just as invisible, because the
    surviving record would look complete.
    """
    before = CostRecord(stages=[StageCost(stage="plan", model=MODEL,
                                          input_tokens=1_000_000)], usd=0.33)
    after = CostRecord(stages=[StageCost(stage="develop", model=MODEL,
                                         input_tokens=1_000_000)], usd=0.33)

    merged = record.merge_cost_records(before, after)

    assert [row.stage for row in merged.stages] == ["plan", "develop"], (
        "the earlier stage's row was lost when the later stage merged its own"
    )
    assert merged.usd == pytest.approx(0.66), (
        f"the merged total is {merged.usd}, not the sum of both stages"
    )


def test_merging_an_unpriced_half_does_not_make_it_free():
    """`None` propagates. It must not be coerced to zero on the way through.

    Written as `(before.usd or 0) + (after.usd or 0)` this would silently price an
    unknown model at nothing -- the exact collapse the whole module prevents. The
    total is recomputed over the merged stage list for that reason.
    """
    priced = CostRecord(stages=[StageCost(stage="plan", model=MODEL,
                                          input_tokens=1_000_000)], usd=0.33)
    unpriced = CostRecord(stages=[StageCost(stage="sre", model="unknown-model",
                                            input_tokens=1_000_000)], usd=None)

    merged = record.merge_cost_records(priced, unpriced)

    assert len(merged.stages) == 2, "a stage was dropped rather than merged"
    assert merged.usd == pytest.approx(0.33), (
        f"the unpriced stage contributed {merged.usd} instead of being excluded"
    )


def test_merging_onto_nothing_keeps_the_first_stage():
    """The `plan` job has no earlier record. `None` in, the new record out."""
    first = CostRecord(stages=[StageCost(stage="plan", model=MODEL)], usd=0.0)

    assert record.merge_cost_records(None, first) is first


def test_two_rows_for_one_stage_are_kept_apart_not_merged_by_name():
    """A stage that ran TWICE is a real signal -- a re-dispatched job.

    Merging by name would hide a duplicate run behind a plausible single total.
    """
    once = CostRecord(stages=[StageCost(stage="develop", model=MODEL,
                                        input_tokens=1_000_000)], usd=0.33)
    twice = record.merge_cost_records(once, once)

    assert len(twice.stages) == 2, (
        "two runs of one stage collapsed into a single row; a duplicate run is "
        "now invisible in the cost record"
    )
    assert twice.usd == pytest.approx(0.66), "the duplicated cost was not counted"


# ── E3: crossing the remote seam ─────────────────────────────────────────────

def test_usage_survives_a_round_trip_over_the_response_envelope():
    """E3. Under REMOTE_AGENTS=true the model call happens in the CONTAINER.

    So `llm.usage()` on the runner is always empty -- exactly as
    `llm.last_source()` was always None before the provenance fix, which is how the
    deployed pipeline printed `_source=none` beside plainly model-written output.
    Usage travels back on the 200 envelope the same way `source` does.

    Serialised through `json.dumps`/`loads` deliberately: `agents/server.py` is
    standard-library only, and a payload holding a dataclass would fail there and
    not here.
    """
    llm._record_usage(llm.Usage(stage="develop", model=MODEL, input_tokens=2000,
                                output_tokens=300, cached_tokens=1500,
                                cached_reported=True))

    wire = json.loads(json.dumps({"agent": "developer", "usage": llm.usage_payload()}))
    llm.reset_usage()                      # the runner never saw the container's calls
    assert llm.usage() == [], "the runner's accumulator should start empty"

    accepted = llm.absorb_usage_payload(wire["usage"])

    assert accepted == 1, f"expected one row absorbed, got {accepted}"
    landed = llm.usage()[0]
    assert landed.stage == "develop"
    assert landed.input_tokens == 2000
    assert landed.cached_tokens == 1500
    assert landed.cached_reported is True, (
        "cached_reported did not survive the wire; an absent cache key and a "
        "measured zero would be indistinguishable on the remote path"
    )


def test_an_older_container_that_sends_no_usage_records_nothing_not_a_zero():
    """Backward compatible in the honest direction.

    A container built before this lane omits the key. Inventing a zero-token row
    for it would assert that the stage spent nothing, when the truth is that the
    container could not report -- and `usd` would then read as a free stage.
    """
    assert llm.absorb_usage_payload(None) == 0
    assert llm.usage() == [], (
        "an absent usage key produced a row; a container that could not report "
        "now looks like one that reported zero"
    )


def test_a_malformed_usage_payload_is_refused_without_raising():
    """This runs on the runner's side of a network call and must never raise.

    `agent_client` records the container's provenance BEFORE validation on purpose,
    so a bookkeeping function that raised there would turn a cost-reporting gap
    into a failed stage.
    """
    for junk in ("not a list", 42, {"usage": "wrong shape"}, [None, 7, "x"]):
        assert llm.absorb_usage_payload(junk) == 0, f"{junk!r} was accepted"

    assert llm.usage() == [], "a malformed payload recorded rows"


def test_a_usage_row_with_unreadable_numbers_is_skipped_and_counted_out():
    """A row whose numbers are not numbers loses that row, not the stage.

    The return count is what lets a caller tell "nothing was sent" from "rows were
    sent and rejected" -- two different faults with two different fixes.
    """
    accepted = llm.absorb_usage_payload([
        {"stage": "plan", "model": MODEL, "input_tokens": "not-a-number"},
        {"stage": "plan", "model": MODEL, "input_tokens": 100},
    ])

    assert accepted == 1, f"expected one of two rows accepted, got {accepted}"
    assert len(llm.usage()) == 1, "the unreadable row was recorded anyway"


def test_the_payload_is_json_serialisable_because_the_server_is_stdlib_only():
    """`agents/server.py` uses `json.dumps` and cannot encode a dataclass.

    The same reason that file already needs `model_dump(mode="json")` rather than
    `model_dump()`.
    """
    llm.record_fixture_fallback()
    llm._record_usage(llm.Usage(stage="plan", model=MODEL, input_tokens=5))

    encoded = json.dumps(llm.usage_payload())     # must not raise

    assert json.loads(encoded)[1]["input_tokens"] == 5


# ── the rendering, where every distinction above could still collapse ────────

def test_an_unpriced_run_renders_as_not_priced_never_as_zero_dollars():
    """THE LAST PLACE `None` COULD BECOME `$0.00`, and the only one a reader sees.

    Precedent: `timeline._outcome` read its banner off the wrong field and
    downgraded BLOCKED to INCOMPLETE while every state-reading assertion stayed
    green. The record was right and the rendering was wrong.
    """
    rendered = report.render(CostRecord(
        stages=[StageCost(stage="plan", model="unknown-model", input_tokens=1000)],
        usd=None,
    ))

    assert "not priced" in rendered, f"an unpriced run did not say so:\n{rendered}"
    assert "$0.00" not in rendered, (
        f"an unpriced run rendered a dollar figure:\n{rendered}"
    )


def test_a_free_run_renders_a_zero_figure_rather_than_not_priced():
    """The other half. `0.0` means priced and free, and must READ as a number.

    Without this, a renderer that printed "not priced" for every falsy value would
    satisfy the test above while destroying the distinction it defends.
    """
    rendered = report.render(CostRecord(
        stages=[StageCost(stage="review", model=MODEL)], usd=0.0,
    ))

    assert "$0.0000" in rendered, f"a priced, free run did not render $0:\n{rendered}"


def test_a_run_with_no_cached_reads_renders_the_finding_in_words():
    """E6's actionable line. Nobody reads `0.0%` as an alarm.

    A zero here means the five agents each paid full price for the repository
    snapshot they re-sent, on every call -- the largest silent cost in the design.

    `cached_reported=True` IS REQUIRED FOR THIS TO BE THE RIGHT FINDING, and that is
    not incidental. The row says the provider measured caching and reported zero, so
    the cause is ours -- we set no cache point -- and "every call paid full price" is
    sound advice. A row with the flag False means nothing reported a cache field at
    all, which renders the same 0.0% and warrants a DIFFERENT message; see
    `test_the_two_zero_cache_causes_render_differently`. Updated by the integrator
    when `StageCost.cached_reported` landed.
    """
    rendered = report.render(CostRecord(
        stages=[StageCost(stage="plan", model=MODEL, input_tokens=20000,
                          cached_reported=True)],
        usd=0.0066,
    ))

    assert "cache hit rate: 0.0%" in rendered, f"the rate is missing:\n{rendered}"
    assert "NO CACHED READS" in rendered, (
        f"a zero cache hit rate was not stated as a finding:\n{rendered}"
    )


def test_a_cache_rate_that_merely_ROUNDS_to_zero_still_carries_the_finding():
    """The gap between `== 0.0` and what the reader actually sees on the page.

    MEASURED, and this is a real defect the first version of `report.render` had.
    `_pct` formats to one decimal place, so EVERY rate below 0.05% renders as the
    string `0.0%` -- while `rate == 0.0` is False, which suppressed the alarm:

        rate=1e-06      renders 0.0%    ==0.0? False
        rate=0.0004     renders 0.0%    ==0.0? False
        rate=0.0005     renders 0.1%    ==0.0? False

    So a run with one cached token in a million rendered `cache hit rate: 0.0%`
    with NO finding beside it, and a reader comparing it against a genuinely
    uncached run saw the same number and a different verdict. Either reading is
    wrong: a rate that displays as zero is a rate the reader will treat as zero,
    and E6 exists precisely because nobody reads `0.0%` as an alarm on its own.

    The condition is therefore on the RENDERED string, not on the float. Pinning
    it on the float is what let the gap exist -- the test and the code agreed with
    each other and neither agreed with the page.
    """
    # One cached token in ~1M shown: 1e-06, which is NOT 0.0 and renders as 0.0%.
    # `cached_reported=True` because a row carrying a cached token self-evidently had
    # a cache field to read it from -- and it keeps this test on the branch it is
    # about, which is the ROUNDING, not the reported/silent split.
    barely = [StageCost(stage="plan", model=MODEL, input_tokens=999_999,
                        output_tokens=10, cached_tokens=1, cached_reported=True)]

    rate = record.cache_hit_rate(barely)
    assert rate != 0.0, (
        "this test needs a rate that is NOT exactly zero, or it pins nothing"
    )
    assert report._pct(rate) == "0.0%", (
        f"this test needs a rate that RENDERS as 0.0%, or it pins nothing; got "
        f"{report._pct(rate)!r}"
    )

    rendered = report.render(CostRecord(stages=barely, usd=0.33))

    assert "NO CACHED READS" in rendered, (
        "a cache hit rate that renders as `0.0%` carried no finding. The reader "
        "sees a measured zero and is told nothing about it, which is the exact "
        f"failure E6 exists to prevent:\n{rendered}"
    )


def test_an_unmeasured_cache_renders_as_not_measured_never_as_a_percentage():
    """Distinction 4, at the rendering layer. `0%` would claim a measured miss."""
    rendered = report.render(CostRecord(stages=[StageCost(stage="plan")], usd=None))

    assert "not measured" in rendered, f"an unmeasurable cache rate:\n{rendered}"
    assert "0.0%" not in rendered, (
        f"an unmeasured cache rendered as a measured zero:\n{rendered}"
    )


def test_the_rendering_carries_the_date_the_prices_were_read():
    """E4 reaches the reader, or it did not happen.

    A dollar figure with no date behind it is a claim about the present that nobody
    checked -- and the presenter is the one who gets caught holding it.
    """
    rendered = report.render(CostRecord(
        stages=[StageCost(stage="plan", model=MODEL, input_tokens=1000)], usd=0.00033,
    ))

    assert prices.PRICES[MODEL].read_on in rendered, (
        f"the rendering does not say when the prices were read:\n{rendered}"
    )


def test_a_run_with_no_cost_record_says_so_rather_than_rendering_zeroes():
    """Every run on disk written before this lane has `cost=None`.

    The fourth unnameable state, handled the way `scan_provenance`'s `""` is.
    """
    rendered = report.render(None)

    assert "no model calls recorded" in rendered
    assert "$" not in rendered, f"a run with no record rendered a figure:\n{rendered}"


# ── the contract, end to end ─────────────────────────────────────────────────

def test_a_cost_record_round_trips_through_the_frozen_contract():
    """`RunState.cost` is a Phase 0 field, and a run's record must survive a save.

    The cloud path hands state between seven jobs as a JSON artifact, so a record
    that could not serialise would be lost between `plan` and `develop` with
    nothing raised.
    """
    llm._record_usage(llm.Usage(stage="plan", model=MODEL,
                                input_tokens=1200, output_tokens=340))
    state = RunState(ticket_id="7", ticket_text="x", cost=cost.build_cost_record())

    reloaded = RunState.model_validate_json(state.model_dump_json())

    assert reloaded.cost is not None, "the cost record did not survive a round trip"
    assert reloaded.cost.stages[0].input_tokens == 1200
    assert reloaded.cost.usd == pytest.approx(state.cost.usd)


def test_a_pre_instrumentation_state_still_loads_with_no_cost():
    """The property that made every Phase 0 addition safe, checked for this field.

    A run written before the instrumentation existed must load and report `None` --
    unmeasured, which is honest -- rather than fail validation or default to free.
    """
    state = RunState.model_validate_json(json.dumps({
        "run_id": "abc", "ticket_id": "7", "ticket_text": "x", "status": "promoted",
    }))

    assert state.cost is None, "cost must default to None, not an empty record"


def test_the_full_shape_of_a_completed_runs_cost_record():
    """WHAT A REAL END-TO-END RUN NOW REPORTS, constructed stage by stage.

    Every stage of a promoted run: `plan`, the `develop` loop calling the model
    three times, `security` reading it once, and `sre`. Built through the real
    accumulator and priced through the real table, so this is the shape a judge
    would be shown -- including the zero cache hit rate, which is the finding.
    """
    for stage, calls in (("plan", 1), ("develop", 3), ("security", 1), ("sre", 1)):
        for _ in range(calls):
            llm._record_usage(llm.Usage(stage=stage, model=MODEL,
                                        input_tokens=18_000, output_tokens=900))

    built = cost.build_cost_record()

    assert [row.stage for row in built.stages] == ["plan", "develop", "security", "sre"]
    assert built.stages[1].input_tokens == 54_000, "the three develop calls did not sum"
    total_input = sum(row.input_tokens for row in built.stages)
    assert total_input == 108_000, f"expected 108,000 input tokens, got {total_input}"
    # 108,000 input at $0.33/1M + 5,400 output at $2.75/1M.
    assert built.usd == pytest.approx(108_000 / 1e6 * 0.33 + 5_400 / 1e6 * 2.75)
    assert record.cache_hit_rate(built.stages) == 0.0, (
        "this pipeline sets no cache point, so a real run's hit rate is 0.0"
    )


def test_a_run_through_the_real_pipeline_records_a_cost_for_every_stage(monkeypatch):
    """THE INTEGRATION, driven through `graph.run_pipeline` rather than asserted about.

    WHY THIS TEST EXISTS AND WHAT IT PROVES. Every test above exercises one function.
    This one drives the real pipeline with a stubbed `_complete` that reports
    strands-shaped usage, so it is the only test here that would catch the
    instrumentation being present and never REACHED.

    IT ALSO PINS THE PENDING WIRING, and that is the honest part. Attribution needs
    one `llm.attribute_usage_to(...)` call per stage, and the natural home for it --
    `graph.py` and `scripts/run_stage.py` -- belongs to the integrator, not to Lane E.
    So this test installs that call itself, around `call_agent`, exactly where the
    integrator would put it. WITHOUT it every one of the nine model calls in a run
    lands in a single `plan` row -- measured, and the reason this test asserts on the
    stage SET rather than only on the total: a total is identical either way, so it
    could not tell the wired case from the unwired one.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)

    def _stub(system_prompt, user_prompt):
        llm._record_usage(llm._usage_from_metrics(
            _FakeMetrics({"inputTokens": len(system_prompt + user_prompt) // 4,
                          "outputTokens": 420, "totalTokens": 0}),
            MODEL,
        ))
        # Unparseable on purpose: every agent falls back to its fixture, so the run
        # reaches `promoted` deterministically AND the E7 fixture rows are exercised
        # on the real path rather than in isolation.
        return "not json"

    monkeypatch.setattr(llm, "_complete", _stub)

    # THE PENDING ONE-LINE WIRING, installed here. See the docstring.
    stage_of = {"planner": "plan", "developer": "develop", "reviewer": "review",
                "security": "security", "sre": "sre"}
    real_call = agent_client.call_agent

    def _attributing_call(role, state, **kwargs):
        result = real_call(role, state, **kwargs)
        llm.attribute_usage_to(stage_of[role])
        return result

    monkeypatch.setattr(agent_client, "call_agent", _attributing_call)
    monkeypatch.setattr(graph.agent_client, "call_agent", _attributing_call)

    llm.reset_source()
    state = graph.run_pipeline(ticket_id="7", ticket_text="Rate-limit login.",
                               poisoned=False, auto_approve=True)
    state.cost = cost.build_cost_record()

    assert state.status == "promoted", f"the probe run ended {state.status}"
    recorded = {row.stage for row in state.cost.stages}
    assert recorded == {"plan", "develop", "review", "security", "sre"}, (
        f"only {sorted(recorded)} recorded a cost. Every stage that calls the model "
        f"must appear, or the per-stage question this record exists to answer -- "
        f"'which stage is expensive' -- cannot be answered."
    )
    assert state.cost.usd is not None and state.cost.usd > 0, (
        f"a run that made model calls reported {state.cost.usd!r}"
    )
    assert any(row.fixture for row in llm.usage()), (
        "no fixture row was recorded on a run where every agent fell back; E7's "
        "zero-rather-than-nothing guarantee is not reaching the real path"
    )



# ── E3's WIRING, pinned over the AST — added by the integrator, not Lane E ────
#
# Lane E built the mechanism and could not wire it: `agents/server.py` and
# `common/agent_client.py` are integrator-owned. Its tests above exercise
# `usage_payload` / `absorb_usage_payload` thoroughly -- and NONE of them touches the
# two call sites, so deleting both lines left the whole suite green. On the remote path
# that is a run reporting zero cost because nobody asked, indistinguishable from a run
# that genuinely spent nothing.
#
# That is this repository's signature defect: a check that cannot tell "did not run"
# from "passed". The two tests below are the smallest thing that can tell them apart.

def test_the_server_puts_usage_on_the_two_hundred_envelope():
    """`agents/server.py` must send the key. Asserted over the AST, not the source text.

    A substring check for `usage_payload` would be satisfied by the COMMENT explaining
    why the key is there -- and CLAUDE.md records that exact failure twice in one lane,
    in a codebase that is roughly 40% commentary. So this walks the dict literal and
    reads its keys.
    """
    import ast
    import pathlib

    source = pathlib.Path("agentorg/agents/server.py").read_text()
    tree = ast.parse(source)

    envelopes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and {k.value for k in node.keys if isinstance(k, ast.Constant)} >= {"agent", "result"}
    ]
    assert envelopes, (
        "no dict literal in server.py carries both 'agent' and 'result'; the 200 "
        "envelope has moved and this test would pin nothing"
    )

    keys = {k.value for env in envelopes for k in env.keys if isinstance(k, ast.Constant)}
    assert "usage" in keys, (
        f"the 200 envelope sends {sorted(keys)} and not 'usage'. Under REMOTE_AGENTS "
        f"the model call happens in the container, so without this key the runner "
        f"records no tokens at all -- and a zero-cost run reads as a free one."
    )


def test_the_client_absorbs_usage_before_it_validates():
    """`agent_client` must call `absorb_usage_payload`, and BEFORE `_validate`.

    Order is the requirement, not the call. A container that answered honestly and then
    failed validation still spent those tokens; absorbing afterwards drops them for
    exactly the runs worth investigating. Same shape as
    `test_the_sre_stage_measures_ci_before_invoking_the_agent`, and pinned the same way
    -- over the AST, because a substring check is satisfied by the comment above the
    call.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("agentorg/common/agent_client.py").read_text())

    absorb_lines, validate_lines = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", ""))
        if name == "absorb_usage_payload":
            absorb_lines.append(node.lineno)
        elif name == "_validate":
            validate_lines.append(node.lineno)

    assert absorb_lines, (
        "agent_client never calls llm.absorb_usage_payload, so the container's token "
        "counts are dropped on arrival and every remote run reports zero cost"
    )
    assert validate_lines, (
        "no _validate call found; this test's ordering assertion would be vacuous"
    )
    assert min(absorb_lines) < min(validate_lines), (
        f"absorb_usage_payload is at line {min(absorb_lines)}, after _validate at "
        f"{min(validate_lines)}. A response that fails validation still cost tokens; "
        f"absorbing afterwards loses them for the runs most worth investigating."
    )
