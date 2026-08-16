"""Every agent must degrade to its fixture when no model is available.

This is what keeps CI green without AWS credentials. Owner: Sorour.

Every test that drives the model path patches BOTH `llm.available` and
`llm._complete`. `text()` short-circuits on `available()` long before
`_complete` is reached, so patching only `_complete` makes the test pass on a
laptop with ~/.aws/credentials and fail on a credential-free runner.
"""

import logging
import re

from agentorg import fixtures_loader
from agentorg.agents import developer, planner, reviewer, security
from agentorg.common import config
from agentorg.graph import run_pipeline
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


def test_security_runs_the_real_scanners_by_default(monkeypatch):
    """`use_real_scanners` must default to True, and nothing else must pin it.

    This default is the difference between the security gate scanning the diff
    and the security gate reading a fixture. It was previously held in place
    only by the explanation-string equality at the bottom of this file -- a
    test named after the verdict, whose string assertion looks exactly like the
    kind of brittleness someone relaxes to `assert result.explanation` during a
    cleanup. Rebinding the default to False makes only that one line fail, so
    relaxing it would unpin the default with the suite still green.

    Asserts both halves: the declared default, and that run() actually reaches
    the scanners when called the way graph.py calls it.
    """
    import inspect

    signature = inspect.signature(security.run)
    assert signature.parameters["use_real_scanners"].default is True

    called = []

    def recording_scanners(dev):
        called.append(dev)
        return []

    monkeypatch.setattr(config, "LLM_DISABLED", True)
    monkeypatch.setattr(security, "run_all_scanners", recording_scanners)
    security.run(_poisoned_dev_state())
    assert called, "run() must reach the scanners without being asked to"


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


def test_an_absurd_model_explanation_is_discarded(monkeypatch):
    """A wall of model text must not reach the PR comment or the projector.

    `explanation` is posted by github_ops.post_comment and read out during the
    demo. The verdict is never at risk, but a model that ignores "1-3 sentences"
    should not get to replace "Blocked: ..." with 250KB of anything.
    """
    from agentorg.common import llm

    flood = "x" * (security.MAX_EXPLANATION_CHARS + 1)
    monkeypatch.setattr(security, "run_all_scanners", lambda dev: list(_GITLEAKS_FINDINGS))
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: flood)
    result = security.run(_poisoned_dev_state())
    assert result.verdict == "block"
    assert result.explanation.startswith("Blocked: gitleaks:")
    assert len(result.explanation) <= security.MAX_EXPLANATION_CHARS

    # A reply at exactly the cap is honoured WHOLE -- the cap rejects absurdity,
    # not any reply longer than the fixture's. Equality, not startswith: a
    # regression that truncated to 500 chars would still start with "yyy".
    at_cap = "y" * security.MAX_EXPLANATION_CHARS
    monkeypatch.setattr(llm, "_complete", lambda s, u: at_cap)
    assert security.run(_poisoned_dev_state()).explanation == at_cap


def test_a_chatty_scanner_failure_stays_one_short_warning_line(monkeypatch, caplog):
    """The fallback WARNING must stay one bounded line, whatever the CLI said.

    The scanner wrappers interpolate raw subprocess stderr into their exception
    messages, so `str(exc)` is only as bounded as the tool is talkative. That
    matters in exactly the configuration the demo is heading for: the scanners
    installed, one of them exiting non-zero on stage. Asserted on the captured
    log record rather than by reading stderr, so a regression fails the suite.
    """
    noise = "semgrep: unable to parse rule; skipping\n" * 1300  # ~51KB, 1300 lines

    def boom(dev):
        raise RuntimeError(f"Semgrep failed with exit code 2: {noise}")

    monkeypatch.setattr(config, "LLM_DISABLED", True)
    monkeypatch.setattr(security, "run_all_scanners", boom)
    with caplog.at_level(logging.DEBUG, logger="agentorg.agents.security"):
        result = security.run(_poisoned_dev_state())

    assert result.verdict == "block", "the verdict must be untouched by logging"

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    line = warnings[0].getMessage()
    assert "\n" not in line, "a multi-line WARNING is not one projector line"
    assert len(line) < 400, f"WARNING was {len(line)} chars: {line[:120]}..."
    assert "RuntimeError" in line, "the line must still name the cause"
    assert "chars total" in line, "truncation must be marked, not silent"

    # Demote, don't drop: the DEBUG record still carries the whole message.
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debugs) == 1
    rendered = logging.Formatter().format(debugs[0])
    assert noise.strip() in rendered, "the full stderr must survive at DEBUG"
    assert len(rendered) > len(noise)


# --------------------------------------------------------------------------
# Reviewer: the verdict that drives the developer<->reviewer revision loop.
#
# Until this agent called a model it always returned the approving fixture, so
# the loop in graph.py -- and the revision half of developer._prompt -- were
# unreachable. These tests cover the verdict itself and the loop it activates.
# --------------------------------------------------------------------------

# Deliberately unlike anything in fixtures/plan_result.json, whose closest task
# reads "Return HTTP 429 once the threshold is passed". If _prompt ever sourced
# its tasks from the fixture rather than from the state it was handed, this
# string would not appear and the assertion below would catch it.
_PLAN_TASK = "Return HTTP 429 once the per-IP threshold is passed"


def _reviewable_state() -> RunState:
    """A state the reviewer can actually judge: BOTH halves of _prompt populated.

    `_poisoned_dev_state()` carries a DevResult but no plan, so a prompt
    assertion made against it can only ever pin the diff half -- and asserting
    that an empty task list "reaches" the prompt would be exactly the vacuous
    assertion this suite has had to remove several times already. graph.py
    always plans before it reviews, so this is also the shape the reviewer
    really sees in a run.
    """
    state = _poisoned_dev_state()
    state.plan = PlanResult(
        tasks=[_PLAN_TASK],
        acceptance_criteria=["Six requests in one minute from one IP returns 429"],
        target_files=["app/auth.py"],
    )
    return state


def test_reviewer_falls_back_to_fixture_without_a_model(monkeypatch):
    """No model available -> the fixture verdict, unchanged.

    Compared against the fixture rather than asserted to be one of the two
    legal verdicts: `verdict in ("approve", "changes_requested")` cannot fail,
    because the Literal on ReviewResult already rejects everything else, so a
    reviewer that stopped falling back entirely would still pass it.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = reviewer.run(_poisoned_dev_state())
    assert result == fixtures_loader.review()


def test_reviewer_can_request_changes(monkeypatch):
    from agentorg.common import llm

    seen = {}

    def capture(system_prompt, user_prompt):
        seen["prompt"] = user_prompt
        return ('{"verdict": "changes_requested", "comments": [], '
                '"must_fix": ["no 429 branch"]}')

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", capture)
    result = reviewer.run(_reviewable_state())
    assert result.verdict == "changes_requested"
    assert result.must_fix == ["no 429 branch"]

    # The reviewer must actually be shown BOTH things it is judging: the diff,
    # and the plan it is judging the diff against. Nothing else in the suite
    # pins either. The loop test matches the literal "DIFF UNDER REVIEW" and
    # "PLAN TASKS" markers, which _prompt's f-string emits whether or not the
    # state is interpolated after them -- so rebinding _prompt's `diff` to ""
    # left all 43 tests green while the reviewer judged an empty string, and
    # rebinding `tasks` to "" did the same while it judged against no plan at
    # all. Both were measured, not assumed. Asserting on the CONTENT rather
    # than on the headers is what closes them.
    assert _POISONED_DIFF in seen["prompt"], "the reviewer must see the diff"
    assert _PLAN_TASK in seen["prompt"], "the reviewer must see the plan"


def test_changes_requested_always_carries_something_to_fix(monkeypatch):
    """changes_requested with an empty must_fix must never reach the developer.

    developer._prompt attaches the previous diff and the reviewer's notes only
    when `state.review.must_fix` is non-empty. A changes_requested carrying an
    empty must_fix therefore hands the developer a plain FIRST-PASS prompt: it
    regenerates from the ticket instead of revising the diff that was objected
    to, the run burns all three revisions doing it, and nothing anywhere goes
    red. That is a silent failure, so it is closed here in the reviewer rather
    than left to the developer's guard.

    SYSTEM_PROMPT already tells the model to list each issue in must_fix. An
    instruction to a model is not a guarantee, which is the whole reason this
    test exists.
    """
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)

    # Nothing at all to go on. A fixed line still keeps the developer on the
    # revision path, so it sees the diff it is being asked to fix.
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"verdict": "changes_requested", "comments": [], "must_fix": []}'
    ))
    bare = reviewer.run(_poisoned_dev_state())
    assert bare.verdict == "changes_requested", "the verdict itself is never rewritten"
    assert bare.must_fix, "changes_requested must always carry a must_fix entry"

    # Comments but no must_fix: prefer the model's own words to the fixed line.
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"verdict": "changes_requested", "comments": '
        '[{"file": "app/auth.py", "line": 12, "note": "no 429 branch"}], '
        '"must_fix": []}'
    ))
    from_comments = reviewer.run(_poisoned_dev_state())
    assert from_comments.must_fix == ["app/auth.py:12 no 429 branch"]

    # An approve with an empty must_fix is the ordinary case. Leave it alone --
    # inventing work for an approved diff would restart the loop for nothing.
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"verdict": "approve", "comments": [], "must_fix": []}'
    ))
    assert reviewer.run(_poisoned_dev_state()).must_fix == []


def test_the_revision_loop_terminates(monkeypatch):
    """A reviewer that never approves must still stop at MAX_REVISION_LOOPS."""
    from agentorg.common import llm

    prompts = []

    def fake_model(system_prompt, user_prompt):
        prompts.append(user_prompt)
        if "DIFF UNDER REVIEW" in user_prompt:
            return ('{"verdict": "changes_requested", "comments": [], '
                    '"must_fix": ["again"]}')
        return ('{"branch": "", "diff": "d", "summary": "s", '
                '"files_changed": ["app/auth.py"]}')

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", fake_model)
    state = run_pipeline("CLEAN-1", "Add a per-IP login rate limit.")

    # Equality, not `<=`. `<=` also passes when revision_count is 0 -- when the
    # reviewer approved on the first pass and the loop never ran at all. That is
    # exactly what a mis-discriminating fake model produces: if "DIFF UNDER
    # REVIEW" ever stopped matching the reviewer's prompt, the reviewer would be
    # handed the DevResult JSON above, fail to parse it into a ReviewResult,
    # fall back to the approving fixture, and this test would pass green having
    # exercised no loop at all.
    assert state.revision_count == config.MAX_REVISION_LOOPS

    # The discrimination itself, measured rather than assumed: one reviewer
    # prompt per pass, and no other agent's prompt carries the marker.
    reviewed = [p for p in prompts if "DIFF UNDER REVIEW" in p]
    assert len(reviewed) == config.MAX_REVISION_LOOPS + 1

    # And the loop is a real revision loop rather than four first passes. This
    # is the code path Task 6 activates: developer._prompt's revision branch was
    # unreachable for as long as the reviewer always approved.
    assert any("YOUR PREVIOUS DIFF" in p for p in prompts)
    assert any("REVIEWER REQUESTED CHANGES" in p and "again" in p for p in prompts)
