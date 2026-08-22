"""The issue's own record: it links the PR, learns the ending, and closes.

OWNER: Sorour.

WHY THIS FILE EXISTS, and why it is separate from tests/test_agent_comments.py.
That file asks "did every stage post, and to which surface". This one asks a
different question about the same seam: WHAT THE ISSUE ITSELF ENDS UP LOOKING LIKE
to somebody who only ever opens the issue -- which is what a judge does, because
the issue is the artifact they were handed.

Three claims, none of which had a test before this file:

  1. the pull request is LINKED to the issue, in GitHub's own Development sidebar,
     which needs a closing keyword in the PR BODY -- a number in the title does not
     do it
  2. the run's ending is posted back to the issue
  3. the issue is CLOSED, with GitHub's own reason: `completed` when the change
     shipped, `not_planned` when it did not

Each of the three writes to somebody else's repository, so each is stubbed here at
`github_ops._repo` -- the same seam conftest guard 2 slams shut -- and opted back in
per test, with all four layers, per that guard's own instructions.

WHAT MAKES THE STUB ABLE TO FAIL. It records `edit(**kwargs)` verbatim rather than
a boolean, because "closed" and "closed for the right reason" are different facts
and a boolean cannot tell them apart: a promoted change closed as `not_planned`
reads, in a list of issues, as work that was abandoned. Same discipline as
_RecordingRepo in test_agent_comments.py, which records the issue NUMBER rather than
call order.
"""

from types import SimpleNamespace

import pytest
from github import GithubException

from agentorg import github_ops
from agentorg.common import config
from agentorg.state import DevResult, Finding, RunState, SecurityResult

ISSUE_NUMBER = 7
ISSUE_TICKET = "7"
PR_NUMBER = 41


class _Issue:
    """One issue, recording every comment body and every `edit` call verbatim."""

    def __init__(self, number: int):
        self.number = number
        self.comments: list[str] = []
        self.edits: list[dict] = []

    def create_comment(self, body: str):
        self.comments.append(body)
        return SimpleNamespace(
            html_url=f"https://github.com/someone/auth-service/issues/"
                     f"{self.number}#issuecomment-{len(self.comments)}"
        )

    def edit(self, **kwargs):
        self.edits.append(kwargs)


class _PR:
    def __init__(self, number: int):
        self.number = number
        self.html_url = f"https://github.com/someone/auth-service/pull/{number}"
        self.merged_flag = False

    def merge(self, **kwargs):
        self.merged_flag = True
        return SimpleNamespace(merged=True, sha="deadbeef")


class _Pulls:
    def __init__(self, pr):
        self._pr = pr
        self.totalCount = 1 if pr is not None else 0

    def __getitem__(self, index):
        return self._pr


class _Repo:
    """The PyGithub repo handle, recording what was written where."""

    def __init__(self, pr=None):
        self.issues: dict[int, _Issue] = {}
        self.pr = pr
        self.owner = SimpleNamespace(login="someone")
        self.created_pulls: list[dict] = []
        self.written: list[tuple[str, str | None]] = []

    def get_issue(self, number):
        return self.issues.setdefault(number, _Issue(number))

    def get_pulls(self, **kwargs):
        return _Pulls(self.pr)

    def create_pull(self, **kwargs):
        self.created_pulls.append(kwargs)
        pr = _PR(PR_NUMBER)
        self.pr = pr
        return pr

    def create_git_ref(self, **kwargs):
        pass

    def get_branch(self, name):
        return SimpleNamespace(commit=SimpleNamespace(sha="c0ffee"))

    def get_contents(self, path, ref=None):
        # A REAL GithubException with status 404, because that is what `open_pr`
        # branches on -- `except GithubException as e: if e.status == 404`. A plain
        # RuntimeError would escape that handler and this stub would be testing the
        # error path instead of the create-file one.
        raise GithubException(404, {"message": "Not Found"}, None)

    # POSITIONAL, because that is how `open_pr` calls them:
    # `repo.create_file(path, message, dev.diff, branch=branch)`. A `**kwargs`-only
    # stub raises TypeError instead of recording the call -- measured, and it took
    # both PR tests down before the signatures matched the real ones.
    def create_file(self, path, message, content, branch=None, **kwargs):
        self.written.append((path, branch))

    def update_file(self, path, message, content, sha, branch=None, **kwargs):
        self.written.append((path, branch))


@pytest.fixture
def repo(monkeypatch):
    """Opt into the online path -- ALL FOUR LAYERS, per tests/conftest.py.

    The policy knob alone is not enough: opting in with `OFFLINE=False` while leaving
    the `_repo` raiser in place reproduces the exact bug guard 2 exists to catch, and
    the raiser's `pytest.fail` derives from BaseException so the blind
    `except Exception` in these functions cannot swallow it into a green pass.
    """
    handle = _Repo()
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")
    monkeypatch.setattr(github_ops, "_repo", lambda: handle)
    return handle


def _state(status: str, *, ticket: str = ISSUE_TICKET, **kw) -> RunState:
    state = RunState(ticket_id=ticket, ticket_text="Rate-limit login", **kw)
    state.status = status
    return state


def _dev(branch: str = "feat/rate-limit") -> DevResult:
    return DevResult(
        branch=branch,
        diff="--- a/app/auth.py\n+++ b/app/auth.py\n+# limited\n",
        summary="Adds a per-IP rate limit to the login view.",
        files_changed=["app/auth.py"],
        pr_url=f"https://github.com/someone/auth-service/pull/{PR_NUMBER}",
    )


# =========================================================================
# 1. THE PULL REQUEST IS LINKED TO THE ISSUE
#
# GitHub populates an issue's Development sidebar from a CLOSING KEYWORD in the
# pull request's body. Nothing else does it -- not a matching title, not a
# comment naming the issue, not the branch name.
# =========================================================================

def test_the_pr_body_carries_a_closing_keyword_for_its_issue(repo):
    """THE test for the link. Without the keyword the sidebar stays empty."""
    github_ops.open_pr(_state("running", dev=_dev()))

    assert repo.created_pulls, "no pull request was created at all"
    body = repo.created_pulls[0]["body"]
    assert f"Closes #{ISSUE_NUMBER}" in body, (
        f"the PR body carries no closing keyword, so GitHub will not link this pull "
        f"request in issue #{ISSUE_NUMBER}'s Development sidebar -- a reader who "
        f"opens the issue has no route to the work it produced. Body was:\n{body}"
    )
    assert "Adds a per-IP rate limit" in body, (
        "the closing keyword replaced the developer's summary instead of joining it"
    )


def test_a_ticket_that_is_not_an_issue_number_gets_no_closing_keyword(repo):
    """`Closes #1` for a ticket called POISON-1 would close a stranger's issue.

    The same refusal `_issue_number` makes for comments, at a place where the cost is
    higher: a comment on the wrong issue is noise, but a closing keyword CLOSES it
    when the PR merges. Every ticket id this repo uses locally -- POISON-1, CLEAN-1,
    DEMO-1, T-1 -- would resolve to #1 under a loose parse.
    """
    github_ops.open_pr(_state("running", ticket="POISON-1", dev=_dev()))

    body = repo.created_pulls[0]["body"]
    assert "Closes #" not in body, (
        f"a ticket id that is not an issue number produced a closing keyword "
        f"anyway, which would close whichever issue it resolved to. Body:\n{body}"
    )


# =========================================================================
# 2. AND 3. THE ENDING IS REPORTED, AND THE ISSUE CLOSES
#
# Parametrised over every status in the frozen contract, because the endings
# that matter most are the ones a happy-path-only test would miss.
# =========================================================================

def test_a_promoted_run_closes_its_issue_as_completed(repo):
    state = _state("promoted", dev=_dev())
    state.security = SecurityResult(
        verdict="pass", findings=[], blocking=[],
        explanation="clean", scan_provenance="scanners",
    )

    ref = github_ops.report_outcome(state)

    issue = repo.issues[ISSUE_NUMBER]
    assert issue.comments, "the outcome was never posted to the issue"
    assert "ACCEPTED" in issue.comments[0], issue.comments[0]
    assert issue.edits == [{"state": "closed", "state_reason": "completed"}], (
        f"a merged change must close its issue as `completed`, which is how a reader "
        f"scanning a list of issues sees it shipped. Got: {issue.edits}"
    )
    assert ref.startswith("https://"), f"undelivered ref for a posted comment: {ref}"


def test_a_blocked_run_closes_its_issue_as_not_planned_and_names_the_findings(repo):
    """THE POISONED HALF, and the reason this whole function exists.

    The block is the demo's point. Before this, the issue that asked for the work
    learned only that a plan had been made -- the verdict landed on the pull request
    and the issue stayed open forever, reading as work still pending.
    """
    finding = Finding(
        tool="gitleaks", severity="critical", rule="aws-access-key-id",
        file="app/auth.py", line=3, description="hardcoded AWS key",
    )
    state = _state("blocked", dev=_dev())
    state.security = SecurityResult(
        verdict="block", findings=[finding], blocking=[finding],
        explanation="an AWS key is committed in the diff",
        scan_provenance="scanners",
    )

    github_ops.report_outcome(state)

    issue = repo.issues[ISSUE_NUMBER]
    body = issue.comments[0]
    assert "REJECTED" in body, body
    # The findings, not just the verdict: "blocked" alone tells a reader nothing
    # about what to fix, and this comment may be the only thing they read.
    assert "aws-access-key-id" in body, body
    assert "app/auth.py:3" in body, body
    assert "scanners" in body, "the provenance is missing, so a fixture-fallback " \
                               "verdict would be indistinguishable from a real scan"
    assert issue.edits == [{"state": "closed", "state_reason": "not_planned"}], (
        f"a blocked change must close its issue as `not_planned`: the pipeline "
        f"reached a verdict and it was no, which is an answer rather than an "
        f"abandonment. Got: {issue.edits}"
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [("rejected", "not_planned"), ("failed", "not_planned")],
)
def test_every_terminal_status_closes_the_issue(repo, status, reason):
    """A human refusal and an exhausted revision cap are endings too."""
    github_ops.report_outcome(_state(status, dev=_dev()))

    issue = repo.issues[ISSUE_NUMBER]
    assert issue.comments, f"status {status!r} posted no outcome comment"
    assert issue.edits == [{"state": "closed", "state_reason": reason}], (
        f"status {status!r} did not close the issue: {issue.edits}"
    )


def test_a_still_running_run_reports_but_does_not_close(repo):
    """The one status that must NOT close, and the reason it is not a special case.

    `_emit` calls `report_outcome` only on a terminal status, so this should never
    happen on the cloud path -- but that gate is one `if` in another file, and this
    function is the one that would do the damage. An issue closed mid-run tells a
    reader the work is over while three gates are still waiting for a click.
    """
    github_ops.report_outcome(_state("running", dev=_dev()))

    issue = repo.issues[ISSUE_NUMBER]
    assert issue.comments, "even an incomplete run should say so"
    assert issue.edits == [], (
        f"a run that has not ended closed its issue anyway: {issue.edits}"
    )


def test_an_unknown_status_is_reported_rather_than_raising(repo):
    """`report_outcome` may not raise, and an unrecognised status is the trap.

    Read with `_OUTCOME_HEADLINE[status]`, a status added to the frozen contract
    later would raise KeyError from the last thing a run does -- losing the ending in
    order to complain about not recognising it. The status is named verbatim instead.
    """
    state = _state("promoted", dev=_dev())
    state.status = "quiesced"  # not in the contract; bypasses validation on purpose

    ref = github_ops.report_outcome(state)

    body = repo.issues[ISSUE_NUMBER].comments[0]
    assert "quiesced" in body, (
        f"an unrecognised status must be named in the comment, since that string is "
        f"the only clue a reader gets. Body:\n{body}"
    )
    assert ref.startswith("https://"), ref
    # Not in _CLOSING_STATUSES, so it must not close: we do not know what happened.
    assert repo.issues[ISSUE_NUMBER].edits == []


def test_a_ticket_that_is_not_an_issue_number_posts_nothing_and_closes_nothing(repo):
    """Every locally-driven run. Not an error, and it must not guess a number."""
    ref = github_ops.report_outcome(_state("promoted", ticket="DEMO-1", dev=_dev()))

    assert repo.issues == {}, (
        f"a run whose ticket id is not an issue number wrote on an issue anyway: "
        f"{sorted(repo.issues)}"
    )
    assert ref == "comment://" + _state("promoted", ticket="DEMO-1").run_id[:0] + \
        ref.split("comment://")[-1], "the ref should be an undelivered comment:// ref"


def test_a_github_failure_while_closing_does_not_lose_the_comment(repo,
                                                                 monkeypatch):
    """The comment is the load-bearing half; an issue left open is untidy, not wrong.

    Ordered deliberately: the comment goes out FIRST, so a locked conversation, a
    rate limit or an expired token at close time cannot take the ending with it.
    """
    issue = repo.get_issue(ISSUE_NUMBER)

    def _boom(**kwargs):
        raise RuntimeError("403 Resource not accessible by personal access token")

    monkeypatch.setattr(issue, "edit", _boom)

    ref = github_ops.report_outcome(_state("promoted", dev=_dev()))

    assert issue.comments, "the outcome comment was lost to a failure to close"
    assert ref.startswith("https://"), (
        f"the ref must still report the comment as delivered, because it was: {ref}"
    )


def test_report_outcome_never_raises_even_when_the_whole_seam_is_broken(monkeypatch):
    """The hard contract, tested at the widest failure: no repo handle at all.

    This is the last thing a run does, after the status is decided and saved. An
    exception here would replace a correct verdict with a traceback on the projector,
    which is precisely the trade `post_comment` and `merge_pr` also refuse.
    """
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")

    def _no_repo():
        raise RuntimeError("Bad credentials")

    monkeypatch.setattr(github_ops, "_repo", _no_repo)

    ref = github_ops.report_outcome(_state("blocked", dev=_dev()))

    assert ref.startswith("comment://"), (
        f"a broken seam must yield an undelivered ref, not a delivered-looking one: "
        f"{ref}"
    )


# =========================================================================
# THE TABLE ITSELF
# =========================================================================

def test_every_status_in_the_frozen_contract_has_a_headline():
    """A status with no entry falls to the unknown default, which says less.

    Read off `RunState`'s own annotation rather than a restated list: a status added
    to the contract must fail here, and a list copied into this file would not notice.
    """
    import typing

    hints = typing.get_type_hints(RunState)
    statuses = set(typing.get_args(hints["status"]))
    assert statuses, "could not read RunState.status's literals; this test pins nothing"

    missing = statuses - set(github_ops._OUTCOME_HEADLINE)
    assert not missing, (
        f"these run statuses have no outcome headline, so they would be reported "
        f"through the generic unknown default: {sorted(missing)}"
    )


def test_the_closing_statuses_are_the_terminal_ones_only():
    """`running` must not be in the closing set, and every other status must be."""
    import typing

    statuses = set(typing.get_args(typing.get_type_hints(RunState)["status"]))
    assert github_ops._CLOSING_STATUSES == statuses - {"running"}, (
        f"the closing set has drifted from the contract: "
        f"{sorted(github_ops._CLOSING_STATUSES)} vs {sorted(statuses - {'running'})}"
    )
