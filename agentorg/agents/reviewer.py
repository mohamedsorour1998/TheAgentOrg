"""Reviewer agent — reviews a DevResult, returns approve or changes_requested.

OWNER: Sorour.  Strands agent on AgentCore.

When the verdict is changes_requested, the graph loops back to the developer
(capped by config.MAX_REVISION_LOOPS). That loop is wired in graph.py.
"""

from ..state import RunState, ReviewResult
from .. import fixtures_loader

SYSTEM_PROMPT = """You are the Reviewer. Read the diff and either approve it or
request changes with specific, actionable notes. Output must match the
ReviewResult schema."""


def run(state: RunState) -> ReviewResult:
    """STUB: returns the fixture (approve). REAL: call the Strands agent on state.dev."""
    # TODO(Sorour, wk2): real agent call; return changes_requested to exercise the loop.
    return fixtures_loader.review()
