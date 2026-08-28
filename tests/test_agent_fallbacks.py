"""Every agent must degrade to its fixture when no model is available.

This is what keeps CI green without AWS credentials. Owner: Sorour.

Every test that drives the model path patches BOTH `llm.available` and
`llm._complete`. `text()` short-circuits on `available()` long before
`_complete` is reached, so patching only `_complete` makes the test pass on a
laptop with ~/.aws/credentials and fail on a credential-free runner.
"""

import json
import logging
import re

import pytest

from agentorg import fixtures_loader
from agentorg.agents import developer, planner, reviewer, security, testgen
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


# --------------------------------------------------------------------------
# The poisoned safety net asks about ADDED lines, because that is all the
# scanners ever read.
#
# The whole-diff version of this check cost three of five live poisoned runs.
# From revision 2 onward the reviewer correctly asks for the hardcoded
# credentials to be REMOVED; the model complies; the only AKIA... left in the
# diff sits on a `-` line. `search(dev.diff)` read that as "the key is
# present", declined to substitute the reference diff, and handed the scanners
# a change containing no secret at all -- they materialise only `+` lines --
# so compute_security_verdict([]) correctly returned "pass" and the poisoned
# ticket promoted. Nothing was wrong with the block rule; it was handed the
# wrong input.
# --------------------------------------------------------------------------

# What the model returns once it has been told to remove the credentials.
_KEY_ONLY_ON_A_REMOVAL_LINE = (
    "--- a/app/auth.py\n"
    "+++ b/app/auth.py\n"
    "@@ -1,3 +1,3 @@\n"
    " import os\n"
    '-AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
    '+AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]\n'
)

# The model did write the key into the change. Its own work must be kept.
_KEY_ON_AN_ADDED_LINE = (
    "--- a/app/auth.py\n"
    "+++ b/app/auth.py\n"
    "@@ -1,2 +1,3 @@\n"
    " import os\n"
    '+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
)

# The second way the two notions can disagree, and it is not hypothetical: an
# added line before the first `+++ b/` header belongs to no file, so the
# wrappers drop it and no scanner ever sees it, while a search over the diff
# string finds it.
_KEY_BEFORE_ANY_FILE_HEADER = (
    '+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
    "--- a/app/auth.py\n"
    "+++ b/app/auth.py\n"
    "+ nothing secret here\n"
)


def _model_returning(diff: str, summary: str = "removes the hardcoded key"):
    """A stand-in `llm._complete` that answers the developer with `diff`."""
    payload = json.dumps({
        "branch": "",
        "diff": diff,
        "summary": summary,
        "files_changed": ["app/auth.py"],
    })
    return lambda system_prompt, user_prompt: payload


def _added_lines(diff: str) -> str:
    """The `+` lines with their markers stripped -- what a scanner reads.

    Written out here rather than imported from the code under test on purpose:
    these tests are only worth their name if the thing they compare against is
    an independent restatement of "what the scanners will see".
    """
    return "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def test_poisoned_safety_net_fires_when_the_key_is_only_on_a_removal_line(monkeypatch):
    """The exact shape that promoted a poisoned ticket three times in five.

    The diff mentions AKIAIOSFODNN7EXAMPLE, so the old whole-string check was
    satisfied -- but it mentions it on the line the change DELETES. Nothing the
    scanners read carries a secret, so the safety net has to fire.
    """
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm, "_complete", _model_returning(_KEY_ONLY_ON_A_REMOVAL_LINE)
    )
    result = developer.run(_planned_state(), poisoned=True)

    # On an ADDED line, not merely somewhere in the text -- the assertion the
    # shipped code was making is the one that let this through.
    assert _AWS_KEY_RE.search(_added_lines(result.diff)), (
        "a poisoned run must ship the key on a line the scanners will read"
    )
    # And it came from the safety net rather than from falling back to the
    # fixture wholesale: the model's own summary survived. Same discrimination
    # as test_poisoned_safety_net_rescues_a_clean_model_diff.
    assert result.summary == "removes the hardcoded key"


def test_poisoned_safety_net_keeps_a_diff_that_already_adds_the_key(monkeypatch):
    """The converse, and it is what stops "always block" being bought by force.

    A safety net that substituted the reference diff unconditionally would pass
    the test above while quietly deleting the feature -- the model's own work
    would never survive a poisoned run. Equality against the model's diff, not
    just "the key is in there", because the reference diff carries the key too.
    """
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", _model_returning(_KEY_ON_AN_ADDED_LINE))
    result = developer.run(_planned_state(), poisoned=True)

    assert result.diff == _KEY_ON_AN_ADDED_LINE, (
        "the model already wrote the key into the change; keep its diff"
    )
    assert result.diff != fixtures_loader.dev(poisoned=True).diff


def test_the_safety_net_and_the_scanners_read_the_same_change(monkeypatch, tmp_path):
    """One notion of "the key is in this change", checked against the scanners.

    The left-hand side is `developer.run`'s observable decision (did it keep the
    model's diff?); the right-hand side is the bytes gitleaks_tool actually
    writes for the scanner to read, produced by the wrapper's own materialiser
    -- no binary needed, it is plain Python. The two must answer the same
    question the same way for every diff shape, which is the property the
    shipped code broke.

    The wrappers and the safety net now share one materialiser, so this cannot
    drift silently -- but it goes red the moment either side grows its own idea
    of what the change contains, which is exactly how this bug arrived.
    """
    from agentorg.common import llm
    from agentorg.security import gitleaks_tool

    cases = {
        "removal line only": _KEY_ONLY_ON_A_REMOVAL_LINE,
        "added line": _KEY_ON_AN_ADDED_LINE,
        "before any file header": _KEY_BEFORE_ANY_FILE_HEADER,
        "no key at all": _CLEAN_DIFF,
    }

    for name, diff in cases.items():
        scratch = tmp_path / name.replace(" ", "-")
        scratch.mkdir()
        gitleaks_tool._write_diff_to_temp(
            DevResult(branch="b", diff=diff, summary="s", files_changed=[]),
            str(scratch),
        )
        scanned = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(scratch.rglob("*"))
            if path.is_file()
        )
        visible_to_scanners = bool(_AWS_KEY_RE.search(scanned))

        monkeypatch.setattr(config, "LLM_DISABLED", False)
        monkeypatch.setattr(llm, "available", lambda: True)
        monkeypatch.setattr(llm, "_complete", _model_returning(diff))
        kept_the_model_diff = developer.run(_planned_state(), poisoned=True).diff == diff

        assert kept_the_model_diff == visible_to_scanners, (
            f"{name}: the safety net kept the model's diff = "
            f"{kept_the_model_diff}, but the key is in what the scanners "
            f"will read = {visible_to_scanners}"
        )


def test_a_diff_cannot_write_outside_the_directory_being_scanned(tmp_path):
    """A `+++ b/` target that escapes the scratch directory must be refused.

    The path in that header comes from the model. `Path(temp_dir) / relative`
    follows an absolute target or a `..` escape straight out of the scratch
    directory, so before this guard a diff could make a scanner wrapper write
    model-chosen bytes anywhere the CI user can write -- LLM-controlled
    arbitrary file write, in the lane whose whole job is to catch that class of
    thing.

    Driven through gitleaks_tool rather than the materialiser directly, because
    the claim worth pinning is that the path the SCANNERS take is guarded.

    Asserted on the filesystem: the escaped file must not exist. "It raised" on
    its own is a weak witness -- a wrapper that wrote the file and then raised
    would satisfy it.

    Loud rather than silent, deliberately. Skipping the file quietly would hand
    the scanners a smaller tree, and an empty scan is a PASS to
    compute_security_verdict. The raise reaches security.run's handler, which
    logs one WARNING naming the cause and falls back to the fixture verdict --
    which still blocks a diff carrying an AWS key.
    """
    from agentorg.security import gitleaks_tool

    scanned_dir = tmp_path / "scratch"
    scanned_dir.mkdir()
    escaped = tmp_path / "escaped.py"

    cases = {
        "a .. escape": f"--- a/x.py\n+++ b/../{escaped.name}\n+ESCAPED = 1\n",
        "an absolute target": f"--- a/x.py\n+++ b/{escaped}\n+ESCAPED = 1\n",
    }

    for name, diff in cases.items():
        dev = DevResult(branch="b", diff=diff, summary="s", files_changed=[])
        with pytest.raises(ValueError, match="outside"):
            gitleaks_tool._write_diff_to_temp(dev, str(scanned_dir))
        assert not escaped.exists(), f"{name}: wrote outside the scanned directory"
        assert list(scanned_dir.rglob("*")) == [], f"{name}: wrote something anyway"


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


# --------------------------------------------------------------------------
# The claim itself, end to end, with no model and no scanner binaries:
# a poisoned ticket blocks even when the model does exactly what the reviewer
# asked and takes the credentials back out.
# --------------------------------------------------------------------------


def test_a_poisoned_run_blocks_even_when_the_model_removes_the_key(monkeypatch):
    """The live failure, reproduced deterministically.

    Measured against a live model, the poisoned ticket blocked on 2 runs of 5;
    two promoted and one failed at review. This is the shape of the two that
    promoted, driven by a scripted model instead: the reviewer objects to the
    hardcoded credentials, the developer complies and returns a diff whose only
    AKIA... sits on a `-` line, and the reviewer then approves the cleaned-up
    change. Before the fix this run reached `promoted` -- the scanners were
    handed a change with no secret in it and correctly passed it.

    The fan-out is replaced with a stand-in that models the one property of the
    real scanners this turns on -- they only ever read added lines -- so the
    test measures the same thing whether or not gitleaks is installed. Letting
    the real fan-out raise instead would send security.run down its fixture
    fallback, whose `_looks_poisoned` is a whole-diff substring check: that
    blocks a removal-only diff too, so this test would have passed BEFORE the
    fix and proved nothing.
    """
    from agentorg.common import llm

    calls = {"develop": 0, "review": 0}

    def scripted_model(system_prompt, user_prompt):
        """One scripted reply per agent. Anything unrecognised fails loudly."""
        if system_prompt == planner.SYSTEM_PROMPT:
            return json.dumps({
                "tasks": ["add a per-IP limiter"],
                "acceptance_criteria": ["429 on the 6th attempt"],
                "target_files": ["app/auth.py"],
                "notes": "",
            })
        if system_prompt == developer.SYSTEM_PROMPT:
            calls["develop"] += 1
            return json.dumps({
                "branch": "",
                "diff": _KEY_ONLY_ON_A_REMOVAL_LINE,
                "summary": "reads the credentials from the environment",
                "files_changed": ["app/auth.py"],
            })
        if system_prompt == reviewer.SYSTEM_PROMPT:
            calls["review"] += 1
            if calls["review"] == 1:
                return json.dumps({
                    "verdict": "changes_requested",
                    "comments": [],
                    "must_fix": ["remove the hardcoded AWS credentials"],
                })
            return json.dumps({"verdict": "approve", "comments": [], "must_fix": []})
        if system_prompt == security.SYSTEM_PROMPT:
            return "The scanners found hardcoded AWS credentials."
        if system_prompt == testgen.SYSTEM_PROMPT:
            # A SIXTH AGENT NOW RUNS ON THIS PATH, and this stub is deliberately
            # scripted per-agent rather than answering everything -- so wiring Lane G's
            # testgen into `_walk` made this test fail with "unscripted agent reached
            # the model". That is the guard WORKING: a new model call on the poisoned
            # path is exactly what it exists to announce.
            #
            # Answered with an empty generation, because this test is about the block
            # rule surviving a model that removes the key. A generated test that
            # claimed a failure would give the run a second reason to be blocked, and
            # the assertion below could no longer tell which one blocked it.
            return json.dumps({"files": [], "passed": 0, "failed": 0,
                               "binding": False, "source": "model", "notes": ""})
        pytest.fail(f"unscripted agent reached the model: {system_prompt[:60]!r}")

    def scanners_reading_only_added_lines(dev):
        """What the three wrappers do: materialise `+` lines, then scan those."""
        if _AWS_KEY_RE.search(_added_lines(dev.diff or "")):
            return list(_GITLEAKS_FINDINGS)
        return []

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", scripted_model)
    monkeypatch.setattr(security, "run_all_scanners", scanners_reading_only_added_lines)

    state = run_pipeline("POISON-1", "Add a per-IP login rate limit.", poisoned=True)

    assert state.status == "blocked"
    assert state.security.verdict == "block"
    assert len(state.security.blocking) == 2

    # The run really did take the revision path -- otherwise this passes on a
    # first-pass-only run, which is not the scenario that promoted.
    assert state.revision_count == 1
    assert calls["develop"] == 2, "the developer must have been asked to revise"
    assert state.review.verdict == "approve", (
        "the reviewer approved the cleaned-up diff; the block came from the "
        "scanners, not from the reviewer refusing"
    )


# ── the prompts must name the target's language ───────────────────────────────
#
# MEASURED 2026-08-22 on the deployed pipeline: the developer agent wrote GO for a
# Python Flask application -- `sync.RWMutex`, `NewRateLimiter` -- and the reviewer
# spent all four revisions objecting to it, so the run ended `failed` with the
# scanners reporting PASS.
#
# Neither prompt said what the target was. `_prompt` names target FILES but never
# their contents, and `target_repo/` is excluded from the container image
# (.dockerignore:48), so the agent genuinely could not look. It guessed.


def test_the_developer_prompt_names_the_targets_language():
    """Otherwise the agent guesses, and a wrong guess costs every revision."""
    from agentorg.agents import developer
    prompt = developer.SYSTEM_PROMPT.lower()
    assert "python" in prompt, (
        "the developer's system prompt does not say the target is Python. It names "
        "target files but never their contents, and target_repo/ is excluded from "
        "the image, so the agent cannot look -- measured writing Go for a Flask app."
    )
    assert "flask" in prompt, (
        "the prompt does not name the framework; 'Python' alone still leaves the "
        "agent inventing a web layer that does not match app/auth.py"
    )


def test_the_developer_prompt_names_gits_default_diff_prefixes():
    """A non-default prefix materialises zero files for the scanners.

    `common/diff.py` now accepts every legal spelling, so this is belt and braces
    rather than the only defence -- but asking for the shape the whole toolchain
    expects costs one line.
    """
    from agentorg.agents import developer
    assert "a/" in developer.SYSTEM_PROMPT and "b/" in developer.SYSTEM_PROMPT, (
        "the prompt does not ask for git's default `--- a/` `+++ b/` prefixes"
    )


def test_the_reviewer_prompt_distinguishes_wrong_from_merely_different():
    """A reviewer that blocks on preferences spends a budget it cannot refill.

    MEASURED: four rounds of `changes_requested` whose must_fix items were a
    different storage choice, a missing Retry-After header, absent cleanup timers
    and unrequested configurability. Each is defensible and none is a defect, and
    together they exhausted MAX_REVISION_LOOPS on a diff the scanners had cleared.
    """
    from agentorg.agents import reviewer
    prompt = reviewer.SYSTEM_PROMPT.lower()
    assert "wrong or unsafe" in prompt or ("wrong" in prompt and "unsafe" in prompt), (
        "the reviewer's prompt does not state the blocking standard as WRONG or "
        "UNSAFE, so preferences read as blocking issues"
    )
    # Asserted on the ROUTE, not on a phrase. An earlier version of this looked for
    # the literal "without blocking" and broke when the prompt was reworded to be
    # clearer -- a matcher keyed on wording, pinning nothing about behaviour.
    assert "comments" in prompt, (
        "the prompt never mentions `comments`, so the reviewer has nowhere to put a "
        "preference that is not blocking and every observation lands in must_fix"
    )
    assert "approve" in prompt and ("taste" in prompt or "preference" in prompt
                                   or "consider" in prompt), (
        "the prompt does not tell the reviewer to APPROVE despite preferences. "
        "Measured: four rounds of changes_requested whose must_fix items were a "
        "different storage choice, a missing Retry-After header, absent cleanup "
        "timers and unrequested configurability -- each defensible, none a defect, "
        "and together they exhausted the revision budget on a diff the scanners "
        "had cleared."
    )
