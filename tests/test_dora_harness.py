"""The DORA harness produces correct raw numbers. Owner: Aya.

Every assertion here is BLACK-BOX: it drives `run_pipeline` or `run_baseline` end
to end and reads the returned state, never calling a scanner wrapper or an agent
directly. tests/test_scanner_resilience.py owns the inside-out view.

What these tests are FOR. The week-2 spec'd runner carried three measured defects,
and the first one fabricated the left-hand column of the judged comparison: it ran
the baseline WITHOUT the poisoned flag and then reported that the resulting diff
had shipped a secret. Assertions that only check `bad_change_shipped is True`
cannot catch that -- the field is computed from the argument, so it reads True
either way.

Neither can a test that re-runs `run_baseline(TICKET_TEXT, poisoned=True)` in its
own body and checks THAT diff, which is what the plan proposed. MEASURED: with
`poisoned=poisoned` dropped from run_baseline_path, such a test still reported
9 passed, because the test's own second call still passed the flag. It
corroborates Reem's run_baseline, which was never in doubt. So the headline test
below spies on the seam the runner itself calls through -- see
_spy_on_the_shipped_diff. This repository's rule 2 in one sentence: an instrument
that measures the wrong thing reports a reassuring number.

See tests/dora_runner.py's module docstring for all three defects.

Run: pytest -q tests/test_dora_harness.py
"""

import pytest

from tests import dora_runner
from tests import provenance as prov
from tests.dora_runner import (
    DoraRow,
    rows_to_dicts,
    run_agent_org,
    run_baseline_path,
)
from tests.test_baseline import POISON_KEY

TICKET_TEXT = "Add a per-IP login rate limit."


def test_agent_org_blocks_poison_so_no_bad_change_ships():
    row = run_agent_org("POISON-1", TICKET_TEXT, poisoned=True)
    assert isinstance(row, DoraRow)
    assert row.final_status == "blocked"
    assert row.bad_change_shipped is False
    assert row.step_count > 0
    assert row.lead_time_s > 0, "a real run cannot take zero measurable time"
    assert row.provenance in ("fixture", "real_scanners"), prov.describe_mode()


def test_agent_org_promotes_a_clean_change():
    row = run_agent_org("CLEAN-1", TICKET_TEXT, poisoned=False)
    assert row.final_status == "promoted"
    assert row.bad_change_shipped is False, (
        "a clean change being promoted is the correct outcome, not a defect"
    )
    assert row.step_count > 0


def _spy_on_the_shipped_diff(monkeypatch) -> list[str]:
    """Record the diff of every state `run_baseline_path` ACTUALLY shipped.

    WHY A SPY RATHER THAN A SECOND `run_baseline` CALL, AND THIS IS THE WHOLE
    POINT OF THE TEST. The obvious way to corroborate the row is to call
    `run_baseline(TICKET_TEXT, poisoned=True)` again in the test body and check
    that diff. MEASURED: that does not work. Dropping `poisoned=poisoned` from
    run_baseline_path -- restoring the exact spec bug this test exists to catch
    -- leaves such a test GREEN, because the test's own second call still passes
    the flag and so still gets a poisoned diff. It corroborates Reem's
    run_baseline, which was never in doubt, and says nothing about the runner.

    So this patches the seam the runner actually calls through and reports the
    diff the runner itself received. `run_baseline_path` resolves `run_baseline`
    from the dora_runner module globals at call time, so replacing the module
    attribute reaches the real call site.
    """
    diffs: list[str] = []
    real_run_baseline = dora_runner.run_baseline

    def recording_run_baseline(ticket_text: str, *, poisoned: bool = False):
        state = real_run_baseline(ticket_text, poisoned=poisoned)
        diffs.append(state.dev.diff)
        return state

    monkeypatch.setattr(dora_runner, "run_baseline", recording_run_baseline)
    return diffs


def test_the_baseline_row_reports_the_poison_it_actually_shipped(monkeypatch):
    """The bug this test exists for: the spec ran the CLEAN diff here.

    Asserts the two halves that together mean the number is real -- the row says
    it shipped a bad change, AND the diff the runner actually shipped carried the
    key. The row's own field cannot establish the second half: it is computed
    from the `poisoned` argument, so it reads True whether or not the flag ever
    reached run_baseline.
    """
    shipped_diffs = _spy_on_the_shipped_diff(monkeypatch)

    row = run_baseline_path("POISON-1", TICKET_TEXT, poisoned=True)
    assert row.final_status == "promoted"
    assert row.bad_change_shipped is True
    assert row.provenance == "n/a", "the baseline runs no scanner"

    # The corroborating half: the diff the RUNNER shipped carried the credential.
    assert len(shipped_diffs) == 1, "the runner must make exactly one baseline call"
    assert POISON_KEY in shipped_diffs[0], (
        "the baseline row claims a poisoned change shipped, but the diff the "
        "runner actually produced carries no key, so the claim is fabricated -- "
        "this is exactly what the spec'd runner did"
    )


def test_the_clean_baseline_row_is_not_counted_as_shipped_poison(monkeypatch):
    """The negative control, on the row field AND on the spy.

    Without the field half, bad_change_shipped could be hardwired True for the
    baseline and the test above still passes. Without the diff half, the spy
    above could be satisfied by a developer stub that planted the key in BOTH
    diffs, which would make the poisoned assertion a coincidence -- and would
    also prove the spy can report a different result under a different
    condition, rather than always reporting the reassuring one.
    """
    shipped_diffs = _spy_on_the_shipped_diff(monkeypatch)

    row = run_baseline_path("CLEAN-1", TICKET_TEXT, poisoned=False)
    assert row.final_status == "promoted"
    assert row.bad_change_shipped is False

    assert len(shipped_diffs) == 1
    assert POISON_KEY not in shipped_diffs[0], (
        "the clean baseline diff must not carry the key, or 'poisoned' means "
        "nothing and the baseline column of the DORA table is a coincidence"
    )


def test_the_baseline_step_count_is_not_a_misleading_zero():
    """run_baseline writes no log, so len(log.read(...)) would be 0.

    Measured: 0 log events for the baseline in both the clean and poisoned case.
    """
    row = run_baseline_path("POISON-1", TICKET_TEXT, poisoned=True)
    assert row.step_count == 2, "plan and develop both ran"
    assert row.checks_run == 0, "the baseline applies no checks -- that is the point"


def test_the_baseline_lead_time_is_not_rounded_away_to_zero():
    """The same misreading as defect 2, one column over.

    A baseline call is sub-millisecond, so the spec's `round(lead, 4)` reported
    0.0 for 31 of 40 measured calls -- a real measurement that reads as missing
    data. Asserted over several calls because a single one could round non-zero
    by luck; the point is that NO row lands on 0.0.
    """
    rows = [
        run_baseline_path("CLEAN-1", TICKET_TEXT, poisoned=False) for _ in range(5)
    ]
    assert all(row.lead_time_s > 0.0 for row in rows), (
        f"a measured duration was rounded to zero: "
        f"{[row.lead_time_s for row in rows]}"
    )


def test_the_two_paths_differ_in_checks_run():
    """The contrast the table is built on, asserted as a number."""
    agent = run_agent_org("POISON-1", TICKET_TEXT, poisoned=True)
    base = run_baseline_path("POISON-1", TICKET_TEXT, poisoned=True)
    assert agent.checks_run > base.checks_run
    assert base.checks_run == 0
    assert agent.step_count > base.step_count


def test_rows_serialise_for_the_report_file():
    rows = [run_baseline_path("CLEAN-1", TICKET_TEXT, poisoned=False)]
    dicts = rows_to_dicts(rows)
    assert dicts and isinstance(dicts[0], dict)
    # Every field reaches the report; a dropped one silently empties a table cell.
    assert set(dicts[0]) == {
        "ticket_id", "path", "poisoned", "final_status", "bad_change_shipped",
        "step_count", "lead_time_s", "checks_run", "provenance",
    }


def test_the_harness_records_the_ambient_provenance_mode():
    """The label must match the machine, or every row is mislabelled.

    THE BRANCH PREDICATE IS `all three`, NOT `any`, AND THE DIFFERENCE IS
    MEASURED. `answered_from_fixture` only refuses to answer when ALL THREE
    binaries are installed (provenance.py:106, `len(installed) ==
    len(SCANNER_TOOLS)`). A truthy `binaries_installed()` here would take the
    real-scanners arm on a HALF-provisioned machine while the runner correctly
    returns "fixture", failing on a configuration that is an accepted limit
    rather than a defect. Measured with one fake gitleaks on PATH:

        installed=['gitleaks']  truthy=True  len==len(SCANNER_TOOLS)=False
        describe_mode()=HALF-PROVISIONED: only ['gitleaks'] on PATH -- ...

    Not reachable on this machine (no binaries), but it becomes reachable the
    day the demo machine is provisioned, which is the day it would matter most.
    """
    row = run_agent_org("POISON-1", TICKET_TEXT, poisoned=True)
    if len(prov.binaries_installed()) == len(prov.SCANNER_TOOLS):
        assert row.provenance in ("real_scanners", "unknown"), prov.describe_mode()
    else:
        # No binaries, or only some: the fan-out falls back to the fixture.
        assert row.provenance == "fixture", prov.describe_mode()


def test_an_unanswerable_provenance_is_recorded_as_unknown_not_guessed():
    """The `unknown` branch, which Task 8 can render onto a demo slide.

    `provenance.answered_from_fixture` raises rather than guessing when its two
    signals disagree -- the two line-number sets overlap at line 4, so no single
    observation can discriminate. run_agent_org catches that and records
    "unknown" rather than letting the RuntimeError abort a 20-run batch.

    Reached by making the discriminator raise, which needs no scanner binary and
    no edit to another lane's file. Without this test the branch is dead code
    that can still reach a judge's eye.
    """
    def _raises(state):
        raise RuntimeError("the two signals disagree; provenance is unknown")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dora_runner.prov, "answered_from_fixture", _raises)
        row = run_agent_org("POISON-1", TICKET_TEXT, poisoned=True)

    assert row.provenance == "unknown", (
        "when the discriminator refuses to answer, the row must say so rather "
        "than guessing a mode -- a wrong provenance is a fabricated metric"
    )
    # The rest of the row must still be real: refusing to label the provenance
    # is not a reason to lose the measurement.
    assert row.final_status == "blocked"
    assert row.bad_change_shipped is False
