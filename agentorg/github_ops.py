"""GitHub operations — branch, open PR, post comments.

OWNER: Mariam.  INTEGRATION SEAM with Sorour's graph.

The graph calls these three functions and nothing else. Their signatures are
frozen (they take/return the shapes in state.py), so the bodies could go from
stub to real without graph.py changing a line — which is exactly what happened.

Two modes:
    online  — real GitHub API via PyGithub; opens real PRs and posts real comments.
              Needs GITHUB_TOKEN + DEMO_REPO (see agentorg/common/config.py).
    local   — no network. Used when OFFLINE=true, and automatically whenever
              those credentials are absent, so every other lane (and CI) can run
              the pipeline without a token. Not a stub: it does the same work
              against a plain git repo at config.OFFLINE_REPO — real branch,
              real commit of the diff — and records what would have been a PR
              comment in config.OFFLINE_NOTES. Returns local:// refs.

The local path is the demo's insurance policy: `OFFLINE=true LLM_DISABLED=true`
walks the whole pipeline, poisoned ticket included, with the network unplugged.

Still to build:
    - deploy_note(), co-owned with Sorour                   (week3.md)
  see docs/plan/mariam/.
"""

import hashlib
import os
import subprocess

from github import Auth, Github, GithubException

from . import fixtures_loader
from .common import config
from .state import DevResult, Finding, RunState


def _short_sha(text: str) -> str:
    """First 7 hex chars of sha1(text) — stable branch suffix per diff."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:7]


def _use_local() -> bool:
    """True when we must not touch the GitHub API.

    Either OFFLINE was requested, or no credentials are configured. The second
    case matters: PyGithub raises on an empty token, so without this guard every
    other lane's `python -m agentorg.graph` — and CI, which has no secrets —
    dies inside the PR node. Nobody needs a token to run the pipeline.
    """
    return config.OFFLINE or not (config.GITHUB_TOKEN and config.GITHUB_REPO)


def _repo():
    """Authenticated handle on the target demo repo."""
    return Github(auth=Auth.Token(config.GITHUB_TOKEN)).get_repo(config.GITHUB_REPO)


def _git(*args: str, cwd: str) -> None:
    """Run a git command in cwd, raising on failure."""
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _ensure_offline_repo() -> str:
    """Create the offline demo repo with a main branch if it doesn't exist.

    user.email/user.name are set LOCALLY here rather than inherited from the
    machine, because a CI container has no global identity. Git only *usually*
    covers for that: with none configured it guesses from username+hostname
    (measured on a laptop: `Mohamed Sorour <sorour@192.168.1.9>`, commit exit 0,
    with a warning). It refuses when it cannot build a plausible address, and
    refuses outright under user.useConfigOnly. Deleting these two lines and
    running the suite with that set fails 25 tests; with them, 74 pass.

    -b main is likewise explicit rather than trusting init.defaultBranch, since
    `open_pr` checks out "main" by name on every subsequent run.
    """
    path = config.OFFLINE_REPO
    os.makedirs(path, exist_ok=True)
    if not os.path.isdir(os.path.join(path, ".git")):
        _git("init", "-b", "main", cwd=path)
        _git("config", "user.email", "agentorg@example.com", cwd=path)
        _git("config", "user.name", "Agent Org", cwd=path)
        with open(os.path.join(path, "README.md"), "w") as fh:
            fh.write("# offline demo\n")
        _git("add", "README.md", cwd=path)
        _git("commit", "-m", "init offline demo repo", cwd=path)
    return path


def open_pr(state: RunState) -> DevResult:
    """Create a branch + PR for the developer's diff. Returns DevResult with pr_url set."""
    dev = state.dev or fixtures_loader.dev()
    branch = f"agent-org/{state.ticket_id}-{_short_sha(dev.diff)}"
    dev.branch = branch

    if _use_local():
        # No network (or no credentials): do the same work against a local repo.
        path = _ensure_offline_repo()
        _git("checkout", "main", cwd=path)
        # -B resets the branch if a prior run created it, so re-runs are safe.
        _git("checkout", "-B", branch, cwd=path)
        os.makedirs(os.path.join(path, "changes"), exist_ok=True)
        diff_file = os.path.join("changes", f"{state.ticket_id}.diff")
        with open(os.path.join(path, diff_file), "w") as fh:
            fh.write(dev.diff)
        _git("add", diff_file, cwd=path)
        _git("commit", "-m", f"{state.ticket_id}: {dev.summary}", cwd=path)
        dev.pr_url = f"local://{branch}"
        return dev

    repo = _repo()

    # 1. Branch off main at its current tip.
    base = repo.get_branch("main")
    ref = f"refs/heads/{branch}"
    try:
        repo.create_git_ref(ref=ref, sha=base.commit.sha)
    except GithubException as e:
        if e.status != 422:
            raise

    # 2. Commit the diff. Write the raw unified diff so the PR carries the
    # change the scanners will read. One deterministic path per run.
    path = f"changes/{state.ticket_id}.diff"
    message = f"{state.ticket_id}: {dev.summary}"

    try:
        existing = repo.get_contents(path, ref=branch)
        repo.update_file(
            path,
            message,
            dev.diff,
            existing.sha,
            branch=branch,
        )
    except GithubException as e:
        if e.status == 404:
            repo.create_file(
                path,
                message,
                dev.diff,
                branch=branch,
            )
        else:
            raise

    # 3. Open the PR (reuse the open one if this branch already has it).
    try:
        pr = repo.create_pull(
            title=message,
            body=dev.summary,
            head=branch,
            base="main",
        )
    except GithubException as e:
        if e.status == 422:
            pr = repo.get_pulls(
                state="open",
                head=f"{repo.owner.login}:{branch}",
            )[0]
        else:
            raise

    dev.pr_url = pr.html_url
    return dev


def post_comment(state: RunState, body: str, finding: Finding | None = None) -> str:
    """Post a comment on the PR (reviewer + security lanes). Returns comment ref (URL)."""
    if finding is not None:
        header = (
            f"**[{finding.tool} · {finding.severity}] {finding.rule}** "
            f"({finding.file}:{finding.line})\n\n"
        )
        body = header + body

    if _use_local():
        # No network (or no credentials): append the reason to a local NOTES file.
        # `or "."` because dirname("NOTES.md") is "", and makedirs("") raises.
        os.makedirs(os.path.dirname(config.OFFLINE_NOTES) or ".", exist_ok=True)
        with open(config.OFFLINE_NOTES, "a") as fh:
            fh.write(f"\n## {state.ticket_id} ({state.run_id})\n{body}\n")
        return f"local://{config.OFFLINE_NOTES}"

    repo = _repo()

    branch = state.dev.branch if state.dev else ""

    pulls = repo.get_pulls(
        state="open",
        head=f"{repo.owner.login}:{branch}",
    )

    if pulls.totalCount == 0:
        raise RuntimeError(
            f"no open PR for branch {branch!r} to comment on"
        )

    issue = repo.get_issue(pulls[0].number)
    comment = issue.create_comment(body)

    return comment.html_url


def deploy_note() -> str:
    """Placeholder for the AgentCore deploy step you co-own with Sorour.

    See infra/agentcore/ for the Terraform. This function is where the deploy
    pipeline (build image -> push ECR -> update AgentCore runtime) gets wired in.
    """
    return "deploy not wired yet — pair with Sorour on infra/agentcore/"
