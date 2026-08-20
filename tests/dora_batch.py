"""DORA batch: 10 baseline vs 10 Agent Org. Owner: Aya.

Produces runs/dora_batch.json -- the raw rows the week-3 deck table is built from.

    LLM_DISABLED=true python -m tests.dora_batch

ALL TEN ARE POISONED, deliberately. The headline the judges are shown is "the
Agent Org blocks the poisoned change 10/10 while the baseline ships it 10/10",
and a mixed batch would make that a 5/5 needing explanation. The clean half of
the story is told by the demo's own clean run and by test_dora_harness.py's
clean rows.

WHAT THE PROVENANCE FIELD IS FOR. Both scanner modes block the poisoned ticket
with blocking=2, so "10/10 blocked" is a claim about the deterministic rule in
one mode and a claim about reading a JSON fixture in the other. The summary
carries the mode so the number cannot be quoted without it. See
tests/provenance.py.

WHY THE ENV KNOB IS IN THE COMMAND LINE ABOVE, MEASURED. Under pytest,
conftest's autouse fixture sets config.LLM_DISABLED and every agent takes the
fixture path. That fixture is a pytest fixture, so `python -m tests.dora_batch`
runs OUTSIDE its reach and the model path is live. Measured on this machine, one
run_pipeline call:

    LLM_DISABLED=true, outside pytest      0.066 s
    no knob,           outside pytest     10.7   s   (~160x, live and billable)

Ten runs is therefore 0.7 s or nearly two minutes, and the difference buys
nothing the table reports: the model only writes the security agent's PROSE, and
compute_security_verdict has already decided the verdict before it is called --
see agentorg/agents/security.py. main() does not force the knob, because
silently rewriting config would hide which regime produced a published number;
it prints the regime instead, and warns before the batch starts rather than
after the dead air.

IMPORT PATH CONSTRAINT: `tests/` has no `__init__.py`; `pyproject.toml` sets
`pythonpath = ["."]`, which makes `import tests.dora_batch` work under pytest
and under `python -m` from the repository ROOT, but not from any other cwd.

COST, MEASURED: one run_batch() is ~0.7 s in fixture-fallback mode with the
model off (10 run_pipeline calls at ~66 ms each, plus 10 run_baseline calls at
~0.07 ms each). With the three real binaries installed each pipeline run
additionally launches three scanners, and no diff-hash cache exists in this
repository -- verified, agentorg/security/__init__.py is a bare fan-out loop --
so the real-scanner batch is ~30 scanner invocations. Run it once per session;
tests/test_dora_batch.py has a module-scoped fixture that does.
"""

import contextlib
import json
import logging
import pathlib

from tests import provenance as prov
from tests.dora_runner import DoraRow, rows_to_dicts, run_agent_org, run_baseline_path

TICKET_TEXT = "Add a per-IP login rate limit."
N = 10
OUT = pathlib.Path(__file__).resolve().parent.parent / "runs" / "dora_batch.json"

# Decimal places for avg_lead_time_s. SIX, matching dora_runner._LEAD_TIME_PLACES,
# for the same measured reason but a WEAKER version of it, and the difference is
# worth stating rather than inheriting. Over 10 baseline rows on this machine:
#
#     per-row  min=0.000047000s  max=0.001217000s
#     per-row  round(d, 4) == 0.0 for 5 of 10;  round(d, 6) == 0.0 for 0 of 10
#     average  0.000169100 -> round(avg, 4) = 0.0002,  round(avg, 6) = 0.000169
#
# So four places destroys half the PER-ROW measurements, which is dora_runner's
# reason, but the AVERAGE survives four places here at 0.0002. Averaging pulls
# the value up. Six is still the right choice -- it keeps two more significant
# figures on a quantity whose whole point is being three orders of magnitude
# below the other column, and it stops the two fields disagreeing about
# resolution -- but this constant is NOT load-bearing against a rendered 0.0 the
# way dora_runner's is. Pinned, at a lead time low enough for the difference to
# be real, by test_summarize_does_not_round_a_real_average_away_to_zero.
_LEAD_TIME_PLACES = 6


def run_batch() -> tuple[list[DoraRow], list[DoraRow]]:
    """N poisoned tickets down each path. Returns (agent_org_rows, baseline_rows)."""
    agent_rows: list[DoraRow] = []
    baseline_rows: list[DoraRow] = []
    for index in range(N):
        ticket_id = f"POISON-{index + 1}"
        agent_rows.append(run_agent_org(ticket_id, TICKET_TEXT, poisoned=True))
        baseline_rows.append(run_baseline_path(ticket_id, TICKET_TEXT, poisoned=True))
    return agent_rows, baseline_rows


def summarize(rows: list[DoraRow]) -> dict:
    """Aggregate one column. Empty input returns zeros rather than dividing by it.

    The key set is a contract: the deck builder subscripts every one of these, so
    a renamed or dropped key must raise KeyError there rather than render a wrong
    number.

    `checks_run` is read off the first row, which is correct only because every
    row of one column walks the same path and so reports the same count. That is
    an assumption this function cannot check without changing the key's type, so
    it is pinned from the outside instead, by
    test_both_columns_agree_on_how_many_checks_they_ran. `provenance` gets the
    opposite treatment -- joined rather than first-taken -- because it is a
    string and CAN carry a disagreement, and because a batch whose rows were
    decided by different mechanisms is not one measurement.
    """
    count = len(rows)
    if not count:
        return {
            "runs": 0, "bad_changes_shipped": 0, "blocked": 0, "promoted": 0,
            "avg_step_count": 0, "avg_lead_time_s": 0, "checks_run": 0,
            "provenance": "n/a",
        }

    # sorted() over a set, so "fixture+unknown" reads the same way whichever run
    # produced the odd one. `unknown` is dora_runner's honest label for a run
    # whose provenance discriminator raised; it must survive into the report
    # rather than be dropped, because a mislabelled metric reads as evidence.
    provenances = sorted({row.provenance for row in rows})
    return {
        "runs": count,
        "bad_changes_shipped": sum(1 for r in rows if r.bad_change_shipped),
        "blocked": sum(1 for r in rows if r.final_status == "blocked"),
        "promoted": sum(1 for r in rows if r.final_status == "promoted"),
        "avg_step_count": round(sum(r.step_count for r in rows) / count, 2),
        "avg_lead_time_s": round(
            sum(r.lead_time_s for r in rows) / count, _LEAD_TIME_PLACES
        ),
        "checks_run": rows[0].checks_run,
        # A batch whose rows disagree about provenance is not one measurement.
        # Reported as a joined string rather than silently taking the first.
        "provenance": "+".join(provenances),
    }


# Substring identifying the security agent's scanner-fallback WARNING. Matched on
# the format string, which is a literal in agentorg/agents/security.py and so is
# stable under any argument values.
_FALLBACK_WARNING = "falling back to the fixture verdict"


class _FallbackWarningCounter(logging.Filter):
    """Counts the scanner-fallback WARNING instead of letting all N of them print.

    WHY THIS EXISTS, AND WHY IT IS NOT "HIDING A FAILURE". In fixture-fallback
    mode every one of the N pipeline runs logs the same bounded WARNING naming
    the absent scanner. Ten identical copies of a FileNotFoundError message
    immediately above `blocked: 10` reads, on a projector, as ten crashes -- and
    this command is a demo closer.

    Nothing is discarded that the report does not already say more precisely:
    `mode` carries prov.describe_mode(), which names the provenance regime
    authoritatively and is asserted by tests, and the count of suppressed records
    is PRINTED rather than silently dropped. So the signal survives, stated once
    with its multiplicity, instead of N times unstructured. The filter matches
    one specific message and returns True for everything else, so a NEW warning
    -- the interesting kind -- still prints in full.
    """

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if _FALLBACK_WARNING in str(record.msg):
            self.count += 1
            return False
        return True


@contextlib.contextmanager
def _fallback_warnings_counted():
    """Aggregate the fallback WARNING for the duration of the batch.

    Attached to the logger rather than to a handler, because with no logging
    configuration Python uses its handler of last resort and there is no handler
    to attach to. Logger.handle applies logger-level filters before calling any
    handler, so this suppresses the record at the source. Removed in a finally,
    so importing this module never changes another caller's logging.
    """
    logger = logging.getLogger("agentorg.agents.security")
    counter = _FallbackWarningCounter()
    logger.addFilter(counter)
    try:
        yield counter
    finally:
        logger.removeFilter(counter)


def _warn_if_the_model_is_live() -> None:
    """Say so BEFORE the batch, while Ctrl-C is still cheap. See module docstring.

    Printed rather than enforced: main() must not rewrite config, or a published
    number would carry no evidence of which regime measured it.
    """
    from agentorg.common import llm

    if not llm.available():
        return
    print(
        f"WARNING: the model is live, so each of the {N} pipeline runs costs "
        f"~10.7 s (measured) instead of ~0.066 s, and is billable."
    )
    print(
        "         It changes no number in this report -- the model only writes "
        "the security agent's prose."
    )
    print("         Ctrl-C and re-run with LLM_DISABLED=true.")


def main() -> dict:
    _warn_if_the_model_is_live()
    with _fallback_warnings_counted() as fallback_warnings:
        agent_rows, baseline_rows = run_batch()
    report = {
        "mode": prov.describe_mode(),
        "agent_org": {
            "summary": summarize(agent_rows),
            "rows": rows_to_dicts(agent_rows),
        },
        "baseline": {
            "summary": summarize(baseline_rows),
            "rows": rows_to_dicts(baseline_rows),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"mode      : {report['mode']}")
    if fallback_warnings.count:
        # Reported, not dropped: N identical copies would bury the two lines
        # below on a projector, but the multiplicity is still information.
        print(
            f"            ({fallback_warnings.count} scanner-fallback warnings "
            f"aggregated; this is that mode's expected path)"
        )
    print("agent_org :", report["agent_org"]["summary"])
    print("baseline  :", report["baseline"]["summary"])
    return report


if __name__ == "__main__":
    main()
