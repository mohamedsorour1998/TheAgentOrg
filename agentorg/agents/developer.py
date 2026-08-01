"""Developer agent — turns a PlanResult into a DevResult (a diff).

OWNER: Sorour.  Strands agent on AgentCore.

The `poisoned` switch lets the demo choose which fixture diff to emit so we can
show both the clean run (passes) and the poisoned run (blocked). In the real
agent this decision goes away — the model writes whatever the ticket asks for.
"""

from ..state import RunState, DevResult
from .. import fixtures_loader

SYSTEM_PROMPT = """You are the Developer. Given the plan, produce a unified diff
that implements it, a short summary, and the list of files changed. Output must
match the DevResult schema. Never invent credentials or secrets."""


def run(state: RunState, poisoned: bool = False) -> DevResult:
    """STUB: returns a fixture diff. REAL: call the Strands agent on state.plan."""
    # TODO(Sorour, wk2): real agent call using state.plan.
    return fixtures_loader.dev(poisoned=poisoned)
