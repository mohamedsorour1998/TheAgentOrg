"""DORA metrics runner. Owner: Aya.

Runs one ticket through one path and returns one row of raw metrics. Consumed by
test_dora_harness.py and by tests/dora_batch.py, which builds the deck table.

THREE THINGS THIS FILE DOES DIFFERENTLY FROM THE WEEK-2 SPEC, each measured:

  1. The baseline is run with the SAME `poisoned` flag as the Agent Org path.
     The spec called `run_baseline(ticket_text)` with no flag, which produces the
     CLEAN diff, and then reported bad_change_shipped=True for it because the
     row's `poisoned` field said so. Measured, both calls under pytest:

         run_baseline(T)                 -> POISON_KEY in state.dev.diff: False
         run_baseline(T, poisoned=True)  -> POISON_KEY in state.dev.diff: True

     So the spec would have put "shipped a poisoned change" against a diff
     carrying no secret -- a fabricated number in the left-hand column of the
     judged comparison. `poisoned` is threaded through instead.

  2. `step_count` is not `len(log.read(run_id))` for the baseline. run_baseline
     writes NO log, so that expression returns 0, which a judge reads as
     "no data" rather than "no checks". Measured under pytest:

         CLEAN,    promoted -> log_events=14
         POISONED, blocked  -> log_events= 9
         baseline, either   -> log_events= 0

     Note the Agent Org's count depends on the OUTCOME, not just the path: a
     blocked run stops after security, so it logs fewer events than a promoted
     one. Nothing here hardcodes either number. The baseline's step count is
     counted from the stages it actually ran, and `checks_run` carries the
     number that makes the contrast legible: how many CHECKS each path applied.

  3. Every row records its scanner PROVENANCE. Both modes block the poisoned
     ticket with blocking=2, so a table that does not say which mode produced it
     is reporting two different claims under one number. See tests/provenance.py.

WHY `lead_time_s` IS MEASURED BY THIS CALLER AND NOT READ OFF THE STATE.
`RunState.started_at` exists but is an ISO-8601 string with no `finished_at`
counterpart, so no duration can be derived from a returned state.
`agentorg/state.py` is a frozen contract, so the fix is to time the call here
with `time.perf_counter()` rather than to add a field.

IMPORT PATH CONSTRAINT: `tests/` has no `__init__.py`; `pyproject.toml` sets
`pythonpath = ["."]`, which makes `from tests.dora_runner import ...` work under
pytest and under `python`/`python -m` from the repository ROOT, but not from any
other cwd.

COST REGIME, AND IT IS A 100x DIFFERENCE. Under pytest, conftest's autouse llm
guard binds and one `run_pipeline` call costs milliseconds. That guard is a
pytest fixture, so it does NOT bind when this module is driven from a bare
`python -c`; there the model path is live and one call takes SECONDS. Any
outside-of-pytest driver should set `LLM_DISABLED=true` in the environment.
"""

import time
from dataclasses import asdict, dataclass

from agentorg import log
from agentorg.graph import run_pipeline
from tests import provenance as prov
from tests.test_baseline import run_baseline

# The stages the Agent Org path applies that the baseline does not. Counted
# rather than derived, because the point of the DORA table is the CONTRAST and a
# derived number would move silently if a stage were added.
AGENT_ORG_CHECKS = ("review", "security", "gate1", "gate2", "gate3", "sre")
BASELINE_CHECKS = ()

# Decimal places for lead_time_s. SIX, not the four the week-2 spec used, and the
# reason is measured rather than stylistic. Over 40 baseline calls under pytest:
#
#     min=0.000039459s  max=0.001188250s
#     round(d, 4) == 0.0 for 31 of 40 calls;  round(d, 6) == 0.0 for 0 of 40
#
# At four places the majority of baseline rows would report a lead time of 0.0 --
# defect 2's failure mode in a different column, a real measurement that reads as
# missing data. Six places keeps the fast path non-zero and stays far above
# perf_counter's resolution. Pinned by
# test_the_baseline_lead_time_is_not_rounded_away_to_zero.
_LEAD_TIME_PLACES = 6


@dataclass(frozen=True)
class DoraRow:
    """One measured run. Frozen: a row is evidence, and evidence is not edited.

    `checks_run` and `provenance` live HERE, on a test-local dataclass, and not
    on `agentorg.state.RunState`, which is a frozen contract this plan does not
    extend.
    """

    ticket_id: str
    path: str            # "baseline" | "agent_org"
    poisoned: bool
    final_status: str    # RunState.status
    bad_change_shipped: bool
    step_count: int
    lead_time_s: float
    checks_run: int
    provenance: str      # "fixture" | "real_scanners" | "n/a" | "unknown"


def _step_count(run_id: str) -> int:
    """One row per logged event in the append-only log.

    Correct for the Agent Org path and WRONG for the baseline, which writes no
    log at all -- see defect 2 in the module docstring. run_baseline_path counts
    its stages from the state instead.
    """
    return len(log.read(run_id))


def run_agent_org(ticket_id: str, ticket_text: str, poisoned: bool) -> DoraRow:
    """The full pipeline: five agents, three gates, the deterministic block rule."""
    t0 = time.perf_counter()
    state = run_pipeline(ticket_id, ticket_text, poisoned=poisoned)
    lead = time.perf_counter() - t0

    # A bad change "ships" only if a poisoned ticket ended promoted. A clean
    # ticket ending promoted is the correct outcome, not a shipped defect.
    shipped = poisoned and state.status == "promoted"

    try:
        answered_by_fixture = prov.answered_from_fixture(state)
        provenance = "fixture" if answered_by_fixture else "real_scanners"
    except RuntimeError:
        # provenance.py raises rather than guessing when its two signals
        # disagree. Record that instead of a plausible label -- an unknown
        # provenance is information; a wrong one is a fabricated metric.
        provenance = "unknown"

    return DoraRow(
        ticket_id=ticket_id,
        path="agent_org",
        poisoned=poisoned,
        final_status=state.status,
        bad_change_shipped=shipped,
        step_count=_step_count(state.run_id),
        lead_time_s=round(lead, _LEAD_TIME_PLACES),
        checks_run=len(AGENT_ORG_CHECKS),
        provenance=provenance,
    )


def run_baseline_path(ticket_id: str, ticket_text: str, poisoned: bool) -> DoraRow:
    """The no-checks path: plan -> develop -> merge. Reem owns run_baseline.

    `poisoned` is PASSED THROUGH. The week-2 spec omitted it, which ran the clean
    diff and then labelled the row as having shipped poison. See this module's
    docstring.
    """
    t0 = time.perf_counter()
    state = run_baseline(ticket_text, poisoned=poisoned)
    lead = time.perf_counter() - t0

    shipped = poisoned and state.status == "promoted"

    # Two stages ran -- plan and develop -- and neither is logged, because the
    # baseline has no log. Counted from the state rather than read from a log
    # that does not exist, so the number is 2 rather than a misleading 0.
    #
    # The alternative was to give run_baseline a log so both paths could use
    # _step_count. Rejected: it edits another lane's file to serve this table,
    # adds a write per stage to the gitignored scratch run directory, and makes
    # the baseline look more instrumented than the "no checks, no ceremony"
    # thing it exists to represent. The misreading was in the consumer, so the
    # fix belongs in the consumer.
    steps = sum(1 for result in (state.plan, state.dev) if result is not None)

    return DoraRow(
        ticket_id=ticket_id,
        path="baseline",
        poisoned=poisoned,
        final_status=state.status,
        bad_change_shipped=shipped,
        step_count=steps,
        lead_time_s=round(lead, _LEAD_TIME_PLACES),
        checks_run=len(BASELINE_CHECKS),
        # The baseline never runs a scanner, so provenance does not apply. Saying
        # "n/a" rather than "fixture" keeps the column honest.
        provenance="n/a",
    )


def rows_to_dicts(rows: list[DoraRow]) -> list[dict]:
    """JSON-serialisable form, for tests/dora_batch.py's report file."""
    return [asdict(row) for row in rows]
