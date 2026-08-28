"""The shipped adapter. DELEGATES to github_ops; it does not reimplement it.

OWNER: Lane D.

EVERY METHOD IS ONE CALL, and that is the whole design. `github_ops.py` carries
1,132 lines of measured behaviour and roughly 600 lines of comments explaining
why each line is the way it is -- the `os.path.exists`-not-`isdir` guard that
stopped offline mode committing into a victim's worktree, `_ISSUE_REF`'s anchors
and `[0-9]`-not-`\\d`, `local://` returned only AFTER the bytes reach disk,
`_destination` derived from the state rather than taken as a parameter.

Moving that body in here would mean re-earning all of it, and D2 says "no
behaviour change". A delegating adapter makes that claim checkable rather than
merely stated: `tests/test_integration_adapters.py` asserts each method's body
resolves to the matching `github_ops` function OVER THE AST, so a future edit
that reimplements one here fails by name.

WHAT THE INDIRECTION BUYS, given it adds a frame and no behaviour:

  * `graph.py` can import `CodeHost` and stop naming GitHub. That is the spec's
    §5 ask -- GitHub as one adapter behind one interface rather than the
    substrate -- and it is now one import line away.
  * the four never-raises contracts are enforced by `base.CodeHost` for every
    adapter, not re-implemented per adapter. `github_ops`'s own handlers stay
    exactly where they are: they produce BETTER refs than the interface's
    fallback (naming the branch, the issue number, the failure kind), so the
    interface wrapper is a second net that never fires on this adapter and
    always fires on a careless new one.
  * `tests/test_integration_conformance.py` drives THIS adapter through the same
    tests as the double and the sketch, so the double is provably not a fiction.

THE CONFTEST GUARDS STILL REACH THROUGH THIS. Guard 2 replaces
`github_ops._repo`, and `github_ops.post_comment` looks that up through its own
module at call time -- so a comment posted through `GitHubHost` hits the raiser,
exactly as a direct call does. Verified by
`test_the_github_adapter_is_still_covered_by_the_conftest_github_guard`, because
"the guard still works" is precisely the claim a refactor of this seam is most
likely to break and least likely to notice.
"""

from __future__ import annotations

from .. import github_ops
from ..state import DevResult, Finding, RunState
from .base import CodeHost


class GitHubHost(CodeHost):
    """GitHub via PyGithub, with github_ops' local-git path underneath it.

    ONE adapter for BOTH of `github_ops`' modes, not two. `_use_local()` --
    `config.OFFLINE or not (GITHUB_TOKEN and GITHUB_REPO)` -- picks the API or
    real local git per call, and splitting that into two adapters would move a
    decision made per call to one made per process. The demo's fallback is
    `OFFLINE=true` on the SAME adapter, and it must stay that way: unsetting one
    variable is the recovery move, not selecting a different implementation.
    """

    name = "github"
    shipped = True

    def open_pr(self, state: RunState) -> DevResult:
        """Branch, commit the diff, open the PR. MAY RAISE -- see base.CodeHost."""
        return github_ops.open_pr(state)

    def _post_comment(self, state: RunState, body: str,
                      finding: Finding | None = None) -> str:
        return github_ops.post_comment(state, body, finding)

    def _merge_pr(self, state: RunState) -> str:
        return github_ops.merge_pr(state)

    def _report_outcome(self, state: RunState) -> str:
        return github_ops.report_outcome(state)

    def _ci_status(self, state: RunState) -> str:
        return github_ops.ci_status(state)
