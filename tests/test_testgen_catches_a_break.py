"""THE LANE'S "DONE WHEN": a broken change caught by a GENERATED TEST, not a scanner.

Everything else in this lane is a property. This file is the claim: a run generates
tests, executes them, and a deliberately broken change is caught by a generated test
**rather than by a scanner**.

WHY "RATHER THAN BY A SCANNER" IS THE WHOLE POINT. The three scanners find committed
credentials, known CVEs and injectable patterns. They are deterministic, they cannot be
argued with, and they are completely blind to a login handler that accepts the wrong
password -- because nothing about that is a secret or a CVE. `compute_security_verdict`
over a diff that breaks authentication returns `("pass", [])`, correctly, and the
change ships. So this file breaks authentication and asserts BOTH halves:

    the security verdict says PASS      <- the scanners have nothing to say
    the generated test says FAILED      <- and it is binding

Asserting only the second would leave the more interesting claim untested: that this
catches something the existing gate structurally cannot.

REAL PYTEST, IN A SUBPROCESS. These tests use `testgen._pytest_runner` -- the shipped
default -- not a double. Every other test file in this lane substitutes the runner to
express a specific outcome; this one must not, because "the generated test ran and
failed" is precisely the thing under test and a stub asserting it would be circular.
"""

from __future__ import annotations

import textwrap

import pytest

from agentorg import repo_snapshot
from agentorg.agents import security, testgen
from agentorg.state import DevResult, PlanResult, RunState, compute_security_verdict

# The app as it works. `authenticate` compares the stored password.
WORKING_APP = textwrap.dedent('''
    _USERS = {"alice": "wonderland"}


    def authenticate(username, password):
        if not username or not password:
            return False
        return _USERS.get(username) == password
''')

# THE DELIBERATELY BROKEN CHANGE. It accepts ANY password for a known user.
#
# Chosen because it is invisible to every layer except a test of the behaviour:
#   * gitleaks     nothing that looks like a credential
#   * trivy        no dependency, no CVE
#   * semgrep      a truthy membership test is not an injectable pattern
#   * the reviewer MIGHT catch it -- it is a model, so it might not, and its verdict
#                  is advisory either way
# It is also the single most consequential bug this file could have.
BROKEN_APP = textwrap.dedent('''
    _USERS = {"alice": "wonderland"}


    def authenticate(username, password):
        if not username or not password:
            return False
        return username in _USERS
''')

# A test written from the ACCEPTANCE CRITERION, not from either version of the app.
# This is what the generator produces; it is spelled out here so the assertion is
# about execution and reporting rather than about the model's wording.
GENERATED_TEST = textwrap.dedent('''
    from app.auth import authenticate


    def test_a_wrong_password_is_refused():
        """Criterion: only the stored password authenticates a known user."""
        assert authenticate("alice", "wonderland") is True
        assert authenticate("alice", "not-the-password") is False
''')

CRITERIA = ["Only the stored password authenticates a known user"]


def _state(diff: str = "") -> RunState:
    return RunState(
        run_id="done-when", ticket_id="1",
        ticket_text="Only the stored password may authenticate a known user",
        plan=PlanResult(tasks=["harden authenticate"], acceptance_criteria=CRITERIA,
                        target_files=["app/auth.py"]),
        dev=DevResult(branch="feat/x", diff=diff, summary="s", files_changed=["app/auth.py"]),
    )


@pytest.fixture()
def workdir(tmp_path):
    """A minimal importable app package, so a real pytest can import `app.auth`."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    return tmp_path


@pytest.fixture()
def generator(monkeypatch):
    """A model that returns the test above. The MODEL is stubbed; pytest is not.

    Stubbing the model is the correct isolation -- conftest guard 1 disables it and
    this suite must stay hermetic. What must NOT be stubbed is the execution, because
    "a generated test caught it" is the claim.
    """
    monkeypatch.setattr(repo_snapshot, "snapshot", lambda: {"app/auth.py": WORKING_APP})
    monkeypatch.setattr(
        testgen.llm, "structured",
        lambda *a, **k: testgen.TestPlan(
            files=[testgen.GeneratedFile(path="tests/test_generated.py", content=GENERATED_TEST)],
            notes="one test per criterion",
        ),
    )


def test_a_deliberately_broken_change_is_caught_by_a_generated_test(generator, workdir):
    """THE LANE'S DONE-WHEN, end to end. Real pytest, real failure, binding.

    Note what is NOT stubbed: `testgen.run` is called with the default runner, so a
    real `python -m pytest` subprocess executes the generated file against the broken
    app and its exit code is the evidence.
    """
    (workdir / "app" / "auth.py").write_text(BROKEN_APP)

    result = testgen.run(_state(), workdir=workdir)

    assert result.files == ["tests/test_generated.py"], (
        f"no test was generated, so nothing could have caught anything: {result!r}"
    )
    assert result.failed >= 1, (
        f"the generated test did not fail against a login handler that accepts ANY "
        f"password. notes={result.notes!r}"
    )
    assert result.binding is True, (
        "the generated test failed and the result is not binding, so the pipeline "
        "would not act on it"
    )
    assert result.source == testgen.SOURCE_ACCEPTANCE


def test_the_scanners_have_nothing_to_say_about_that_same_change():
    """The other half, and the reason this lane exists at all.

    `compute_security_verdict` is the five lines the whole pipeline's authority rests
    on, and it is CORRECT to pass here: a broken password comparison is not a secret,
    a CVE or an injectable pattern. This is the gap a generated test fills.
    """
    verdict, blocking = compute_security_verdict([])

    assert verdict == "pass", "the empty-findings contract changed; re-derive this test"
    assert blocking == []


def test_the_security_agent_passes_the_broken_diff_while_the_generated_test_fails(
    generator, workdir, monkeypatch
):
    """Both verdicts on ONE change, side by side. This is the argument in one test.

    The scanners are stubbed to find nothing -- which is what they genuinely find on
    this diff, and stubbing them keeps the suite hermetic on a machine without the
    three binaries. What matters is the CONTRAST, and the contrast is real: no
    arrangement of gitleaks, trivy and semgrep detects a wrong password comparison.
    """
    monkeypatch.setattr(security, "run_all_scanners", lambda *a, **k: [])
    (workdir / "app" / "auth.py").write_text(BROKEN_APP)

    broken_diff = (
        "--- a/app/auth.py\n+++ b/app/auth.py\n"
        "@@ -1,3 +1,3 @@\n-    return _USERS.get(username) == password\n"
        "+    return username in _USERS\n"
    )
    state = _state(broken_diff)

    security_result = security.run(state, use_real_scanners=True)
    generated = testgen.run(state, workdir=workdir)

    assert security_result.verdict == "pass", (
        f"the scanners blocked this diff, which would make the contrast this test "
        f"draws untrue: {security_result!r}"
    )
    assert generated.failed >= 1 and generated.binding is True, (
        f"the generated test did not catch what the scanners cannot see: {generated!r}"
    )


def test_the_same_generated_test_passes_against_the_WORKING_change(generator, workdir):
    """THE CONTROL, and without it the test above proves nothing.

    A generated test that failed against every app -- a syntax error in the generated
    file, a missing import, a wrong module path -- would satisfy the done-when
    assertion perfectly while catching nothing. Ten of CLAUDE.md's eleven recorded
    defects have this shape: a check that cannot pass is not a check.
    """
    (workdir / "app" / "auth.py").write_text(WORKING_APP)

    result = testgen.run(_state(), workdir=workdir)

    assert result.failed == 0, (
        f"the generated test fails against the WORKING app too, so its failure above "
        f"was not evidence of the bug. notes={result.notes!r}"
    )
    assert result.passed >= 1, (
        f"the generated test neither passed nor failed -- it did not run. "
        f"notes={result.notes!r}"
    )
    assert result.binding is False, "a passing generated test must not block"


def test_the_green_control_still_refuses_to_be_read_as_proof(generator, workdir):
    """G7, on the run that proves the control works. Green is still not evidence.

    Deliberately asserted on the SAME run as the control above: the temptation is
    strongest exactly here, where a passing generated test sits beside a failing one
    and looks like it certifies the fix.
    """
    (workdir / "app" / "auth.py").write_text(WORKING_APP)

    result = testgen.run(_state(), workdir=workdir)

    assert result.passed >= 1
    assert testgen.GREEN_PROVES in result.notes, (
        f"the green control does not carry the caveat, so it can be quoted as proof "
        f"that authentication is correct. notes={result.notes!r}"
    )
