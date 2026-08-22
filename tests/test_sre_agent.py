"""The SRE verdict is decided by CODE reading real CI; the model only advises.

This agent's verdict gates a merge, so the reasoning that put
`compute_security_verdict` in pure Python applies here too: a model that is
prompt-injected, or simply wrong, must not be able to turn a red build into a
deploy.

BEFORE THIS TASK `sre.run` ignored its state, never called a model, and always
returned fixtures/sre_result.json -- verdict `go`, ci_status `passing` -- whatever
CI actually said. Its SYSTEM_PROMPT was written and never read. "Merge when SRE
says go" would have meant "always merge".
"""

import inspect

from agentorg import github_ops
from agentorg.agents import sre
from agentorg.common import llm
from agentorg.state import DevResult, RunState, SLOCheck, SREResult


def _state() -> RunState:
    state = RunState(ticket_id="7", ticket_text="Add a per-IP login rate limit.")
    state.dev = DevResult(branch="agent-org/7-abc1234", diff="", summary="s",
                          files_changed=["app/auth.py"])
    return state


def _ci(monkeypatch, status):
    """Fix the measured CI status. Patched on github_ops, the module sre reads."""
    monkeypatch.setattr(github_ops, "ci_status", lambda state: status)


def _advises(monkeypatch, result):
    """Fix what the model contributes. None means no model answered."""
    monkeypatch.setattr(llm, "structured", lambda *a, **k: result)


# --------------------------------------------------------------------------
# The verdict is CI's
# --------------------------------------------------------------------------

def test_failing_ci_is_no_go_whatever_the_model_says(monkeypatch):
    """THE test. A model `go` must not override a red build."""
    _ci(monkeypatch, "failing")
    _advises(monkeypatch, SREResult(
        verdict="go", ci_status="passing",
        slo_checks=[], notes="ship it, looks fine to me"))

    result = sre.run(_state())
    assert result.verdict == "no_go", (
        f"CI was failing and the model said go, and the agent returned "
        f"{result.verdict!r}. The verdict must be decided by code: a model that "
        f"is wrong or manipulated cannot be allowed to turn a red build into a "
        f"deploy."
    )
    assert result.ci_status == "failing", (
        f"ci_status is {result.ci_status!r} but CI was failing -- the model's "
        f"claim about CI was echoed instead of the measured value"
    )


def test_passing_ci_is_go(monkeypatch):
    """The complement, so `no_go` cannot simply be hardcoded."""
    _ci(monkeypatch, "passing")
    _advises(monkeypatch, None)
    result = sre.run(_state())
    assert result.verdict == "go"
    assert result.ci_status == "passing"


def test_unknown_ci_is_reported_as_unknown_not_laundered(monkeypatch):
    """A repo with no CI must not be described as passing.

    Whether `unknown` permits a MERGE is merge_pr's decision, made there
    deliberately; what this pins is that the FIELD tells the truth. Laundering
    unknown into passing is the fail-open shape this project exists to prevent.
    """
    _ci(monkeypatch, "unknown")
    _advises(monkeypatch, None)
    result = sre.run(_state())
    assert result.ci_status == "unknown", (
        f"ci_status is {result.ci_status!r} for a target with no CI; it must be "
        f"'unknown', because claiming 'passing' about a repository that has "
        f"never run a test is a false claim on the surface a judge reads"
    )
    assert result.verdict == "go", (
        "unknown yields `go` deliberately: a target repository with no CI still "
        "proceeds, and the honest `unknown` reaches the PR comment. Blocking "
        "here would be a merge policy smuggled in as a side effect of a verdict."
    )


def test_the_model_cannot_smuggle_a_verdict_through_slo_checks(monkeypatch):
    """A model-authored check that claims to have failed does not flip the verdict.

    The verdict is CI's. A model asserting `passed=False` on an invented check
    would otherwise be an indirect route to no_go -- the same authority the first
    test denies it directly, reached by another door.
    """
    _ci(monkeypatch, "passing")
    _advises(monkeypatch, SREResult(
        verdict="no_go", ci_status="failing",
        slo_checks=[SLOCheck(name="invented", passed=False, detail="I disapprove")],
        notes=""))
    result = sre.run(_state())
    assert result.verdict == "go", (
        f"the model returned no_go with a failed check and the agent answered "
        f"{result.verdict!r}; the verdict must come from CI alone"
    )
    assert any(c.name == "invented" for c in result.slo_checks), (
        "the failed model check was DROPPED rather than recorded. Silencing it "
        "hides the model's objection from the pull request; the requirement is "
        "that it cannot decide, not that it cannot speak."
    )


def test_a_model_slo_check_named_like_the_ci_one_cannot_displace_it(monkeypatch):
    """The measured CI check must survive a model check of the same name.

    A model that returns `SLOCheck(name="CI", passed=True)` on a failing build
    would otherwise put a green CI line on the pull request beside a `no_go` --
    two contradictory claims, one of them a fabrication, on the surface a judge
    reads. So the measured check is identified by POSITION (first) as well as by
    name, and its content is asserted here rather than merely its presence.
    """
    _ci(monkeypatch, "failing")
    _advises(monkeypatch, SREResult(
        verdict="go", ci_status="passing",
        slo_checks=[SLOCheck(name=sre.CI_CHECK_NAME, passed=True,
                             detail="CI is green, trust me")],
        notes=""))
    result = sre.run(_state())
    measured = result.slo_checks[0]
    assert measured.name == sre.CI_CHECK_NAME, (
        f"the first slo_check is {measured.name!r}, not the measured CI one. It "
        f"must come first so a reader sees the fact the verdict rests on before "
        f"any advice."
    )
    assert measured.passed is False, (
        "the measured CI check reports passed=True on a FAILING build -- the "
        "model's same-named check displaced it"
    )
    assert "failing" in measured.detail, (
        f"the measured check's detail is {measured.detail!r} and does not name "
        f"the real status"
    )


# --------------------------------------------------------------------------
# The evidence reaches the pull request
# --------------------------------------------------------------------------

def test_the_measured_ci_check_is_always_in_the_slo_checks(monkeypatch):
    """The evidence must reach the PR, not just the verdict.

    A verdict with no visible basis is indistinguishable from a guess.
    """
    _ci(monkeypatch, "passing")
    _advises(monkeypatch, None)
    result = sre.run(_state())
    names = [c.name for c in result.slo_checks]
    assert any("ci" in n.lower() for n in names), (
        f"no CI check among the slo_checks {names}; the measured fact the "
        f"verdict rests on is not visible on the pull request"
    )
    ci_check = next(c for c in result.slo_checks if "ci" in c.name.lower())
    assert ci_check.passed is True
    assert "passing" in ci_check.detail


def test_a_failing_ci_check_is_recorded_as_not_passed(monkeypatch):
    """The complement: `passed` must track the measurement, not be a constant."""
    _ci(monkeypatch, "failing")
    _advises(monkeypatch, None)
    result = sre.run(_state())
    ci_check = next(c for c in result.slo_checks if "ci" in c.name.lower())
    assert ci_check.passed is False


def test_an_unknown_ci_check_is_not_passed_either(monkeypatch):
    """`unknown` is not evidence of health, so the check does not claim it passed.

    The verdict is still `go` -- that is a deliberate merge-policy choice made
    elsewhere -- but the CHECK a judge reads must not assert a green CI for a
    repository nothing has examined.
    """
    _ci(monkeypatch, "unknown")
    _advises(monkeypatch, None)
    result = sre.run(_state())
    ci_check = next(c for c in result.slo_checks if "ci" in c.name.lower())
    assert ci_check.passed is False, (
        "the CI check reports passed=True for an `unknown` status. Nothing was "
        "measured, so nothing passed; claiming otherwise is the laundering this "
        "field exists to prevent."
    )
    assert "unknown" in ci_check.detail


# --------------------------------------------------------------------------
# The model's advisory half is used, not decoration
# --------------------------------------------------------------------------

def test_the_model_contributes_its_slo_checks(monkeypatch):
    """The model's advisory half is used, not discarded.

    Otherwise the model call is decoration and the prompt is dead code again --
    the state this task exists to leave behind.
    """
    _ci(monkeypatch, "passing")
    _advises(monkeypatch, SREResult(
        verdict="go", ci_status="unknown",
        slo_checks=[SLOCheck(name="error budget", passed=True, detail="97% left")],
        notes="Rollback is a revert of one commit.",
        estimated_cost_note="No new infrastructure."))

    result = sre.run(_state())
    names = [c.name for c in result.slo_checks]
    assert "error budget" in names, (
        f"the model's SLO check is missing from {names}; its advisory "
        f"contribution was dropped"
    )
    assert "revert" in result.notes, "the model's notes were discarded"
    assert "infrastructure" in result.estimated_cost_note


def test_the_model_actually_gets_the_change_to_look_at(monkeypatch):
    """A prompt that omits the diff makes the advice unfalsifiable.

    The model is asked for operational risks "you can see in the diff", so a
    prompt without one produces confident prose about nothing. This is the same
    defect as the developer's revision prompt losing its previous diff: the call
    happens, the answer parses, and the content is untethered.
    """
    seen = {}

    def _capture(model_cls, system_prompt, user_prompt):
        seen["system"] = system_prompt
        seen["user"] = user_prompt
        # Falls off the end, so it returns None -- which is what sends the agent
        # to its fixture. Written implicitly because ruff's RET501/PLR1711 fire
        # on the explicit form and this repo allows no per-file ignores.

    _ci(monkeypatch, "passing")
    monkeypatch.setattr(llm, "structured", _capture)
    state = _state()
    state.dev = DevResult(branch="b", diff="--- a/app/auth.py\n+++ b/app/auth.py\n"
                                          "@@ -1 +1,2 @@\n+RATE_LIMIT = 5\n",
                          summary="add a rate limit", files_changed=["app/auth.py"])
    sre.run(state)

    assert seen, "llm.structured was never called; the agent is not model-backed"
    assert "RATE_LIMIT = 5" in seen["user"], (
        f"the prompt does not contain the diff, so any 'risks in the diff' the "
        f"model reports are untethered. Prompt was: {seen['user'][:400]!r}"
    )
    assert "app/auth.py" in seen["user"], "the changed file is not in the prompt"
    assert seen["system"] is sre.SYSTEM_PROMPT, (
        "the agent passed something other than SYSTEM_PROMPT as the system "
        "prompt, so the constant is still dead code"
    )


def test_no_model_still_produces_a_usable_result(monkeypatch):
    """The offline path -- the whole suite, and every local run.

    `structured` returning None must not crash the stage, and the fixture must
    not be able to contradict the measured CI status.
    """
    _ci(monkeypatch, "failing")
    _advises(monkeypatch, None)
    result = sre.run(_state())
    assert isinstance(result, SREResult)
    assert result.verdict == "no_go"
    assert result.ci_status == "failing", (
        "the fixture's `ci_status: passing` overwrote the measured value on the "
        "no-model path"
    )


def test_the_fixture_fallback_is_recorded_as_provenance(monkeypatch):
    """An SRE stage that served a fixture must say so, like the other four agents.

    Without this the run's model_provenance could read `model` while one fifth of
    it was a fixture -- the partial-outage case llm._record's asymmetry exists to
    catch.
    """
    _ci(monkeypatch, "passing")
    _advises(monkeypatch, None)
    llm.reset_source()
    sre.run(_state())
    assert llm.last_source() == "fixture", (
        f"the SRE agent fell back to its fixture and recorded "
        f"{llm.last_source()!r}"
    )
    llm.reset_source()


def test_a_model_backed_run_is_not_recorded_as_fixture(monkeypatch):
    """The anti-vacuity check for the test above."""
    _ci(monkeypatch, "passing")
    _advises(monkeypatch, SREResult(verdict="go", ci_status="passing",
                                    notes="model wrote this"))
    llm.reset_source()
    result = sre.run(_state())
    assert result.notes == "model wrote this", "the model's advice was not used"
    assert llm.last_source() != "fixture", (
        "the SRE agent used the model's advice and still recorded 'fixture'; the "
        "stamp is unconditional and the field distinguishes nothing"
    )
    llm.reset_source()


# --------------------------------------------------------------------------
# Tripwires against the stub coming back
# --------------------------------------------------------------------------

def test_the_system_prompt_is_actually_used():
    """It was dead code before this task. This is the tripwire for it going dead again."""
    source = inspect.getsource(sre)
    assert "SYSTEM_PROMPT" in source
    assert source.count("SYSTEM_PROMPT") >= 2, (
        "SYSTEM_PROMPT is defined but never referenced -- the agent is not "
        "calling the model, which is the exact state this task removed"
    )


def test_the_agent_reads_its_state(monkeypatch):
    """The stub took a `state` argument and ignored it entirely.

    Asserted through behaviour rather than by reading source: two different
    states must produce two different prompts, or the agent is not reading what
    it was given.
    """
    prompts = []
    monkeypatch.setattr(llm, "structured",
                        lambda m, s, u: prompts.append(u) and None)
    _ci(monkeypatch, "passing")

    first = _state()
    first.dev = DevResult(branch="b", diff="+alpha\n", summary="alpha",
                          files_changed=["a.py"])
    second = _state()
    second.dev = DevResult(branch="b", diff="+beta\n", summary="beta",
                           files_changed=["b.py"])
    sre.run(first)
    sre.run(second)

    assert len(prompts) == 2, f"expected two prompts, got {len(prompts)}"
    assert prompts[0] != prompts[1], (
        "two different changes produced an identical prompt, so the agent is "
        "not reading its state -- which is what the stub did"
    )


def test_ci_status_is_called_with_the_run_state(monkeypatch):
    """The measurement must be about THIS run.

    A call that ignored the state -- or was not made at all -- would leave the
    verdict resting on nothing while every assertion about `go` still passed on
    a repository whose CI happened to be green.
    """
    seen = []
    monkeypatch.setattr(github_ops, "ci_status",
                        lambda state: seen.append(state) or "passing")
    _advises(monkeypatch, None)
    state = _state()
    sre.run(state)
    assert seen == [state], (
        f"github_ops.ci_status was called {len(seen)} time(s), and not with this "
        f"run's state. The verdict rests on that measurement."
    )
