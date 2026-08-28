"""G6 — the flake policy, and why a quarantined test's absence must be REPORTED.

THE FAILURE MODE IS SOCIAL, NOT TECHNICAL, and the requirement follows from it: a
flaky blocking test gets disabled by the first person it inconveniences, and after
that the gate is theatre. It reports green because nothing is left to run — this
repository's signature defect, a check that cannot distinguish "did not run" from
"passed", arriving through a human decision rather than a code path.

So the policy has two halves and both are tested here:

  the RETRY      one, exactly, and a result that disagrees with itself is neither
                 a pass nor a failure
  the REPORT     every quarantined test named in the artifact a human reads, and an
                 explicit "none are quarantined" when there are none

The two quarantine reasons stay apart for exactly `scan_provenance`'s reason:
`flaky` is a FAULT in the test, `chosen` is a human's CHOICE. Collapsing them hides a
genuinely broken generated test behind a decision somebody made last month, the way
collapsing `fixture-fallback` into `fixture-stub` would hide a broken scanner behind a
demo setting.
"""

from __future__ import annotations

import subprocess

import pytest

from agentorg import repo_snapshot
from agentorg.agents import testgen
from agentorg.state import PlanResult, RunState

ONE_TEST = "def test_x():\n    assert True\n"


def _completed(returncode: int, stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["pytest"], returncode=returncode, stdout=stdout, stderr="")


class FlakyRunner:
    """A runner that FAILS the first time and PASSES the second.

    THE DOUBLE THAT MAKES THIS TESTABLE AT ALL. A stub that always succeeds cannot
    express flake, so no test written against it could tell a retry policy from its
    absence -- CLAUDE.md's eleven-instance pattern, and the one place it would matter
    most here.
    """

    def __init__(self, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, workdir, paths) -> subprocess.CompletedProcess:
        self.calls += 1
        if self.calls <= self.fail_times:
            return _completed(1, "1 failed, 1 passed in 0.1s")
        return _completed(0, "2 passed in 0.1s")


@pytest.fixture()
def state(monkeypatch):
    monkeypatch.setattr(repo_snapshot, "snapshot", lambda: {"app/auth.py": "pass\n"})
    return RunState(
        run_id="g6", ticket_id="1", ticket_text="Rate-limit login",
        plan=PlanResult(tasks=["t"], acceptance_criteria=["429 past the limit"],
                        target_files=["app/auth.py"]),
    )


@pytest.fixture()
def one_file(monkeypatch):
    monkeypatch.setattr(
        testgen.llm, "structured",
        lambda *a, **k: testgen.TestPlan(
            files=[testgen.GeneratedFile(path="tests/test_gen.py", content=ONE_TEST)],
            notes="",
        ),
    )


# ── the retry ──────────────────────────────────────────────────────────────────

def test_a_failing_run_is_retried_exactly_once(state, one_file, tmp_path):
    """ONE retry. Not zero, not three, and the count is asserted.

    Zero makes an infrastructure hiccup indistinguishable from a real failure. Three
    raises the chance of turning a genuine intermittent failure green, and an
    intermittent failure in a security-adjacent pipeline is information.
    """
    runner = FlakyRunner(fail_times=99)  # never recovers
    testgen.run(state, workdir=tmp_path, runner=runner)

    assert runner.calls == 2, (
        f"the runner was called {runner.calls} times. Exactly two -- the run and one "
        f"retry -- is the policy; more retries buy a green run at the cost of the signal."
    )


def test_a_run_that_passes_first_time_is_not_retried(state, one_file, tmp_path):
    """A green run must not be re-run. Otherwise the suite pays double for every pass."""
    calls = []
    testgen.run(state, workdir=tmp_path,
                runner=lambda wd, p: (calls.append(1), _completed(0, "2 passed in 0.1s"))[1])

    assert len(calls) == 1, f"a passing run was executed {len(calls)} times"


def test_a_result_that_disagrees_with_itself_is_neither_a_pass_nor_a_failure(state, one_file, tmp_path):
    """Failed, then passed. That is a FLAKE, and it gets its own answer.

    Counting it as a failure blocks on evidence that did not reproduce. Counting it as
    a pass claims a check succeeded when the run disagreed with itself. Both are
    dishonest in different directions, so it is reported as a third thing and `binding`
    stays False.
    """
    runner = FlakyRunner(fail_times=1)
    result = testgen.run(state, workdir=tmp_path, runner=runner)

    assert runner.calls == 2
    assert result.failed == 0, (
        f"a flaky result was recorded as a failure ({result.failed}), which blocks on "
        f"evidence that did not reproduce"
    )
    assert result.binding is False, "a flaky result is binding; it did not reproduce"
    assert testgen.QUARANTINE_FLAKY in result.notes, (
        f"the flake is not reported at all, so it is indistinguishable from a clean "
        f"pass. notes={result.notes!r}"
    )


def test_a_run_that_fails_twice_is_a_fact(state, one_file, tmp_path):
    """The retry must not be able to rescue a genuine failure. Two failures bind."""
    runner = FlakyRunner(fail_times=2)
    result = testgen.run(state, workdir=tmp_path, runner=runner)

    assert runner.calls == 2
    assert result.failed >= 1, "two consecutive failures were not recorded as a failure"
    assert result.binding is True, (
        "a test that failed twice is not binding. The retry exists to absorb a hiccup, "
        "not to absorb a result."
    )
    assert testgen.QUARANTINE_FLAKY not in result.notes, (
        "a reproducible failure was labelled flaky, which is how a real failure gets "
        "explained away"
    )


# ── the report ─────────────────────────────────────────────────────────────────

def test_a_quarantined_test_is_NAMED_in_the_artifact_a_human_reads(monkeypatch):
    """A human's chosen quarantine must appear in the notes, by name and with a reason.

    A quarantine nobody can enumerate cannot be reported, which is why `QUARANTINED` is
    a named dict rather than a `-k 'not ...'` string in a workflow.
    """
    monkeypatch.setitem(testgen.QUARANTINED, "tests/test_gen.py::test_flappy",
                        "intermittent on the shared runner; see issue 91")

    note = testgen._quarantine_note([])

    assert "tests/test_gen.py::test_flappy" in note, f"the quarantined test is not named: {note!r}"
    assert "issue 91" in note, f"the reason is not reported: {note!r}"
    assert testgen.QUARANTINE_CHOSEN in note, f"the quarantine's KIND is not reported: {note!r}"


def test_the_two_quarantine_reasons_do_not_share_a_representation(monkeypatch):
    """FAULT and CHOICE must be distinguishable in the report. `scan_provenance`'s rule.

    A flaky test is a fault in the test; a chosen one is a human decision. One spelling
    for both hides a broken generated test behind last month's decision -- the same
    reason `fixture-fallback` and `fixture-stub` stay apart.
    """
    assert testgen.QUARANTINE_FLAKY != testgen.QUARANTINE_CHOSEN, (
        "the two quarantine kinds share a spelling, so a fault reads as a choice"
    )

    monkeypatch.setitem(testgen.QUARANTINED, "tests/test_a.py::chosen_one", "a human said so")
    note = testgen._quarantine_note(["tests/test_b.py"])

    assert testgen.QUARANTINE_FLAKY in note and testgen.QUARANTINE_CHOSEN in note, (
        f"a report carrying both kinds does not distinguish them: {note!r}"
    )
    assert "tests/test_a.py::chosen_one" in note and "tests/test_b.py" in note


def test_an_empty_quarantine_says_so_rather_than_saying_nothing():
    """"none are quarantined" is WRITTEN, not left as an empty string.

    An absent line and a line saying nothing was excluded are different facts, and only
    one is checkable. A reader who sees nothing cannot tell whether the policy ran --
    which is precisely the ambiguity this whole feature is supposed to remove.
    """
    assert not testgen.QUARANTINED, (
        "QUARANTINED is non-empty at import, so this test is measuring another test's "
        "leftover state"
    )

    note = testgen._quarantine_note([])

    assert note.strip(), "an empty quarantine produced an empty report; silence is the defect"
    assert "no tests are quarantined" in note, f"the empty case does not state itself: {note!r}"


def test_every_run_carries_a_quarantine_report_whatever_the_outcome(state, one_file, tmp_path, monkeypatch):
    """Green, red, flaky and missing must ALL report the quarantine state.

    Reporting it only on the interesting paths is the same gap one level up: a reader
    of a green run could not tell whether three blocking tests had been quietly removed.
    """
    monkeypatch.setattr(testgen.llm, "structured", lambda *a, **k: None)
    missing = testgen.run(state)

    monkeypatch.undo()
    monkeypatch.setattr(repo_snapshot, "snapshot", lambda: {"app/auth.py": "pass\n"})
    monkeypatch.setattr(
        testgen.llm, "structured",
        lambda *a, **k: testgen.TestPlan(
            files=[testgen.GeneratedFile(path="tests/test_gen.py", content=ONE_TEST)], notes=""),
    )
    green = testgen.run(state, workdir=tmp_path, runner=lambda wd, p: _completed(0, "2 passed"))
    red = testgen.run(state, workdir=tmp_path, runner=lambda wd, p: _completed(1, "1 failed, 1 passed"))
    flaky = testgen.run(state, workdir=tmp_path, runner=FlakyRunner(fail_times=1))

    for label, result in [("missing", missing), ("green", green), ("red", red), ("flaky", flaky)]:
        assert "quarantin" in result.notes.lower(), (
            f"the {label} path reports no quarantine state, so a reader cannot tell "
            f"whether a blocking test was quietly removed. notes={result.notes!r}"
        )
