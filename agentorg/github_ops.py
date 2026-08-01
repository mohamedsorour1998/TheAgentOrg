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

from .state import RunState, DevResult, Finding
from .common import config
from . import fixtures_loader


def open_pr(state: RunState) -> DevResult:
    """Create a branch + PR for the developer's diff. Returns DevResult with pr_url set.

    STUB: returns the fixture DevResult (already has a fake pr_url).
    REAL: use PyGithub (online) or `git` subprocess (offline) to open the PR,
          then set dev.pr_url on the returned result.
    """
    dev = state.dev or fixtures_loader.dev()
    if config.OFFLINE:
        # TODO(Mariam): git checkout -b <branch>; apply diff; commit. No network.
        dev.pr_url = f"local://{dev.branch}"
    else:
        # TODO(Mariam): PyGithub create_pull(...) and set the real URL.
        dev.pr_url = dev.pr_url or "https://github.com/quorum/demo-app/pull/PENDING"
    return dev


def post_comment(state: RunState, body: str, finding: Finding | None = None) -> str:
    """Post a comment on the PR (used by reviewer + security lanes). Returns comment ref.

    STUB: returns a fake ref.
    REAL: PyGithub issue/review comment (online) or append to a local NOTES file (offline).
    """
    # TODO(Mariam): real comment. finding is optional structured context.
    return f"comment://{state.run_id}"


def deploy_note() -> str:
    """Placeholder for the AgentCore deploy step you co-own with Sorour.

    See infra/agentcore/ for the Terraform. This function is where the deploy
    pipeline (build image -> push ECR -> update AgentCore runtime) gets wired in.
    """
    return "deploy not wired yet — pair with Sorour on infra/agentcore/"
