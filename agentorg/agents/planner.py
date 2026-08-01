"""Planner agent — turns a ticket into a PlanResult.

OWNER: Sorour.  Strands agent, deployed on AgentCore (see infra/agentcore/).

Follows the astrolabe pattern: an Agent(create_model(), prompt, tools) exposed
as a FastMCP `run` tool. Stubbed to return the fixture plan until the real
prompt + tools are wired in week 2.
"""

from ..state import RunState, PlanResult
from .. import fixtures_loader

SYSTEM_PROMPT = """You are the Planner. Read the ticket and produce:
- concrete tasks, acceptance criteria, and the files likely to change.
Output must match the PlanResult schema exactly. Do not write code."""


def run(state: RunState) -> PlanResult:
    """STUB: returns the fixture plan. REAL: call the Strands agent on state.ticket_text."""
    # TODO(Sorour, wk2): agent = Agent(create_model(), SYSTEM_PROMPT, tools=[...])
    #                    return PlanResult.model_validate_json(str(agent(state.ticket_text)))
    return fixtures_loader.plan()
