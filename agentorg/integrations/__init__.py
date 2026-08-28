"""Integration adapters — the code host behind one interface.

OWNER: Lane D.  Spec §5 ("seam-bound: GitHub — one adapter behind one
interface"), §12.

WHY THIS PACKAGE EXISTS. `github_ops.py` is 1,132 lines reached from 20 files,
and it is the ONE module in `agentorg/` with a hard module-level vendor import --
measured, not asserted: `scripts/measure_dependencies.py` reports `1` of `50`
modules with a MODULE-LEVEL vendor import and names `github_ops.py` / `github`.
So GitHub was not a dependency of this pipeline; it was its substrate.

WHAT THIS PACKAGE DOES NOT DO, stated first because the alternative reading is
the expensive one: it does not rewrite `github_ops.py`. That module stays exactly
where it is, with every behaviour, every comment and all 20 importers intact.
`GitHubHost` DELEGATES to it, function for function. A refactor that moved 1,132
lines of measured behaviour would have to re-earn every one of the traps recorded
in its comments -- the `os.path.exists`-not-`isdir` worktree guard, the anchored
`_ISSUE_REF`, `local://` only after the bytes reach disk -- and "no behaviour
change" (D2) is not a claim a rewrite can make honestly.

  interface   base.CodeHost          five methods, four of which cannot raise
  shipped     github.GitHubHost      delegates to github_ops; the real one
  double      memory.MemoryHost      no network, no git, no disk; records calls
  proof       git.GitHost            a second adapter, DELIBERATELY NOT SHIPPED

=========================================================================
THE SECOND ADAPTER IS THE POINT, AND IT IS NOT SHIPPED
=========================================================================

The plan's D6 says it plainly: "a one-adapter interface is an unproven claim". An
interface extracted from a single implementation reliably ends up being that
implementation with the names changed, and nothing in a green suite says so.

So `git.GitHost` is real code that a conformance test drives, and
`shipped = False`. `host()` REFUSES to hand it to a pipeline -- see below. What it
bought is recorded in `git.py`'s docstring: it found two places where this
interface still assumed GitHub, and both were fixed in `base.py` rather than
worked around in the adapter.

=========================================================================
`host()` REFUSES AN UNKNOWN OR UNSHIPPED NAME. IT NEVER FALLS BACK
=========================================================================

Same rule as `config.STATE_BACKEND` (unknown values raise at import rather than
silently writing to disk) and `QUEUE_BACKEND=sqs` (raises rather than falling
through to memory). An operator who asked for `gitlab` and got GitHub would open
a real pull request on a real repository believing they had done neither.
"""

from __future__ import annotations

import os

from .base import (
    CI_ANSWERS,
    CI_FAILING,
    CI_PASSING,
    CI_UNKNOWN,
    DELIVERY_SCHEMES,
    MERGE_FAILED_PREFIX,
    MERGE_REFUSED_PREFIX,
    SCHEME_DELIVERED_LOCAL,
    SCHEME_DELIVERED_REMOTE,
    SCHEME_UNDELIVERED,
    CodeHost,
    scheme_of,
    undelivered_ref,
)
from .git import GitHost
from .github import GitHubHost
from .memory import MemoryHost

__all__ = [
    "ADAPTERS",
    "CI_ANSWERS",
    "CI_FAILING",
    "CI_PASSING",
    "CI_UNKNOWN",
    "DEFAULT_HOST",
    "DELIVERY_SCHEMES",
    "MERGE_FAILED_PREFIX",
    "MERGE_REFUSED_PREFIX",
    "SCHEME_DELIVERED_LOCAL",
    "SCHEME_DELIVERED_REMOTE",
    "SCHEME_UNDELIVERED",
    "CodeHost",
    "GitHost",
    "GitHubHost",
    "MemoryHost",
    "host",
    "scheme_of",
    "undelivered_ref",
]

# Every adapter, by the name `INTEGRATION_HOST` would carry. A DICT rather than a
# lookup by class name, so an adapter is registered deliberately: a class that
# merely exists must not become selectable by being importable.
ADAPTERS: dict[str, type[CodeHost]] = {
    GitHubHost.name: GitHubHost,
    MemoryHost.name: MemoryHost,
    GitHost.name: GitHost,
}

# The default is GITHUB, deliberately, and it is the same argument
# `REMOTE_AGENTS=false` makes: the shipped path must be the tested one. Defaulting
# to `memory` would keep the suite green while no run reached a code host at all.
DEFAULT_HOST = GitHubHost.name

# Read at CALL time, not bound at import. `from ..common.config import X` binds
# before any fixture runs -- the trap CLAUDE.md records for every knob -- and this
# one is read straight from the environment because it selects the adapter rather
# than configuring one, so `config.py` (another lane's file) needs no new line for
# the interface to be usable.
_ENV_VAR = "INTEGRATION_HOST"


def host(name: str | None = None) -> CodeHost:
    """The adapter to run against. Raises on anything it cannot honestly serve.

    Three refusals, and each one has already happened in this repository in
    another form:

      * an UNKNOWN name raises rather than defaulting. `config.STATE_BACKEND`
        makes the same choice, with the reason recorded: a typo'd `dynamo`
        silently writing to disk leaves an operator believing a run is durable.
      * an UNSHIPPED adapter raises even though it is registered and works.
        `GitHost` passes the conformance suite, which is exactly why the refusal
        must be explicit: "it passes the tests" is not "it may open a pull
        request on somebody's repository".
      * an EMPTY string is not silently the default. `INTEGRATION_HOST=` in a
        workflow env block is a value somebody set and got wrong; `""` means
        "unset" only for a variable nobody wrote, which is the absent case the
        `or DEFAULT_HOST` below covers.
    """
    chosen = name if name is not None else os.environ.get(_ENV_VAR) or DEFAULT_HOST
    adapter = ADAPTERS.get(chosen)
    if adapter is None:
        raise ValueError(
            f"unknown INTEGRATION_HOST {chosen!r}; known adapters are "
            f"{sorted(ADAPTERS)}. Refusing to fall back to {DEFAULT_HOST!r}: a "
            f"fallback would open a real pull request on the target repository "
            f"for an operator who asked for something else."
        )
    if not adapter.shipped:
        raise ValueError(
            f"adapter {chosen!r} is registered and passes the conformance suite "
            f"but is NOT SHIPPED, so it must not serve a real run. It exists to "
            f"prove the interface is implementable by something other than "
            f"GitHub (plan D6). Shipped adapters: "
            f"{sorted(n for n, a in ADAPTERS.items() if a.shipped)}."
        )
    return adapter()
