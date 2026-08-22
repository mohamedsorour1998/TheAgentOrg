"""Every agent reads the target repository, and reads it fresh.

MEASURED on the deployed pipeline before `repo_snapshot` existed. Each failure below
is an agent reasoning about bytes it was never shown:

  * the developer proposed `sync.RWMutex` and `NewRateLimiter` -- GO -- for a Python
    Flask application, four revisions running, until the cap expired with the
    scanners reporting PASS
  * the planner named `app/controllers/password_resets_controller.rb` and
    `config/initializers/rate_limit_config.rb`, a RAILS layout, for a repository that
    contains neither
  * the reviewer listed "Missing import for the authenticate function" as BLOCKING,
    for a file defining `authenticate` twenty lines above the hunk
"""

import time

import pytest

from agentorg import repo_snapshot
from agentorg.agents import developer, planner, reviewer, security, sre
from agentorg.common import config
from agentorg.state import DevResult, Finding, PlanResult, RunState

_FILES = {
    "app/auth.py": (
        "from flask import request, jsonify\n\n"
        "def authenticate(username, password):\n"
        "    return bool(username and password)\n"
    ),
    "tests/test_auth.py": "def test_authenticate():\n    assert True\n",
    "README.md": "# auth-service\n",
}


@pytest.fixture(autouse=True)
def _no_real_clone(request, monkeypatch):
    """Never clone in a test.

    Stubbed at `snapshot` rather than at the subprocess, because what the agents do
    with the result is where every measured defect was.

    A test marked `real_snapshot` opts OUT, because it is exercising `snapshot`
    itself -- the cache, the failure paths -- and a stub would make it vacuous. That
    marker is the difference between testing the caching and testing the stub.
    """
    repo_snapshot.reset_cache()
    if request.node.get_closest_marker("real_snapshot") is None:
        monkeypatch.setattr(repo_snapshot, "snapshot", lambda: dict(_FILES))
    yield
    repo_snapshot.reset_cache()


def _state(**kw) -> RunState:
    state = RunState(ticket_id="7", ticket_text="Add a per-IP login rate limit.")
    state.plan = PlanResult(
        tasks=["Add a Redis counter keyed on IP"],
        acceptance_criteria=["429 past five attempts"],
        target_files=["app/auth.py", "tests/test_auth.py"],
    )
    for key, value in kw.items():
        setattr(state, key, value)
    return state


# ── every agent, not a subset ─────────────────────────────────────────────────


def test_the_planner_is_shown_the_repository():
    """It CHOOSES target_files, so it needs to know which files exist.

    The agent with the most to gain: a plan naming files that do not exist sends the
    developer to patch a project that is not there.
    """
    monkey_prompt: list[str] = []
    original = planner.llm.structured
    try:
        planner.llm.structured = (
            lambda cls, sys, user: monkey_prompt.append(user) or None
        )
        planner.run(_state())
    finally:
        planner.llm.structured = original

    assert monkey_prompt, "the planner made no model call"
    assert "app/auth.py" in monkey_prompt[0], (
        f"the planner's prompt does not name the repository's files, so it must "
        f"invent them -- measured naming a Rails layout for this Flask app. Prompt "
        f"was:\n{monkey_prompt[0][:400]}"
    )
    assert "def authenticate" in monkey_prompt[0], (
        "the planner sees file NAMES but not contents; it cannot tell what the "
        "project is"
    )


def test_the_developer_is_shown_the_file_it_must_patch():
    prompt = developer._prompt(_state())
    assert "def authenticate" in prompt, (
        f"the developer's prompt lacks the target file's contents, so its diff's "
        f"context lines are guesses and `git apply` would refuse them. Prompt:\n"
        f"{prompt[:400]}"
    )


def test_the_reviewer_is_shown_the_file_AFTER_the_diff():
    """Not the same view the developer got, and deliberately so.

    The developer wanted the file as it stands. The reviewer wants it as the change
    would leave it -- otherwise it applies the patch in its head, which is the work
    that produced a blocking objection about an import that already existed.
    """
    diff = (
        "--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1,2 +1,3 @@\n"
        " from flask import request, jsonify\n+import redis\n"
    )
    state = _state(dev=DevResult(branch="b", diff=diff, summary="s",
                                files_changed=["app/auth.py"]))
    prompt = reviewer._prompt(state)

    assert "import redis" in prompt, (
        "the reviewer's view does not include the diff's added lines, so it cannot "
        "see what the change would leave behind"
    )
    assert "WITH THE DIFF UNDER REVIEW APPLIED" in prompt, (
        f"the reviewer is shown the repository BEFORE the change. It would have to "
        f"apply the patch mentally. Prompt:\n{prompt[:400]}"
    )


def test_the_sre_is_shown_what_it_is_deploying_into():
    diff = "--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n+import redis\n"
    state = _state(dev=DevResult(branch="b", diff=diff, summary="s",
                                files_changed=["app/auth.py"]))
    prompt = sre._prompt(state)
    assert "def authenticate" in prompt, (
        "the SRE judges operational risk without seeing the application, so its "
        "answer is about a generic web service rather than this one"
    )


def test_the_security_explanation_is_written_with_the_file_in_view():
    """The verdict is already decided; this is the prose that reaches the PR.

    Asserted through `_explain` because that is the only place the security agent
    calls a model -- the verdict comes from `compute_security_verdict`, and no
    snapshot can reach it.
    """
    seen: list[str] = []
    original = security.llm.text
    try:
        security.llm.text = lambda sys, user: seen.append(user) or None
        security._explain("block", [Finding(
            tool="gitleaks", severity="critical", rule="aws-access-key-id",
            file="app/auth.py", line=3, description="AWS access key ID",
        )])
    finally:
        security.llm.text = original

    assert seen, "the security agent made no model call"
    assert "def authenticate" in seen[0], (
        "the explanation is written without the file in view, so it can only "
        "paraphrase the finding back"
    )


def test_all_five_agents_read_the_same_snapshot_function():
    """One source, so two agents cannot reason about different bytes.

    The first version of this fetched per-agent subsets through different calls, and
    a reviewer judging different information than the developer wrote from is a
    reviewer whose objections are unactionable.
    """
    import inspect
    for module in (planner, developer, reviewer, security, sre):
        source = inspect.getsource(module)
        assert "repo_snapshot.render" in source, (
            f"{module.__name__} does not read the shared snapshot"
        )


# ── it must be fresh, because promote merges ──────────────────────────────────


@pytest.mark.real_snapshot
def test_the_snapshot_is_re_read_once_the_ttl_expires(monkeypatch):
    """`promote` merges the PR, so a later run must see the merged file.

    A process-lifetime cache would answer run 2's planner from a clone taken before
    run 1 merged -- and the demo runs two tickets against one repository.
    """
    repo_snapshot.reset_cache()
    calls: list[int] = []

    def _clone_once(*args, **kwargs):
        calls.append(1)

        class _R:
            returncode = 1
            stderr = "stubbed"
        return _R()

    monkeypatch.setattr(config, "GITHUB_REPO", "owner/name")
    monkeypatch.setattr(repo_snapshot.subprocess, "run", _clone_once)

    repo_snapshot.snapshot()
    repo_snapshot.snapshot()
    assert len(calls) == 1, f"cached call re-cloned immediately: {len(calls)} clones"

    # Push the clock past the TTL rather than sleeping for it. `later` is captured
    # BEFORE the patch: a lambda calling time.monotonic() would call the patched
    # function and recurse forever.
    later = time.monotonic() + repo_snapshot.CACHE_TTL_SECONDS + 1
    monkeypatch.setattr(repo_snapshot.time, "monotonic", lambda: later)
    repo_snapshot.snapshot()
    assert len(calls) == 2, (
        f"the snapshot was not re-read after {repo_snapshot.CACHE_TTL_SECONDS}s. A "
        f"run following a merge would reason about the pre-merge file."
    )


def test_the_ttl_is_long_enough_for_one_run_and_short_enough_between_runs():
    """A stated bound, so a future edit cannot quietly make it either."""
    assert 30 <= repo_snapshot.CACHE_TTL_SECONDS <= 600, (
        f"CACHE_TTL_SECONDS={repo_snapshot.CACHE_TTL_SECONDS}. Below ~30s the five "
        f"agents of ONE run could disagree with each other; above ~600s a second run "
        f"could miss the first one's merge."
    )


# ── degrading, rather than failing ────────────────────────────────────────────


@pytest.mark.real_snapshot
def test_no_target_repo_means_no_clone_attempt(monkeypatch):
    repo_snapshot.reset_cache()
    monkeypatch.setattr(config, "GITHUB_REPO", "")

    def _boom(*args, **kwargs):
        raise AssertionError("cloned with no repository configured")

    monkeypatch.setattr(repo_snapshot.subprocess, "run", _boom)
    assert repo_snapshot.snapshot() == {}


@pytest.mark.real_snapshot
def test_a_failed_clone_is_empty_rather_than_an_exception(monkeypatch):
    """A private repo, or an outage. Every agent carries on without the context.

    NEVER raises, for the reason `post_comment` never raises: this runs on the plan
    and develop stages' critical path, and a briefly unreachable repository must not
    fail a run that would otherwise complete.
    """
    repo_snapshot.reset_cache()
    monkeypatch.setattr(config, "GITHUB_REPO", "owner/private")

    class _Failed:
        returncode = 128
        stderr = "fatal: could not read Username"

    monkeypatch.setattr(repo_snapshot.subprocess, "run",
                        lambda *a, **k: _Failed())
    assert repo_snapshot.snapshot() == {}


def test_an_empty_snapshot_renders_nothing_rather_than_an_empty_heading(monkeypatch):
    """"REPOSITORY CONTENTS" with nothing under it reads as "the repo is empty".

    That is a different and worse claim than saying nothing, and it is the claim a
    model would act on.
    """
    monkeypatch.setattr(repo_snapshot, "snapshot", dict)
    assert repo_snapshot.render(["app/auth.py"]) == ""


def test_the_prompts_still_build_when_the_snapshot_is_empty(monkeypatch):
    """The whole suite runs this way, and so does every offline run."""
    monkeypatch.setattr(repo_snapshot, "snapshot", dict)
    state = _state(dev=DevResult(branch="b", diff="", summary="s",
                                files_changed=["app/auth.py"]))
    assert "TICKET:" in developer._prompt(state)
    assert "DIFF UNDER REVIEW:" in reviewer._prompt(state)
    assert "TICKET:" in sre._prompt(state)


# ── the clone is anonymous, and stays that way ────────────────────────────────


def test_the_clone_url_carries_no_credential(monkeypatch):
    """No token reaches the five runtimes, by design.

    They hold AGENT_ROLE and DEMO_REPO. Shipping a GitHub token into five containers
    so they could read a PUBLIC repository would put a real credential in five more
    places for no capability -- and a token embedded in a clone URL lands in process
    listings and error messages.
    """
    monkeypatch.setattr(config, "GITHUB_REPO", "owner/name")
    url = repo_snapshot._clone_url()
    assert url == "https://github.com/owner/name.git", url
    assert "@" not in url, f"the clone URL embeds a credential: {url}"
    assert "token" not in url.lower()


def test_the_snapshot_never_writes_to_the_target():
    """Read-only. An agent that could push through this seam is a different feature."""
    import inspect
    source = inspect.getsource(repo_snapshot)
    for forbidden in ("git\", \"push", "git\", \"commit", "create_git_ref"):
        assert forbidden not in source, f"repo_snapshot can write: {forbidden}"


# ── the caps, which exist to protect the rest of the prompt ────────────────────


def test_a_large_file_is_truncated_with_a_marker_not_dropped(monkeypatch, tmp_path):
    """Truncated, because the first 20 KB establishes language and style.

    Dropping it silently would put the agent back to guessing, which is the failure
    this module exists to remove.
    """
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 20_000)
    out = repo_snapshot._read_tree(tmp_path)
    assert "big.py" in out, "a large file was dropped entirely"
    assert "truncated" in out["big.py"]
    assert len(out["big.py"]) < repo_snapshot.MAX_FILE_BYTES + 200


def test_binaries_and_vendor_directories_are_skipped(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "auth.py").write_text("x = 1\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x\n")

    out = repo_snapshot._read_tree(tmp_path)
    assert "app/auth.py" in out
    assert "logo.png" not in out, "a binary reached the prompt"
    assert not any(p.startswith("node_modules") for p in out)
    assert not any(p.startswith(".git") for p in out)


def test_the_rendering_is_deterministic(monkeypatch):
    """Two runs at one commit produce identical prompts, so an answer is debuggable."""
    assert repo_snapshot.render(["app/auth.py"]) == repo_snapshot.render(["app/auth.py"])
