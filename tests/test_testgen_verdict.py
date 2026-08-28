"""G5, G6, G7 — what a generated test result is allowed to MEAN.

Three claims, and they are one idea:

  G5  a FAILING generated test is binding; a MISSING one is advisory
  G6  a quarantined test's absence is REPORTED, never silent
  G7  a GREEN generated test must not be quotable as proof of correctness

`GeneratedTests` already encodes G5 and G7 by carrying `passed`, `failed` and a
separate `binding` — two fields rather than one verdict, because one could not hold
the distinction. `tests/test_final_contract.py` pins the model; this file pins the
AGENT's use of it, which is where the distinction can be lost.

THE EXECUTION SEAM IS A PARAMETER, NOT A PATCH. `testgen.run(state, workdir, runner=)`
takes the pytest runner as an argument, so these tests can express a failing run, a
run that disagrees with itself, and a crashing run. A double that could only express
success is this repository's most-repeated defect (CLAUDE.md: eleven instances), and it
is precisely the mistake that would matter here — the failing case is the only one
whose result is binding.
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


@pytest.fixture()
def state(monkeypatch):
    monkeypatch.setattr(repo_snapshot, "snapshot", lambda: {"app/auth.py": "pass\n"})
    return RunState(
        run_id="g5", ticket_id="1", ticket_text="Rate-limit login",
        plan=PlanResult(tasks=["t"], acceptance_criteria=["429 past the limit"],
                        target_files=["app/auth.py"]),
    )


@pytest.fixture()
def one_file(monkeypatch):
    """A model that answers with exactly one test file. Returns the plan it hands back."""
    plan = testgen.TestPlan(
        files=[testgen.GeneratedFile(path="tests/test_gen.py", content=ONE_TEST)],
        notes="covered the 429 case",
    )
    monkeypatch.setattr(testgen.llm, "structured", lambda *a, **k: plan)
    return plan


# ── G5: the binding rule ───────────────────────────────────────────────────────

def test_a_failing_generated_test_is_binding(state, one_file, tmp_path):
    """A failure is a FACT: something ran and disagreed with the acceptance criteria."""
    result = testgen.run(
        state, workdir=tmp_path,
        runner=lambda wd, paths: _completed(1, "1 failed, 2 passed in 0.1s"),
    )

    assert result.failed == 1, f"the failure count was not measured: {result!r}"
    assert result.binding is True, (
        "a failing generated test is not binding. A failure is the one observation "
        "this feature can make that is a fact rather than an absence."
    )


def test_a_passing_generated_test_is_NOT_binding(state, one_file, tmp_path):
    """G7, in the field that carries it. Green must not block, and must not be proof."""
    result = testgen.run(
        state, workdir=tmp_path,
        runner=lambda wd, paths: _completed(0, "3 passed in 0.1s"),
    )

    assert result.passed == 3
    assert result.failed == 0
    assert result.binding is False, (
        "a passing generated test was marked binding. It proves the generator produced "
        "something that RAN -- nothing about correctness."
    )


def test_a_MISSING_generated_test_is_advisory_and_says_so(state, monkeypatch):
    """No model answered. That must not block, and must not read as a green run.

    THE DIRECTION IS THE WHOLE POINT, and the wrong one is easy to defend in review:
    `binding = not passed` would make the GENERATOR's failure to produce anything into
    a block on somebody else's change. The feature would then acquire a reputation for
    false alarms, and a feature with that reputation gets switched off -- which is G6's
    social failure mode arriving through G5.
    """
    monkeypatch.setattr(testgen.llm, "structured", lambda *a, **k: None)

    result = testgen.run(state)

    assert result.binding is False, "a missing generated test blocked the run; it is ADVISORY"
    assert result.passed == 0
    assert result.failed == 0
    assert result.source == testgen.SOURCE_FIXTURE
    assert "ADVISORY" in result.notes, (
        f"the result does not SAY a missing test is advisory, so passed=0 failed=0 "
        f"reads identically to a green run. notes={result.notes!r}"
    )


def test_the_binding_decision_lives_in_one_comparison():
    """`_binding` is `failed > 0`, driven directly across the boundary.

    Three spellings read as reasonable and two are wrong: `not passed` blocks a run
    that generated nothing, and `failed >= 0` blocks every run including green. Both
    are one character from the correct one.
    """
    assert testgen._binding(0) is False
    assert testgen._binding(1) is True
    assert testgen._binding(99) is True


def test_a_generation_that_was_never_executed_does_not_report_a_green_run(state, one_file):
    """`workdir=None` means generated-but-not-run, and the counts are not measurements.

    This is `ci_status_measured`'s distinction one layer out: `passed=0, failed=0` is
    the same tuple a genuinely green zero-test run would produce, so the notes must
    carry the fact that nothing executed. Otherwise "did not run" and "passed" are
    indistinguishable, which is the defect this whole project exists to prevent.
    """
    result = testgen.run(state, workdir=None)

    assert result.files == ["tests/test_gen.py"], "the generated file was not reported"
    assert result.binding is False
    assert "NOT EXECUTED" in result.notes, (
        f"a generation that never ran does not say so; its passed=0/failed=0 reads as "
        f"a green run. notes={result.notes!r}"
    )


# ── G7: green is not proof ─────────────────────────────────────────────────────

def test_a_green_run_states_what_it_does_NOT_prove(state, one_file, tmp_path):
    """The rendered result must carry the caveat, not just the number.

    Nobody reads `passed=3` as "a model wrote three assertions from a ticket". The
    person reading this artifact in six months will not remember the reasoning, so the
    artifact states it -- the same argument as `report.render` naming the zero cache
    hit rate in words because nobody reads a percentage as an alarm.
    """
    result = testgen.run(
        state, workdir=tmp_path,
        runner=lambda wd, paths: _completed(0, "3 passed in 0.1s"),
    )

    assert "not evidence" in result.notes.lower(), (
        f"a green generated-test result does not say what it fails to prove, so it can "
        f"be quoted as proof of correctness. notes={result.notes!r}"
    )
    assert testgen.GREEN_PROVES in result.notes


def test_a_red_run_says_the_opposite_and_the_two_messages_differ(state, one_file, tmp_path):
    """A failing run must NOT carry the green caveat, and vice versa.

    A single message covering both cases would be honest and useless: the caveat's
    whole function is to distinguish the two, and one string cannot.
    """
    green = testgen.run(state, workdir=tmp_path,
                        runner=lambda wd, p: _completed(0, "3 passed in 0.1s")).notes
    red = testgen.run(state, workdir=tmp_path,
                      runner=lambda wd, p: _completed(1, "1 failed, 2 passed in 0.1s")).notes

    assert "BINDING" in red, f"a failing run does not state that it is binding: {red!r}"
    assert testgen.GREEN_PROVES not in red, (
        "a FAILING run carries the 'green proves little' caveat, which undercuts the "
        "one result this feature produces that IS a fact."
    )
    assert green != red, "the green and red messages are identical, so neither says anything"
