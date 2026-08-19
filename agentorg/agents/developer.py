"""Developer agent — turns a PlanResult into a DevResult (a diff).

OWNER: Sorour.

The `poisoned` switch is a demo safety net, not a code path the model sees:
the real agent runs first, and only if the poisoned run came back without an
AWS key ON AN ADDED LINE do we substitute the reference diff. Friday's 10/10
block depends on that key being present every single time, and "present" means
present in the change the scanners will actually read -- see
_key_is_in_the_change below, which is where that distinction was lost once.

As in planner.py there is deliberately no try/except around the model call.
`llm.structured` already absorbs every model-side failure and returns None,
which is the one signal this function acts on.
"""

import re

from .. import fixtures_loader
from ..common import llm
from ..common.diff import added_files
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


def _key_is_in_the_change(diff: str) -> bool:
    """Would a scanner find an AWS key in what this diff PROPOSES?

    Added lines only, because that is the whole of what the scanners read --
    agentorg/common/diff.py has the full account, and both sides now ask it the
    same question. The version this replaced searched the whole diff string:

        if poisoned and not _AWS_KEY.search(dev.diff):

    which counts a key on a `-` line, i.e. a key the change REMOVES. That is
    the shape every revision after the first takes, because the reviewer
    correctly asks for the hardcoded credentials to be deleted and the model
    does it. The safety net then declined to substitute, the scanners were
    handed a change with no secret in it, and the poisoned ticket promoted --
    2 blocks in 5 live runs. Do not widen this back to the whole diff text.
    """
    return any(_AWS_KEY.search(body) for body in added_files(diff).values())


def _prompt(state: RunState) -> str:
    parts = [f"TICKET:\n{state.ticket_text}"]
    if state.plan is not None:
        parts.append("PLAN TASKS:\n- " + "\n- ".join(state.plan.tasks))
        parts.append("TARGET FILES:\n- " + "\n- ".join(state.plan.target_files))
    if state.review is not None and state.review.must_fix:
        # This is a revision, not a first pass. graph.py re-calls run() before
        # it overwrites state.dev, so state.dev still holds the diff the
        # reviewer objected to. Send it: without it the model is asked to fix
        # problems in a diff it cannot see, and "revise" silently degrades into
        # "regenerate from the ticket with a hint". Guarded on state.dev so the
        # first pass, which has none, keeps its original prompt unchanged.
        if state.dev is not None:
            parts.append(
                "YOUR PREVIOUS DIFF — revise THIS, do not start over:\n"
                + state.dev.diff
            )
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
    #
    # `summary` is deliberately NOT rewritten here, and a test depends on that.
    # Only diff and files_changed are swapped, so the model's own summary
    # survives -- that is the only observable difference between this rescue
    # path and a plain fixture fallback, and
    # test_poisoned_safety_net_rescues_a_clean_model_diff asserts on it to prove
    # the key came from the safety net rather than from falling back wholesale.
    # It does leave a poisoned run whose summary can read "no secrets here"; if
    # you want to fix that cosmetic mismatch, give the test a different way to
    # tell the two paths apart FIRST. Deleting the assertion to make a summary
    # swap pass restores exactly the vacuous test this guards against.
    if poisoned and not _key_is_in_the_change(dev.diff):
        reference = fixtures_loader.dev(poisoned=True)
        dev.diff = reference.diff
        dev.files_changed = reference.files_changed
    return dev
