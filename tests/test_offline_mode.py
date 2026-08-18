"""Offline mode: a real local branch and a NOTES file, no network. Owner: Mariam.

This is the demo's insurance policy, so the tests are written to fail if the
offline path only *looks* like it worked:

  * The commit test reads the diff back out of the OBJECT STORE
    (`git show <branch>:<path>`), not off the working tree. The working-tree
    copy is written by a plain `open(...).write()` a line earlier, so asserting
    on it would pass even if `git add`/`git commit` were deleted -- something
    else writes those same bytes.
  * The re-run test counts commits on the branch rather than only comparing
    branch names. `dev.branch` is a hash of the diff, so it is equal across two
    runs whatever the git code does; the claim worth pinning is that `-B` RESETS
    the branch, leaving one commit on top of main instead of two (or an error).
  * The NOTES test posts TWICE with two distinct bodies. A single call cannot
    tell `open(..., "a")` from `open(..., "w")`, and "appends" is the claim.
"""

import logging
import subprocess
from types import SimpleNamespace

import pytest

from agentorg import github_ops, log
from agentorg.common import config
from agentorg.state import DevResult, RunState

POISON_KEY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.setattr(config, "OFFLINE_REPO", str(tmp_path / "offline-demo"))
    monkeypatch.setattr(config, "OFFLINE_NOTES", str(tmp_path / "offline-demo" / "NOTES.md"))
    return tmp_path / "offline-demo"


def _git(*args: str, cwd) -> str:
    """git in a scratch repo -- inspecting one for assertions, or building one."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout


def _state() -> RunState:
    state = RunState(ticket_id="POISON-1", ticket_text="Add a rate limit.")
    state.dev = DevResult(
        branch="",
        diff=f'AWS_ACCESS_KEY_ID = "{POISON_KEY}"\n',
        summary="adds a limiter",
        files_changed=["app/auth.py"],
    )
    return state


def test_open_pr_creates_a_real_local_branch(offline):
    state = _state()
    dev = github_ops.open_pr(state)

    assert dev.branch.startswith("agent-org/POISON-1-")
    assert dev.pr_url == f"local://{dev.branch}"
    branches = _git("branch", "--list", "agent-org/*", cwd=offline)
    assert dev.branch in branches


def test_open_pr_commits_the_diff(offline):
    state = _state()
    dev = github_ops.open_pr(state)

    # Out of the object store, not off the working tree -- see module docstring.
    committed = _git("show", f"{dev.branch}:changes/POISON-1.diff", cwd=offline)
    assert POISON_KEY in committed


def test_open_pr_is_rerun_safe(offline):
    state = _state()
    first = github_ops.open_pr(state)
    second = github_ops.open_pr(state)

    assert first.branch == second.branch
    # `-B` resets the branch to main, so a second run replaces the commit
    # rather than stacking a second one -- and does not raise the way `-b` would.
    assert _git("rev-list", "--count", f"main..{second.branch}", cwd=offline).strip() == "1"
    assert POISON_KEY in _git("show", f"{second.branch}:changes/POISON-1.diff", cwd=offline)


def test_post_comment_appends_to_notes(offline):
    state = _state()
    github_ops.open_pr(state)
    ref = github_ops.post_comment(state, "Blocked: hardcoded AWS key.")

    notes = (offline / "NOTES.md").read_text()
    assert "POISON-1" in notes
    assert "Blocked: hardcoded AWS key." in notes
    assert ref.startswith("local://")


def test_post_comment_keeps_the_earlier_comments(offline):
    """A second comment must not overwrite the first -- 'a', not 'w'."""
    state = _state()
    github_ops.post_comment(state, "first: gitleaks found a key.")
    github_ops.post_comment(state, "second: semgrep agrees.")

    notes = (offline / "NOTES.md").read_text()
    assert "first: gitleaks found a key." in notes
    assert "second: semgrep agrees." in notes
    assert notes.index("first:") < notes.index("second:")


def test_post_comment_survives_a_notes_path_with_no_directory(tmp_path, monkeypatch):
    """OFFLINE_NOTES may be a bare filename; os.path.dirname is "" for those."""
    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "OFFLINE_NOTES", "NOTES.md")

    github_ops.post_comment(_state(), "bare filename still records the reason.")

    assert "bare filename still records the reason." in (tmp_path / "NOTES.md").read_text()


def test_missing_credentials_also_take_the_local_git_path(offline, monkeypatch):
    """CI has no token either, so absent credentials must do the same real work.

    Nothing here fakes the GitHub API: if this ever left the local path it would
    hit conftest's `_unpatched_repo`, which fails the test by name.
    """
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "")
    monkeypatch.setattr(config, "GITHUB_REPO", "")

    dev = github_ops.open_pr(_state())

    assert dev.pr_url == f"local://{dev.branch}"
    assert POISON_KEY in _git("show", f"{dev.branch}:changes/POISON-1.diff", cwd=offline)


def test_the_whole_poisoned_pipeline_runs_offline(offline):
    """The demo claim: network off, the poisoned ticket still blocks with evidence."""
    from agentorg.graph import run_pipeline

    final = run_pipeline("DEMO-POISON", "Add a per-IP login rate limit.", poisoned=True)

    assert final.status == "blocked"
    assert final.security.verdict == "block"
    assert final.dev.branch in _git("branch", "--list", "agent-org/*", cwd=offline)

    notes = (offline / "NOTES.md").read_text()
    assert "DEMO-POISON" in notes
    assert final.run_id in notes
    # The note carries the scanners' own words, not a generic placeholder.
    assert final.security.explanation in notes


def _make_foreign_repo(path):
    """A git repo somebody else made: distinct default branch, one commit.

    Identity is set locally rather than inherited, for the same reason
    _ensure_offline_repo sets it: a machine enforcing user.useConfigOnly has no
    identity to inherit, and this test must not depend on the one this laptop
    happens to have.
    """
    path.mkdir(parents=True)
    _git("init", "-b", "trunk", cwd=path)
    _git("config", "user.email", "someone@example.com", cwd=path)
    _git("config", "user.name", "Someone Else", cwd=path)
    (path / "their_work.txt").write_text("do not touch\n")
    _git("add", "their_work.txt", cwd=path)
    _git("commit", "-m", "their commit", cwd=path)
    return path


def test_open_pr_refuses_a_repository_it_did_not_create(tmp_path, monkeypatch):
    """OFFLINE_REPO=. would branch and switch THIS checkout, mid-pipeline.

    _ensure_offline_repo skips `init` when a .git is already there, and open_pr
    then runs `git checkout -B agent-org/<ticket>` in whatever that repository
    is. The witness is not only that it raises: it is that the victim's HEAD and
    branch list are byte-identical afterwards, since a guard placed one line too
    late would raise having already moved them.
    """
    victim = _make_foreign_repo(tmp_path / "someone-elses-checkout")
    head_before = _git("rev-parse", "HEAD", cwd=victim)
    branches_before = _git("branch", "--list", cwd=victim)

    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.setattr(config, "OFFLINE_REPO", str(victim))

    with pytest.raises(RuntimeError) as excinfo:
        github_ops.open_pr(_state())

    assert str(victim) in str(excinfo.value)
    assert _git("rev-parse", "HEAD", cwd=victim) == head_before
    assert _git("branch", "--list", cwd=victim) == branches_before
    assert "agent-org" not in _git("branch", "--list", cwd=victim)
    assert (victim / "their_work.txt").read_text() == "do not touch\n"


def _make_foreign_worktree(tmp_path):
    """Somebody else's checkout where `.git` is a FILE, not a directory.

    `git init` writes `.git/` as a directory, but `git worktree add` -- and
    `git submodule add` -- writes `.git` as a one-line `gitdir:` text file
    pointing into the parent's admin directory. Everything else about it is a
    working checkout: a HEAD, a branch, an index, a local identity.
    """
    origin = _make_foreign_repo(tmp_path / "someone-elses-repo")
    linked = tmp_path / "someone-elses-worktree"
    _git("worktree", "add", "-b", "their-feature", str(linked), cwd=origin)
    return linked


def test_open_pr_refuses_a_worktree_it_did_not_create(tmp_path, monkeypatch):
    """The same refusal where `.git` is a file -- a linked worktree, a submodule.

    A separate test because it was a separate code path: asking
    `os.path.isdir(<path>/.git)` answers False for a checkout that very much is
    a repository, so the guard was skipped and `init` ran against a live one.
    That is not a hypothetical shape -- this project is itself a linked
    worktree, so `OFFLINE_REPO=.`, the example the docstring names, is exactly
    this case.

    MEASURED against that version, in a throwaway worktree: it rewrote the
    victim's local `user.email`, committed a `README.md` onto the branch they
    had checked out, then died with `NotADirectoryError` writing the marker
    inside a `.git` that is a file. Note what that run left alone -- the branch
    LIST, byte for byte, because the damage was a commit on the branch already
    checked out. So HEAD, the local identity and the working tree all have to
    be part of the witness here; the branch list alone would have called it
    clean.
    """
    victim = _make_foreign_worktree(tmp_path)
    # The precondition the whole test rests on. If a future git ever writes a
    # directory here, everything below still passes while testing nothing.
    assert not (victim / ".git").is_dir()
    assert (victim / ".git").is_file()

    head_before = _git("rev-parse", "HEAD", cwd=victim)
    branches_before = _git("branch", "--list", cwd=victim)
    email_before = _git("config", "--local", "--get", "user.email", cwd=victim)
    files_before = sorted(p.name for p in victim.iterdir())

    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.setattr(config, "OFFLINE_REPO", str(victim))

    with pytest.raises(RuntimeError) as excinfo:
        github_ops.open_pr(_state())

    assert str(victim) in str(excinfo.value)
    assert _git("rev-parse", "HEAD", cwd=victim) == head_before
    assert _git("branch", "--list", cwd=victim) == branches_before
    assert _git("config", "--local", "--get", "user.email", cwd=victim) == email_before
    assert sorted(p.name for p in victim.iterdir()) == files_before
    assert not (victim / "README.md").exists()
    assert (victim / "their_work.txt").read_text() == "do not touch\n"


def test_a_repo_offline_mode_created_is_reused_not_refused(offline):
    """The guard must not fire on our own workspace -- including a second run.

    Paired with the test above on purpose: a guard that refuses everything would
    pass that one. This is the half that says the marker is actually written.
    """
    first = github_ops.open_pr(_state())
    assert (offline / ".git" / "agent-org-offline").exists()

    second = github_ops.open_pr(_state())
    assert first.branch == second.branch


# =========================================================================
# The ONLINE branch of post_comment -- it lives in this file because it is the
# other half of the same function, and because what it does when it cannot
# deliver is: fall back, the way the offline path already does.
#
# graph.py sets status="blocked" and calls post_comment on the very next line,
# so a raise there turns a correctly-blocked run into a traceback. The claim is
# that post_comment RETURNS in every case. Every test below opts into the
# online path with all four lines conftest.py demands.
#
# The load-bearing one is test_post_comment_posts_on_the_open_pr_when_there_is_one.
# Without it, a post_comment that always fell back -- one that never contacted
# GitHub at all -- would pass every other test in this section, and "never
# raises" would have been bought by deleting the feature.
# =========================================================================

COMMENT_URL = "https://github.com/someone/auth-service/pull/41#issuecomment-9001"
GITHUB_BOOM = "GitHub returned 502 Bad Gateway"
BLOCK_REASON = "Blocked: hardcoded AWS key."
BRANCH = "agent-org/POISON-1-abc1234"


class _FakePulls:
    def __init__(self, total: int):
        self.totalCount = total

    def __getitem__(self, index):
        return SimpleNamespace(number=41)


class _FakeIssue:
    def __init__(self, repo):
        self.repo = repo

    def create_comment(self, body):
        self.repo.record("create_comment", body)
        return SimpleNamespace(html_url=COMMENT_URL)


class _FakeRepo:
    """Stand-in for the PyGithub repo handle, recording every call it takes.

    `failing` names the ONE call that raises, so each step of the online path
    can be broken on its own; `open_prs` is how many open PRs the branch has.
    Recording matters as much as returning here: several of the claims below
    are about a call that must NOT happen, and a fake that only returns values
    cannot witness that.
    """

    def __init__(self, *, open_prs: int = 1, failing: str = "", boom: str = GITHUB_BOOM):
        self.calls: list[tuple[str, object]] = []
        self.open_prs = open_prs
        self.failing = failing
        self.boom = boom
        self.owner = SimpleNamespace(login="someone")

    def record(self, name: str, arg) -> None:
        self.calls.append((name, arg))
        if self.failing == name:
            raise RuntimeError(self.boom)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def get_pulls(self, **kwargs):
        self.record("get_pulls", kwargs)
        return _FakePulls(self.open_prs)

    def get_issue(self, number):
        self.record("get_issue", number)
        return _FakeIssue(self)


def _online(monkeypatch, repo_factory) -> None:
    """Opt this test into the online path -- all four lines, per conftest.py."""
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")
    monkeypatch.setattr(github_ops, "_repo", repo_factory)


def test_post_comment_posts_on_the_open_pr_when_there_is_one(monkeypatch, capsys):
    """The control for everything below: the delivery path still delivers.

    Asserted on what FLOWS, not on structure. The returned ref has to be the
    comment's own html_url; the issue number has to come from the PR the head
    filter found; the body has to arrive at create_comment unaltered; and the
    head filter has to be built from the repo owner and this branch. A
    post_comment that skipped GitHub entirely and returned `comment://...`
    would satisfy every other test in this section and fail this one.
    """
    repo = _FakeRepo(open_prs=1)
    _online(monkeypatch, lambda: repo)

    state = _state()
    state.dev.branch = BRANCH
    ref = github_ops.post_comment(state, BLOCK_REASON)

    assert ref == COMMENT_URL
    assert repo.calls == [
        ("get_pulls", {"state": "open", "head": f"someone:{BRANCH}"}),
        ("get_issue", 41),
        ("create_comment", BLOCK_REASON),
    ]
    # Nothing fell back, so nothing was printed instead of posted.
    assert capsys.readouterr().out == ""


def test_post_comment_never_raises_without_a_pr(monkeypatch, capsys):
    """A missing PR must surface the reason, not crash the blocked run."""
    repo = _FakeRepo(open_prs=0)
    _online(monkeypatch, lambda: repo)

    state = _state()
    state.dev.branch = BRANCH
    ref = github_ops.post_comment(state, BLOCK_REASON)

    assert ref == f"comment://{state.run_id}"

    out = capsys.readouterr().out
    assert BLOCK_REASON in out
    assert BRANCH in out
    # It RECOGNISED there was no PR rather than tripping over one that is not
    # there. Note where that is actually caught: deleting the totalCount check
    # is caught by the REF assertion above, because _FakePulls has __getitem__,
    # so the mutant runs on through get_issue to create_comment and returns
    # COMMENT_URL instead of the fallback ref. The two witnesses below are the
    # independent ones -- GitHub was asked exactly once and never written to,
    # and this is not the wording the exception handler uses.
    assert repo.names() == ["get_pulls"]
    assert "could not comment on the PR" not in out


@pytest.mark.parametrize("break_state", [
    pytest.param(lambda s: setattr(s, "dev", None), id="dev-is-None"),
    pytest.param(lambda s: None, id="branch-is-empty"),
])
def test_post_comment_does_not_ask_github_without_a_branch(monkeypatch, capsys,
                                                           break_state):
    """No branch, no query -- `head="someone:"` does not select nothing.

    The dangerous version of this is not a crash, it is a success: an empty
    branch makes a head filter that GitHub can answer with an unrelated PR, and
    the next thing this function does is write the block reason on whatever
    came back. So the claim is stronger than "it returned a ref" -- it is that
    get_pulls is never called at all. `_state()` leaves branch "", which is why
    the second case needs no setup of its own.
    """
    repo = _FakeRepo(open_prs=1)
    _online(monkeypatch, lambda: repo)

    state = _state()
    break_state(state)
    ref = github_ops.post_comment(state, BLOCK_REASON)

    assert repo.calls == []
    assert ref == f"comment://{state.run_id}"
    assert BLOCK_REASON in capsys.readouterr().out


@pytest.mark.parametrize("failing", ["_repo", "get_pulls", "get_issue", "create_comment"])
def test_post_comment_survives_a_github_failure(monkeypatch, capsys, failing):
    """Rate limit, 502, dead token, locked conversation -- the block still stands.

    Each step of the online path is broken on its own, `_repo()` included, so
    this cannot pass by hardening only the first call. The witness is not just
    "did not raise": the real exception's own text has to reach stdout, because
    a canned "something went wrong" string would satisfy a handler that never
    looked at what it caught.
    """
    repo = _FakeRepo(open_prs=1, failing=failing)

    def _explode():
        raise RuntimeError(GITHUB_BOOM)

    _online(monkeypatch, _explode if failing == "_repo" else (lambda: repo))

    state = _state()
    state.dev.branch = BRANCH
    ref = github_ops.post_comment(state, BLOCK_REASON)

    assert ref == f"comment://{state.run_id}"

    out = capsys.readouterr().out
    assert BLOCK_REASON in out
    assert GITHUB_BOOM in out
    # And it is the failure ending, not the no-PR one -- a handler that reported
    # every problem as "no PR" would hide a token that expired mid-demo.
    assert "could not comment on the PR" in out
    assert "no PR for" not in out


def test_the_blind_except_does_not_swallow_the_conftest_github_guard(monkeypatch):
    """`except Exception` must not eat the seam raiser that keeps us off GitHub.

    post_comment now calls `_repo()` INSIDE its try, so the only thing still
    holding conftest's `_unpatched_repo` up is that `pytest.fail` raises
    `Failed`, which derives from BaseException rather than Exception. Downgrade
    that raiser to an ordinary Exception and this handler eats it: every test
    that opts into the online path without replacing `_repo` would go green
    while performing live writes against DEMO_REPO, which is the exact bug
    SEAM 2 in conftest.py exists to prevent. That property is now pinned by a
    test rather than by a comment.
    """
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")
    # github_ops._repo is deliberately left as conftest's raiser.

    state = _state()
    state.dev.branch = BRANCH

    with pytest.raises(pytest.fail.Exception, match="github_ops._repo"):
        github_ops.post_comment(state, BLOCK_REASON)


def test_a_chatty_github_failure_stays_one_short_warning_line(monkeypatch, caplog,
                                                              capsys):
    """The delivery-failure WARNING must stay one bounded projector line.

    A real GithubException carries the whole JSON response body in its message,
    and this fires immediately above `status=blocked` during the demo -- where
    a wall of text reads as a crash rather than as the block working. Same
    shape, and the same reason, as security.run's scanner fallback; the
    analogue is test_a_chatty_scanner_failure_stays_one_short_warning_line in
    tests/test_agent_fallbacks.py.

    This test exists because lint cannot do its job: BLE001 forces a logging
    call carrying exc_info to EXIST, but it has nothing to say about that
    call's LEVEL, wording or length. A handler that dumped 60KB at WARNING is
    BLE001-clean.
    """
    body = '{"message": "Validation Failed", "documentation_url": "..."}, '
    noise = body * 1000                                       # ~60KB, one line
    repo = _FakeRepo(failing="get_pulls",
                     boom=f"502 Bad Gateway from api.github.com: {noise}")
    _online(monkeypatch, lambda: repo)

    state = _state()
    state.dev.branch = BRANCH
    with caplog.at_level(logging.DEBUG, logger="agentorg.github_ops"):
        ref = github_ops.post_comment(state, BLOCK_REASON)

    assert ref == f"comment://{state.run_id}", "the block must still be reported back"

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "one line on the projector, not none and not three"
    line = warnings[0].getMessage()
    assert "\n" not in line, "a multi-line WARNING is not one projector line"
    assert len(line) < 400, f"WARNING was {len(line)} chars: {line[:120]}..."
    assert "RuntimeError" in line, "the line must still name the cause"
    assert "chars total" in line, "truncation must be marked, not silent"
    # The crux: a WARNING carrying exc_info renders the whole traceback through
    # whatever handler is attached, which is the wall of text this exists to
    # prevent. getMessage() would not show that, so assert on the record.
    assert warnings[0].exc_info is None, "the traceback must not ride the projector line"

    # Demote, don't drop: the DEBUG record still carries the whole thing.
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debugs) == 1
    assert debugs[0].exc_info is not None, "demoted, not discarded"
    rendered = logging.Formatter().format(debugs[0])
    assert noise.strip() in rendered, "the full response body must survive at DEBUG"

    # And stdout is the projector too -- it is where the block reason goes when
    # the PR is unreachable, so it is bounded on the same terms.
    out = capsys.readouterr().out
    assert BLOCK_REASON in out, "the reason itself is never what gets truncated"
    assert len(out) < 1000, f"stdout was {len(out)} chars"
    assert "chars total" in out


# =========================================================================
# The block reason's fate has to reach the RUN'S OWN ARTIFACT, not just
# Python's stderr. These live here rather than with the pipeline tests because
# they need the fake GitHub above; what they exercise is graph.py + log.py.
# =========================================================================

def _poisoned_run_with_only_the_comment_online(monkeypatch, repo_factory):
    """Run the poisoned pipeline with the block comment as its ONLY online call.

    open_pr is pinned to the local git path on purpose. If the PR failed too,
    a run could end blocked-and-unreported for either reason, and the log row
    under test would no longer be about the comment.
    """
    from agentorg.graph import run_pipeline

    real_open_pr = github_ops.open_pr

    def local_open_pr(state):
        config.OFFLINE = True
        try:
            return real_open_pr(state)
        finally:
            config.OFFLINE = False

    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")
    monkeypatch.setattr(github_ops, "_repo", repo_factory)
    monkeypatch.setattr(github_ops, "open_pr", local_open_pr)
    return run_pipeline("DEMO-POISON", "Add a per-IP login rate limit.", poisoned=True)


def _block_row(state):
    """The single system/security/blocked row out of runs/<run_id>.jsonl."""
    rows = [e for e in log.read(state.run_id)
            if (e.actor, e.stage, e.action) == ("system", "security", "blocked")]
    assert len(rows) == 1, f"expected one block row, got {len(rows)}"
    return rows[0]


def test_the_decision_log_distinguishes_a_delivered_comment_from_a_lost_one(
        monkeypatch):
    """The run's own artifact must tell a posted block reason from a lost one.

    log.py calls runs/<run_id>.jsonl the source of truth the timeline UI renders
    and the judges score. post_comment cannot raise any more, which is the whole
    point -- but that means a failed delivery is INVISIBLE unless it is written
    down, and before this the file was byte-identical whether the reason landed
    on the PR or evaporated into a 502. The only trace was a stderr log line,
    which is not the audit trail.

    Read back through log.read rather than off the returned RunState: the claim
    is about the artifact on disk, and the in-memory object is not it.

    Both halves are run, because either alone proves nothing. A graph that
    logged the ref only on success passes a delivered-only test; a graph that
    logged any constant passes either one on its own. The claim is that the two
    rows carry the two DIFFERENT refs and are not equal.
    """
    delivered = _poisoned_run_with_only_the_comment_online(
        monkeypatch, lambda: _FakeRepo(open_prs=1))

    def _explode():
        raise RuntimeError(GITHUB_BOOM)

    lost = _poisoned_run_with_only_the_comment_online(monkeypatch, _explode)

    assert delivered.status == "blocked", "both runs must still be blocked runs"
    assert lost.status == "blocked", "a lost comment must not change the verdict"

    delivered_row = _block_row(delivered)
    lost_row = _block_row(lost)

    assert COMMENT_URL in delivered_row.summary, "the posted comment's URL"
    assert f"comment://{lost.run_id}" in lost_row.summary, "the fallback ref"
    assert delivered_row.summary != lost_row.summary
