"""`ci_status` must distinguish "no CI exists" from "CI is running" from "CI passed".

MEASURED 2026-08-22 on the target repo before it had any workflow:

    gh api repos/mohamedsorour1998/auth-service/contents/.github/workflows
    {"message":"Not Found","status":"404"}
    gh api repos/.../commits/<sha>/status --jq '{state, total_count}'
    {"state":"pending","total_count":0}

GitHub reports `pending` when NOTHING has run. Reading `state` naively therefore
calls an unchecked commit pending, and treating pending as go would make the SRE
agent report "CI passing" about a repository that has never run a test. That is
the fail-open shape the security lane exists to prevent, one agent over.

**This must work for a target repo with CI and one without.** `unknown` is a
first-class answer, not an error -- another lane is adding CI to `auth-service`
concurrently, and nothing here may assume that landed.

Every test stubs `github_ops._repo`, the seam conftest already guards, so nothing
reaches the network.
"""

import typing

from agentorg import github_ops
from agentorg.state import DevResult, RunState, SREResult


class _FakeCheckRun:
    """One check run, in the two fields ci_status is allowed to read.

    `status` and `conclusion` are separate on purpose: GitHub leaves
    `conclusion` None while a check is still running, and that pair is the only
    way to tell "in progress" from "finished and green".
    """

    def __init__(self, conclusion, status="completed"):
        self.conclusion = conclusion
        self.status = status


class _FakePaginated(list):
    """A PyGithub PaginatedList, in the two ways ci_status uses one.

    `totalCount` is PyGithub's own spelling, and it is why this class exists at
    all rather than a plain list: reading `len()` would work here and NOT against
    the real API, so a test double without it could not express the case where
    the count and the iteration disagree.
    """

    @property
    def totalCount(self):
        return len(self)


class _FakeCommit:
    def __init__(self, runs):
        self._runs = runs

    def get_check_runs(self):
        return _FakePaginated(self._runs)


class _FakeBranch:
    def __init__(self, sha):
        self.commit = type("C", (), {"sha": sha})()


class _FakeRepo:
    def __init__(self, runs):
        self._runs = runs
        self.asked_for = []

    def get_branch(self, name):
        self.asked_for.append(name)
        return _FakeBranch("deadbeef")

    def get_commit(self, sha):
        return _FakeCommit(self._runs)


def _online(monkeypatch, runs):
    """Put the test on the online path with a fake repo. Returns the fake."""
    repo = _FakeRepo(runs)
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: repo)
    return repo


def _state() -> RunState:
    state = RunState(ticket_id="7", ticket_text="Add a per-IP login rate limit.")
    state.dev = DevResult(branch="agent-org/7-abc1234", diff="", summary="s",
                          files_changed=["app/auth.py"])
    return state


# --------------------------------------------------------------------------
# The vocabulary, read from the contract rather than restated
# --------------------------------------------------------------------------

def test_the_three_answers_are_exactly_the_contracts_three():
    """Read from the frozen contract, not restated.

    A fourth spelling would fail SREResult validation at a distance, inside the
    SRE agent, rather than at this boundary.
    """
    allowed = set(typing.get_args(typing.get_type_hints(SREResult)["ci_status"]))
    assert allowed == {"passing", "failing", "unknown"}, (
        f"SREResult.ci_status now admits {sorted(allowed)}; ci_status() must be "
        f"updated to match, and this test is the tripwire"
    )


def test_every_answer_is_one_of_the_contracts_three(monkeypatch):
    """The anti-vacuity check for the test above.

    Reading the vocabulary proves nothing unless the function is held to it.
    Every scenario below is driven through the real function and its answer
    checked for membership, so a typo'd `"pass"` fails here rather than inside
    SREResult validation one agent later.
    """
    allowed = set(typing.get_args(typing.get_type_hints(SREResult)["ci_status"]))
    scenarios = {
        "no checks": [],
        "one green": [_FakeCheckRun("success")],
        "one red": [_FakeCheckRun("failure")],
        "still running": [_FakeCheckRun(None, status="in_progress")],
        "cancelled": [_FakeCheckRun("cancelled")],
        "skipped": [_FakeCheckRun("skipped")],
    }
    for label, runs in scenarios.items():
        _online(monkeypatch, runs)
        answer = github_ops.ci_status(_state())
        assert answer in allowed, (
            f"the {label!r} case answered {answer!r}, which is not one of "
            f"{sorted(allowed)}. SREResult would reject it one agent later, "
            f"inside the SRE stage, rather than here at the boundary."
        )


# --------------------------------------------------------------------------
# Zero checks -- the measured case, and the whole reason for a third value
# --------------------------------------------------------------------------

def test_zero_checks_is_unknown_not_passing(monkeypatch):
    """The measured case: a repository with no CI at all.

    `unknown` and not `passing`, because a commit nothing has checked is not a
    green commit -- and not `failing`, because nothing failed. This is why the
    third value exists.
    """
    _online(monkeypatch, [])
    assert github_ops.ci_status(_state()) == "unknown", (
        "a head commit with zero check runs must be `unknown`. GitHub reports "
        "`pending` for this, and treating pending as passing would claim CI "
        "passed on a repository that has never run a test. MEASURED on the "
        "target repo: {\"state\": \"pending\", \"total_count\": 0}."
    )


# --------------------------------------------------------------------------
# The ordinary answers
# --------------------------------------------------------------------------

def test_all_successful_is_passing(monkeypatch):
    """The complement of everything below: a real green build must read green.

    Without this the function could return `unknown` unconditionally and every
    fail-closed test in this file would still pass.
    """
    _online(monkeypatch, [_FakeCheckRun("success"), _FakeCheckRun("success")])
    assert github_ops.ci_status(_state()) == "passing"


def test_any_failure_is_failing(monkeypatch):
    """One red check outweighs any number of green ones."""
    _online(monkeypatch, [
        _FakeCheckRun("success"), _FakeCheckRun("failure"), _FakeCheckRun("success"),
    ])
    assert github_ops.ci_status(_state()) == "failing", (
        "a failing check among passing ones must make the whole status failing; "
        "a majority vote on CI results is not a thing"
    )


def test_a_still_running_check_is_unknown_not_passing(monkeypatch):
    """In progress is not green. Treating it as green merges before CI finishes."""
    _online(monkeypatch, [_FakeCheckRun(None, status="in_progress")])
    assert github_ops.ci_status(_state()) == "unknown", (
        "a check with no conclusion has not finished, so the honest answer is "
        "`unknown`. Calling it passing would let a merge land before CI ran."
    )


def test_one_unfinished_check_among_green_ones_is_unknown(monkeypatch):
    """The partial case, which is what a real PR looks like mid-run.

    Not `passing`: the checks that HAVE finished are green, but the one still
    running is the one that might not be. Reading only the finished ones is how
    a merge lands during CI while looking fully informed.
    """
    _online(monkeypatch, [
        _FakeCheckRun("success"), _FakeCheckRun(None, status="in_progress"),
    ])
    assert github_ops.ci_status(_state()) == "unknown", (
        "two green checks and one still running answered `passing`; the "
        "unfinished check is precisely the one whose result is not in yet"
    )


def test_a_cancelled_or_timed_out_check_is_not_passing(monkeypatch):
    """Neither success nor a clean failure.

    Anything that is not `success` and not a running check is treated as
    failing, because promoting on a cancelled check is promoting on no
    information while looking decided.
    """
    for conclusion in ("cancelled", "timed_out", "action_required", "stale"):
        _online(monkeypatch, [_FakeCheckRun(conclusion)])
        assert github_ops.ci_status(_state()) == "failing", (
            f"conclusion {conclusion!r} was treated as passing"
        )


def test_neutral_and_skipped_do_not_fail_the_build(monkeypatch):
    """GitHub's own semantics: `neutral` and `skipped` are not failures.

    A repository with a path-filtered workflow reports `skipped` on commits the
    filter excludes, and calling that a failure would block every such change.
    """
    _online(monkeypatch, [
        _FakeCheckRun("success"), _FakeCheckRun("skipped"), _FakeCheckRun("neutral"),
    ])
    assert github_ops.ci_status(_state()) == "passing"


# --------------------------------------------------------------------------
# It never raises, and no failure mode becomes a green light
# --------------------------------------------------------------------------

def test_the_offline_path_answers_unknown_rather_than_raising(monkeypatch):
    """The whole suite and every local run take this path.

    `unknown` is the honest answer with no GitHub to ask -- and it must not
    raise, because the SRE agent calls this on every run.

    Note this test does NOT stub `_repo`: conftest's raiser is still in place, so
    if the offline branch ever started reaching the seam this would fail by name
    rather than silently going online.
    """
    monkeypatch.setattr(github_ops, "_use_local", lambda: True)
    assert github_ops.ci_status(_state()) == "unknown"


def test_a_github_failure_is_unknown_not_passing(monkeypatch):
    """The seam raising must not become a green light."""
    def _boom():
        raise RuntimeError("api down")

    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", _boom)
    assert github_ops.ci_status(_state()) == "unknown", (
        "an unreachable GitHub must be `unknown`; returning `passing` would make "
        "an outage look like a green build"
    )


def test_a_branch_that_does_not_exist_is_unknown(monkeypatch):
    """A 404 on the branch lookup is the ordinary case before open_pr pushes.

    Reached through get_branch rather than through _repo, so it exercises the
    try's second statement -- a guard placed only around the client construction
    would let this one escape.
    """
    class _NoSuchBranch(_FakeRepo):
        def get_branch(self, name):
            raise RuntimeError("404 Branch not found")

    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _NoSuchBranch([]))
    assert github_ops.ci_status(_state()) == "unknown"


def test_no_dev_branch_is_unknown_without_asking_github(monkeypatch):
    """Called before `open_pr`, there is no head to look up.

    And it must not ASK: `get_branch("")` is not a query that selects nothing,
    so a lookup built from an empty branch can come back with something. Same
    refusal, and the same reasoning, as post_comment's empty-branch guard.
    """
    repo = _online(monkeypatch, [_FakeCheckRun("success")])
    state = RunState(ticket_id="7", ticket_text="x")
    assert github_ops.ci_status(state) == "unknown"
    assert repo.asked_for == [], (
        f"ci_status asked GitHub about {repo.asked_for} with no dev branch on "
        f"the state. An empty branch name is not a filter that selects nothing."
    )


def test_an_empty_branch_string_is_unknown_without_asking_github(monkeypatch):
    """`state.dev` exists but `branch` is still "" -- the shape between the
    developer returning and open_pr running."""
    repo = _online(monkeypatch, [_FakeCheckRun("success")])
    state = RunState(ticket_id="7", ticket_text="x")
    state.dev = DevResult(branch="", diff="", summary="s", files_changed=[])
    assert github_ops.ci_status(state) == "unknown"
    assert repo.asked_for == [], f"asked GitHub about {repo.asked_for}"


def test_it_reads_the_runs_branch_not_a_hardcoded_one(monkeypatch):
    """The head it checks must be THIS run's, or the answer is about main.

    A lookup of `main` would report the target repo's default-branch CI, which
    on a healthy repo is green -- so the SRE agent would read `passing` for
    every change, including one whose own checks failed. Green for the wrong
    commit is the worst available answer.
    """
    repo = _online(monkeypatch, [_FakeCheckRun("success")])
    github_ops.ci_status(_state())
    assert repo.asked_for == ["agent-org/7-abc1234"], (
        f"ci_status looked up {repo.asked_for}, not the run's own branch. "
        f"Reading main's status would report green for every change."
    )


# ── EVERY `unknown` MUST SAY WHY ──────────────────────────────────────────────
#
# `unknown` has three causes and they call for opposite actions:
#
#     no seam / no branch    nothing to look up; expected on every local run
#     zero check runs        nothing has examined this commit
#     the lookup RAISED      we could not look -- CI may well be green
#
# The third was logged at DEBUG, invisible in a job log. MEASURED on the clean demo
# run: the SRE reported `CI unknown` while both check runs on that exact commit were
# `completed/success`, finished 49 seconds before the stage asked.
#
# THAT RUN'S CAUSE WAS THE FIRST ONE, not this third one, and the distinction is why
# these tests exist. `sre.run` executed inside a container with no GitHub token, so
# `_use_local()` was True and this function returned `unknown` on its first line
# without calling GitHub -- fixed by measuring on the runner
# (`RunState.ci_status_measured`). A first diagnosis blamed a missing `Checks: read`
# scope; the deployed token answers **HTTP 200** on both endpoints, so that was wrong.
# Three causes, one return value, and no way to tell them apart is exactly how a
# wrong diagnosis gets written down.
#
# The verdict was correct and fail-safe throughout. The missing REASON is the defect
# -- same class as a silent fixture fallback: a defensible answer with no way to tell
# which question it answered.

def test_a_lookup_failure_is_reported_at_warning_not_debug(monkeypatch, caplog):
    """THE test. A 403 from a token without Checks:read must be visible."""
    class _Raises:
        def get_branch(self, name):
            raise RuntimeError("403 Resource not accessible by personal access token")

    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _Raises())

    with caplog.at_level("WARNING", logger="agentorg.github_ops"):
        assert github_ops.ci_status(_state()) == "unknown"

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, (
        "a failed CI lookup produced no WARNING, so `CI unknown` on a pull request "
        "is indistinguishable from `this repository has no CI` -- which is what sent "
        "a real diagnosis looking at the wrong thing for half an hour"
    )
    message = warnings[0].getMessage()
    assert "403" in message, f"the exception is not named in the log line: {message}"
    assert "could not look" in message, (
        f"the log line does not distinguish `we could not look` from `nothing ran`, "
        f"which is the whole reason it is at WARNING: {message}"
    )


def test_zero_checks_and_a_failed_lookup_do_not_log_the_same_thing(monkeypatch,
                                                                  caplog):
    """Both answer `unknown`; a reader must still be able to tell them apart.

    This is the assertion the return value cannot make. `unknown == unknown`, so if
    the two paths logged the same sentence the distinction would exist only in the
    source -- and the whole point of the field is that somebody reading a job log can
    act on it.
    """
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)

    _online(monkeypatch, [])
    with caplog.at_level("INFO", logger="agentorg.github_ops"):
        assert github_ops.ci_status(_state()) == "unknown"
    zero_checks = " ".join(r.getMessage() for r in caplog.records)
    caplog.clear()

    class _Raises:
        def get_branch(self, name):
            raise RuntimeError("boom")

    monkeypatch.setattr(github_ops, "_repo", lambda: _Raises())
    with caplog.at_level("INFO", logger="agentorg.github_ops"):
        assert github_ops.ci_status(_state()) == "unknown"
    raised = " ".join(r.getMessage() for r in caplog.records)

    assert zero_checks and raised, "one of the two paths logged nothing at all"
    assert zero_checks != raised, (
        f"'nothing has run' and 'we could not look' log the same line, so the two "
        f"are indistinguishable to a reader:\n  {zero_checks}"
    )
    assert "zero check runs" in zero_checks, zero_checks


def test_an_unfinished_check_says_how_many(monkeypatch, caplog):
    """`unknown` mid-CI is ordinary, and the count is what says to wait."""
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    _online(monkeypatch, [_FakeCheckRun("success"),
                          _FakeCheckRun(None, status="in_progress")])

    with caplog.at_level("INFO", logger="agentorg.github_ops"):
        assert github_ops.ci_status(_state()) == "unknown"

    message = " ".join(r.getMessage() for r in caplog.records)
    assert "1 of 2" in message, (
        f"the log does not say how many checks are still running, so a mid-CI "
        f"`unknown` reads the same as a broken one: {message}"
    )


# ── THE MEASUREMENT MUST CROSS THE REMOTE SEAM ────────────────────────────────
#
# THE DEFECT, and it is structural rather than a mistake in this function. Under
# REMOTE_AGENTS=true, `sre.run` executes inside an AgentCore container whose entire
# environment is `AGENT_ROLE` and `DEMO_REPO` -- no GitHub token, deliberately, because
# a credential in five containers to read a public repository is five more places to
# leak one. So `_use_local()` is True in there and `ci_status` returns `unknown` on its
# FIRST LINE: no API call, no exception, nothing to log.
#
# MEASURED on the verified clean run: `CI unknown` on a pull request whose two check
# runs were `completed/success` 49 seconds earlier. The question was asked in the one
# place that structurally cannot answer it.
#
# Fixed the way `RunState.poisoned` was: measured on the runner, carried on the state.

def test_the_sre_agent_prefers_the_measurement_carried_on_the_state(monkeypatch):
    """THE test. The container cannot measure, so it must read.

    `ci_status` is patched to a raiser rather than to a value: if the agent consults
    it at all when the field is set, this fails loudly instead of silently agreeing.
    """
    from agentorg.agents import sre

    def _must_not_be_called(state):
        raise AssertionError(
            "sre.run called ci_status even though the state carries a measurement. "
            "In the container that call answers `unknown` without asking GitHub, "
            "which is the whole defect this field exists to fix."
        )

    monkeypatch.setattr(github_ops, "ci_status", _must_not_be_called)
    monkeypatch.setattr(sre.llm, "structured", lambda *a, **k: None)

    state = _state()
    state.ci_status_measured = "passing"
    result = sre.run(state)

    assert result.ci_status == "passing", result.ci_status
    assert result.verdict == "go"
    ci_check = [c for c in result.slo_checks if c.name == sre.CI_CHECK_NAME]
    assert ci_check and ci_check[0].passed, (
        f"the measured CI check does not reflect the carried value: {result.slo_checks}"
    )


def test_a_blank_measurement_means_measure_it_yourself(monkeypatch):
    """`""` is "nobody asked", NOT "unknown" -- the local path depends on it.

    A run written before the field existed, and every in-process run, must still get a
    real answer. Reading `""` as `unknown` would make the field a silent downgrade for
    exactly the path that CAN measure.
    """
    from agentorg.agents import sre

    asked = []

    def _measures(state):
        asked.append(state)
        return "failing"

    monkeypatch.setattr(github_ops, "ci_status", _measures)
    monkeypatch.setattr(sre.llm, "structured", lambda *a, **k: None)

    state = _state()
    assert state.ci_status_measured == "", "the field should default blank"
    result = sre.run(state)

    assert asked, "a blank measurement did not fall back to measuring"
    assert result.ci_status == "failing"
    assert result.verdict == "no_go", (
        "a failing CI must still produce no_go through the fallback path"
    )


def test_a_measured_unknown_is_carried_through_rather_than_re_measured(monkeypatch):
    """`unknown` measured on the runner is a real answer, not a blank.

    This is the falsy-value trap the `or` in sre.run could have walked into: if
    `unknown` were treated as "no measurement", the container would re-measure and get
    `unknown` anyway -- the same answer for the wrong reason, and the test would pass
    while pinning nothing. So the raiser proves it is NOT re-measured.
    """
    from agentorg.agents import sre

    def _must_not_be_called(state):
        raise AssertionError("a measured `unknown` was discarded and re-measured")

    monkeypatch.setattr(github_ops, "ci_status", _must_not_be_called)
    monkeypatch.setattr(sre.llm, "structured", lambda *a, **k: None)

    state = _state()
    state.ci_status_measured = "unknown"
    result = sre.run(state)

    assert result.ci_status == "unknown"
    assert result.verdict == "go", "unknown must still proceed; only failing is no_go"


def test_the_field_is_optional_and_defaults_blank():
    """An ADDITION to the frozen contract: every existing RunState must still load."""
    state = RunState(ticket_id="7", ticket_text="x")
    assert state.ci_status_measured == ""
    # And a state serialised before the field existed must round-trip.
    old = state.model_dump_json(exclude={"ci_status_measured"})
    assert RunState.model_validate_json(old).ci_status_measured == ""
