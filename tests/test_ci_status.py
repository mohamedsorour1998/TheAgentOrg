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
