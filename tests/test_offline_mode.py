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

import subprocess

import pytest

from agentorg import github_ops
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


def test_a_repo_offline_mode_created_is_reused_not_refused(offline):
    """The guard must not fire on our own workspace -- including a second run.

    Paired with the test above on purpose: a guard that refuses everything would
    pass that one. This is the half that says the marker is actually written.
    """
    first = github_ops.open_pr(_state())
    assert (offline / ".git" / "agent-org-offline").exists()

    second = github_ops.open_pr(_state())
    assert first.branch == second.branch
