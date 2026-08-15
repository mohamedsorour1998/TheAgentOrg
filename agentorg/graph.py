"""The pipeline graph — walks a ticket through all five agents and three gates.

OWNER: Sorour.

This is the spine of The Agent Org. On day 1 it runs end-to-end on STUBS: every
node returns a validated fixture, so the whole path works before any real agent,
scanner, or GitHub call exists. As each teammate replaces their stub, this file
does not change — the function signatures are frozen in state.py.

Flow:
    plan -> gate1 -> develop -> review -(loop)-> open_pr -> security -> gate2 -> sre -> gate3 -> promote

Human gates are handled by pause()/resume() in gates.py. In this synchronous
demo runner we AUTO-APPROVE gates (auto_approve=True) so a single call walks the
whole path; the real UI/CLI records genuine HumanDecisions.

Run it:
    python -m agentorg.graph            # clean ticket  -> promoted
    python -m agentorg.graph --poisoned # poisoned      -> blocked
"""

from __future__ import annotations

from . import gates, github_ops, log
from .agents import developer, planner, reviewer, security, sre
from .common import config
from .state import (
    HumanDecision,
    LogEvent,
    RunState,
)


def _log(state: RunState, actor, stage, action, verdict="", summary=""):
    log.append(LogEvent(
        run_id=state.run_id, ticket_id=state.ticket_id,
        actor=actor, stage=stage, action=action, verdict=verdict, summary=summary,
    ))


def _auto_gate(state: RunState, gate: str) -> HumanDecision:
    """Demo helper: record an auto-approval. The real UI replaces this."""
    gates.pause(state, gate)
    return HumanDecision(gate=gate, decision="approved", by="auto", reason="demo auto-approve")


def run_pipeline(ticket_id: str, ticket_text: str, *, poisoned: bool = False,
                 auto_approve: bool = True) -> RunState:
    """Walk one ticket through the whole pipeline. Returns the final RunState."""
    state = RunState(ticket_id=ticket_id, ticket_text=ticket_text)
    _log(state, "system", "plan", "opened", summary=f"run started for {ticket_id}")

    # 1. PLAN ---------------------------------------------------------------
    state.plan = planner.run(state)
    _log(state, "planner", "plan", "proposed", summary=f"{len(state.plan.tasks)} tasks")

    # 2. GATE 1 -------------------------------------------------------------
    if auto_approve:
        state.decisions.append(_auto_gate(state, "gate1"))

    # 3. DEVELOP + REVIEW LOOP ---------------------------------------------
    while True:
        state.dev = developer.run(state, poisoned=poisoned)
        _log(state, "developer", "develop", "proposed", summary=state.dev.summary)

        state.review = reviewer.run(state)
        if state.review.verdict == "approve" or state.revision_count >= config.MAX_REVISION_LOOPS:
            _log(state, "reviewer", "review", "reviewed", verdict=state.review.verdict)
            break
        state.revision_count += 1
        _log(state, "reviewer", "review", "reviewed", verdict="changes_requested",
             summary=f"revision {state.revision_count}")

    # 4. OPEN PR (Mariam's seam) -------------------------------------------
    state.dev = github_ops.open_pr(state)
    _log(state, "system", "develop", "opened", summary=f"PR {state.dev.pr_url}",
         )

    # 5. SECURITY (deterministic block rule) -------------------------------
    state.security = security.run(state)
    _log(state, "security", "security", "blocked" if state.security.verdict == "block" else "passed",
         verdict=state.security.verdict, summary=f"{len(state.security.blocking)} blocking")
    if state.security.verdict == "block":
        state.status = "blocked"
        github_ops.post_comment(state, state.security.explanation)
        _log(state, "system", "security", "blocked", summary="pipeline halted by block rule")
        return state

    # 6. GATE 2 -------------------------------------------------------------
    if auto_approve:
        state.decisions.append(_auto_gate(state, "gate2"))

    # 7. SRE ----------------------------------------------------------------
    state.sre = sre.run(state)
    _log(state, "sre", "sre", "reviewed", verdict=state.sre.verdict)
    if state.sre.verdict == "no_go":
        state.status = "failed"
        return state

    # 8. GATE 3 + PROMOTE ---------------------------------------------------
    if auto_approve:
        state.decisions.append(_auto_gate(state, "gate3"))
    state.status = "promoted"
    _log(state, "system", "promote", "promoted", summary="change promoted")
    return state


if __name__ == "__main__":
    import sys
    poisoned = "--poisoned" in sys.argv
    tid = "DEMO-POISON" if poisoned else "DEMO-CLEAN"
    final = run_pipeline(tid, "Add a per-IP login rate limit.", poisoned=poisoned)
    print(f"\nrun_id={final.run_id}")
    print(f"status={final.status}")
    if final.security:
        print(f"security verdict={final.security.verdict}, blocking={len(final.security.blocking)}")
