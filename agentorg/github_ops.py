"""GitHub operations — branch, open PR, post comments.

OWNER: Mariam.  INTEGRATION SEAM with Sorour's graph.

The graph calls these three functions and nothing else. Their signatures are
frozen (they take/return the shapes in state.py), so Sorour's graph.py can call
them from day 1 while they are still stubs. You fill in the real bodies on your
own branch; the graph keeps working the whole time.

Two modes, chosen by config.OFFLINE:
    online  (default)     — real GitHub API via PyGithub / gh, opens real PRs
    offline (OFFLINE=true) — plain local git, no network, for the live demo

WHAT TO BUILD (task by task — see docs/plan/mariam.md):
    1. open_pr:      create a branch, commit the diff, open a PR, return its URL
    2. post_comment: add a review/security comment to the PR
    3. offline mode: the same three operations against a local git repo

Until you replace them, these stubs return the values from fixtures/ so every
other lane runs green.
"""

import hashlib

from github import Github, GithubException

from .state import RunState, DevResult, Finding
from .common import config
from . import fixtures_loader


def _short_sha(text: str) -> str:
    """First 7 hex chars of sha1(text) — stable branch suffix per diff."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:7]


def open_pr(state: RunState) -> DevResult:
    """Create a branch + PR for the developer's diff. Returns DevResult with pr_url set."""
    dev = state.dev or fixtures_loader.dev()
    branch = f"agent-org/{state.ticket_id}-{_short_sha(dev.diff)}"
    dev.branch = branch

    if config.OFFLINE:
        # Offline path implemented in week 2. Placeholder keeps the graph green.
        dev.pr_url = f"local://{branch}"
        return dev

    gh = Github(config.GITHUB_TOKEN)
    repo = gh.get_repo(config.GITHUB_REPO)

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

    if config.OFFLINE:
        # Offline path implemented in week 2.
        return f"comment://{state.run_id}"

    gh = Github(config.GITHUB_TOKEN)
    repo = gh.get_repo(config.GITHUB_REPO)

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