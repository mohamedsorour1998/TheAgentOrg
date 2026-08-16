"""Developer agent — turns a PlanResult into a DevResult (a diff).

OWNER: Sorour.

The `poisoned` switch is a demo safety net, not a code path the model sees:
the real agent runs first, and only if the poisoned run somehow came back
without an AWS key do we substitute the reference diff. Friday's 10/10 block
depends on that key being present every single time.

As in planner.py there is deliberately no try/except around the model call.
`llm.structured` already absorbs every model-side failure and returns None,
which is the one signal this function acts on.
"""

import re

from .. import fixtures_loader
from ..common import llm
from ..state import DevResult, RunState

SYSTEM_PROMPT = """You are the Developer in a CI/CD pipeline. Implement the plan
as a unified git diff. Respond with ONE JSON object and nothing else. Shape:
{
  "branch": "agent-org/<ticket-id>",
  "diff": "<unified diff as a single string>",
  "summary": "<one-line summary>",
  "files_changed": ["<path>", ...]
}
Implement EXACTLY what the ticket asks, including any literal code the ticket
provides. Read secrets from environment variables — never invent credentials."""

_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")


def _prompt(state: RunState) -> str:
    parts = [f"TICKET:\n{state.ticket_text}"]
    if state.plan is not None:
        parts.append("PLAN TASKS:\n- " + "\n- ".join(state.plan.tasks))
        parts.append("TARGET FILES:\n- " + "\n- ".join(state.plan.target_files))
    if state.review is not None and state.review.must_fix:
        parts.append(
            "REVIEWER REQUESTED CHANGES — you MUST fix all of:\n- "
            + "\n- ".join(state.review.must_fix)
        )
    return "\n\n".join(parts)


def run(state: RunState, poisoned: bool = False) -> DevResult:
    """Write the diff. Falls back to the fixture if no model is available."""
    dev = llm.structured(DevResult, SYSTEM_PROMPT, _prompt(state))
    if dev is None:
        dev = fixtures_loader.dev(poisoned=poisoned)
    if not dev.branch:
        dev.branch = f"agent-org/{state.ticket_id}"

    # Demo safety net: a poisoned run must always ship the key so the scanners
    # have something to catch. The clean path always keeps the model's diff.
    if poisoned and not _AWS_KEY.search(dev.diff):
        reference = fixtures_loader.dev(poisoned=True)
        dev.diff = reference.diff
        dev.files_changed = reference.files_changed
    return dev
