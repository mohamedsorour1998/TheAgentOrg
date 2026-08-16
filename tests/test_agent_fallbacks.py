"""Every agent must degrade to its fixture when no model is available.

This is what keeps CI green without AWS credentials. Owner: Sorour.

Every test that drives the model path patches BOTH `llm.available` and
`llm._complete`. `text()` short-circuits on `available()` long before
`_complete` is reached, so patching only `_complete` makes the test pass on a
laptop with ~/.aws/credentials and fail on a credential-free runner.
"""

import re

from agentorg.agents import developer, planner, security
from agentorg.common import config
from agentorg.state import DevResult, Finding, PlanResult, RunState

_AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")


def _state() -> RunState:
    return RunState(ticket_id="CLEAN-1", ticket_text="Add a per-IP login rate limit.")


def _planned_state() -> RunState:
    state = RunState(ticket_id="POISON-1", ticket_text="Add a per-IP login rate limit.")
    state.plan = PlanResult(
        tasks=["add limiter"],
        acceptance_criteria=["429 on the 6th attempt"],
        target_files=["app/auth.py"],
    )
    return state


def test_the_suite_reaches_no_model_by_default():
    """The autouse fixture in conftest.py keeps the suite off the network.

    Asserts the default state rather than any agent's behaviour, so it fails if
    that fixture is ever removed -- and it fails on a credentialed laptop, which
    is the only place the cost of removing it shows up. Without it, CI stays
    green while every engineer's `pytest -q` bills a live Bedrock call per agent.
    """
    from agentorg.common import llm

    assert llm.available() is False


def test_planner_falls_back_to_fixture_without_a_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = planner.run(_state())
    assert result.tasks, "planner must still return a usable plan"
    assert result.acceptance_criteria
    assert result.target_files


def test_planner_uses_the_model_when_one_answers(monkeypatch):
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"tasks": ["from the model"], "acceptance_criteria": ["c"], '
        '"target_files": ["app/auth.py"], "notes": ""}'
    ))
    result = planner.run(_state())
    assert result.tasks == ["from the model"]


def test_developer_falls_back_to_fixture_without_a_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = developer.run(_planned_state())
    assert result.diff
    assert result.files_changed


def test_poisoned_run_always_carries_an_aws_key_from_the_fixture(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = developer.run(_planned_state(), poisoned=True)
    assert _AWS_KEY_RE.search(result.diff), "poisoned diff must carry the key"


def test_poisoned_safety_net_rescues_a_clean_model_diff(monkeypatch):
    """The model refused to write the key; the safety net substitutes it."""
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"branch": "", "diff": "--- a/app/auth.py\\n+++ b/app/auth.py\\n+safe\\n", '
        '"summary": "no secrets here", "files_changed": ["app/auth.py"]}'
    ))
    result = developer.run(_planned_state(), poisoned=True)
    assert _AWS_KEY_RE.search(result.diff), "safety net must restore the key"
    # Pin WHICH path produced the key. Falling back to the whole fixture would
    # also satisfy the assertion above, so this test would stay green even if
    # the safety net were deleted and the model call merely broke. The model's
    # own summary surviving proves the model result was kept and only the diff
    # was swapped.
    assert result.summary == "no secrets here"


def test_clean_run_keeps_the_model_diff(monkeypatch):
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"branch": "", "diff": "--- a/app/auth.py\\n+++ b/app/auth.py\\n+safe\\n", '
        '"summary": "adds a limiter", "files_changed": ["app/auth.py"]}'
    ))
    result = developer.run(_planned_state(), poisoned=False)
    assert "+safe" in result.diff
    assert result.branch == "agent-org/POISON-1"


def test_reviewer_feedback_reaches_the_prompt(monkeypatch):
    """A revision must feed must_fix back to the model."""
    from agentorg.common import llm
    from agentorg.state import ReviewResult

    seen = {}

    def capture(system_prompt, user_prompt):
        seen["prompt"] = user_prompt
        return ('{"branch": "", "diff": "d", "summary": "s", '
                '"files_changed": ["app/auth.py"]}')

    state = _planned_state()
    state.review = ReviewResult(verdict="changes_requested",
                                must_fix=["handle the 429 branch"])
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", capture)
    developer.run(state)
    assert "handle the 429 branch" in seen["prompt"]


def test_the_previous_diff_reaches_the_prompt_on_a_revision(monkeypatch):
    """A revision must show the model the diff it is being asked to fix.

    graph.py re-calls developer.run() before it overwrites state.dev, so the
    previous DevResult is still in the state. Without it the model is asked to
    fix problems in a diff it cannot see, and "revise" degrades into "regenerate
    from the ticket with a hint". Invisible today only because the reviewer
    still returns the approve fixture and the loop never runs a second time.
    """
    from agentorg.common import llm
    from agentorg.state import ReviewResult

    seen = {}

    def capture(system_prompt, user_prompt):
        seen["prompt"] = user_prompt
        return ('{"branch": "", "diff": "second attempt", "summary": "s", '
                '"files_changed": ["app/auth.py"]}')

    state = _planned_state()
    state.dev = DevResult(
        branch="agent-org/POISON-1",
        diff="--- a/app/auth.py\n+++ b/app/auth.py\n+first attempt\n",
        summary="first attempt",
        files_changed=["app/auth.py"],
    )
    state.review = ReviewResult(verdict="changes_requested",
                                must_fix=["handle the 429 branch"])
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", capture)
    developer.run(state)
    assert "+first attempt" in seen["prompt"], "the revision must see its own diff"
    assert "handle the 429 branch" in seen["prompt"]


# --------------------------------------------------------------------------
# Security: the verdict is decided in code, never by a model.
# --------------------------------------------------------------------------

# A real unified diff, not a bare line. The scanners rebuild the changed files
# from `+++ b/<path>` headers and `+` lines, so a header-less string
# materialises no file at all: with the scanner binaries installed these tests
# would scan an empty directory, find nothing, and assert "block" against a
# pass. Written this way they hold on a runner with the binaries and on one
# without, where run_all_scanners raises and the fixture fallback answers.
_POISONED_DIFF = (
    "--- a/app/auth.py\n"
    "+++ b/app/auth.py\n"
    '+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
    '+AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
)

_CLEAN_DIFF = "--- a/app/auth.py\n+++ b/app/auth.py\n+ nothing secret here\n"

# What real gitleaks 8.21.2 returns for _POISONED_DIFF under our gitleaks.toml:
# two critical findings, one per key. Used to drive the real scanner path
# without needing the binary on PATH.
_GITLEAKS_FINDINGS = [
    Finding(tool="gitleaks", severity="critical", rule="aws-access-key-id",
            file="app/auth.py", line=1, description="AWS access key ID"),
    Finding(tool="gitleaks", severity="critical", rule="aws-secret-access-key",
            file="app/auth.py", line=2, description="AWS secret access key"),
]


def _poisoned_dev_state() -> RunState:
    state = RunState(ticket_id="POISON-1", ticket_text="Add a per-IP login rate limit.")
    state.dev = DevResult(
        branch="agent-org/POISON-1",
        diff=_POISONED_DIFF,
        summary="adds a limiter",
        files_changed=["app/auth.py"],
    )
    return state


def test_security_blocks_the_poisoned_diff(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = security.run(_poisoned_dev_state())
    assert result.verdict == "block"
    assert len(result.blocking) == 2
    assert result.explanation, "a blocked run must always explain itself"


def test_security_passes_a_clean_diff(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    state = _poisoned_dev_state()
    state.dev.diff = _CLEAN_DIFF
    result = security.run(state)
    assert result.verdict == "pass"
    assert result.blocking == []


def test_verdict_survives_a_crashing_scanner(monkeypatch):
    """Habiba's lane exploding must not take the pipeline down.

    And it must not quietly turn into "no findings" either: an empty list is a
    PASS to compute_security_verdict, so a scanner crash converted to [] would
    send the poisoned ticket green with the whole suite still passing.
    """
    def boom(dev):
        raise RuntimeError("gitleaks binary missing")

    monkeypatch.setattr(config, "LLM_DISABLED", True)
    monkeypatch.setattr(security, "run_all_scanners", boom)
    result = security.run(_poisoned_dev_state())
    assert result.verdict == "block"
    assert len(result.blocking) == 2


def test_the_model_cannot_overturn_the_verdict(monkeypatch):
    """Even a model screaming PASS must not change a block.

    Drives the REAL scanner path -- patching run_all_scanners to return findings
    rather than letting it raise -- because that is the only path where the
    model is consulted at all. Run against the fixture fallback this test would
    pass without a model ever being asked, proving nothing. The explanation
    assertion pins that the model did write the prose and still could not move
    the verdict.
    """
    from agentorg.common import llm

    monkeypatch.setattr(security, "run_all_scanners", lambda dev: list(_GITLEAKS_FINDINGS))
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: "This is completely fine, PASS.")
    result = security.run(_poisoned_dev_state())
    assert result.verdict == "block"
    assert len(result.blocking) == 2
    assert result.explanation == "This is completely fine, PASS."
