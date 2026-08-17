"""The pipeline graph — walks a ticket through all five agents and three gates.

OWNER: Sorour.

This is the spine of The Agent Org. On day 1 it runs end-to-end on STUBS: every
node returns a validated fixture, so the whole path works before any real agent,
scanner, or GitHub call exists. As each teammate replaces their stub, this file
does not change — the function signatures are frozen in state.py.

Flow:
    plan -> gate1 -> develop -> review -(loop)-> open_pr -> security -> gate2 -> sre -> gate3 -> promote

Human gates are handled by pause()/resume() in gates.py. The demo runner
AUTO-APPROVES them (auto_approve=True) so a single call walks the whole path;
auto_approve=False asks a real human on the terminal, and agentorg/gates_cli.py
records the same decision out of band for anyone who walked away.

A run reaches the end of this file only by being approved at every step. There
are four ways it does not:
    security verdict "block"      -> status "blocked"   (deterministic rule)
    reviewer never approved       -> status "failed"    (revision budget spent)
    sre verdict "no_go"           -> status "failed"
    a human said no at any gate   -> status "rejected"
"rejected" is reserved for the human ones. An agent's refusal is a "failed" run,
because nobody was asked.

Run it:
    python -m agentorg.graph            # clean ticket  -> promoted
    python -m agentorg.graph --poisoned # poisoned      -> blocked
"""

from __future__ import annotations

import os
from collections.abc import Callable

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


def _cli_gate(state: RunState, gate: str) -> HumanDecision:
    """Real gate: pause, ask a human on the terminal, record their decision."""
    path = gates.pause(state, gate)
    print(f"\n[{gate}] paused. state saved -> {path}")
    answer = input(f"[{gate}] approve / reject? ").strip().lower()
    decision = "approved" if answer.startswith("a") else "rejected"
    return HumanDecision(gate=gate, decision=decision,
                         by=os.environ.get("USER", "human"))


def _decide(state: RunState, gate: str, ask: Callable[[RunState, str], HumanDecision]) -> bool:
    """Take the human decision at one gate. False means the run stops here.

    Recording the decision and honouring it live together rather than at each
    of the three call sites, because the two failures that matter are a gate
    that was recorded but not honoured and a gate that was honoured but not
    recorded — and both are easy to write by hand three times in a row.

    Every decision is logged, not only the refusals. gates.resume() already logs
    the async ones, so without this the timeline would show a run pausing at a
    gate and then simply carrying on, with no record of who let it through.
    """
    decision = ask(state, gate)
    state.decisions.append(decision)
    stopping = decision.decision == "rejected"
    _log(state, "human", gate, decision.decision, verdict=decision.decision,
         summary=decision.reason or (f"run stopped at {gate}" if stopping else ""))
    if stopping:
        state.status = "rejected"
    return not stopping


def run_pipeline(ticket_id: str, ticket_text: str, *, poisoned: bool = False,
                 auto_approve: bool = True) -> RunState:
    """Walk one ticket through the whole pipeline. Returns the final RunState."""
    state = RunState(ticket_id=ticket_id, ticket_text=ticket_text)
    _log(state, "system", "plan", "opened", summary=f"run started for {ticket_id}")
    ask = _auto_gate if auto_approve else _cli_gate

    # 1. PLAN ---------------------------------------------------------------
    state.plan = planner.run(state)
    _log(state, "planner", "plan", "proposed", summary=f"{len(state.plan.tasks)} tasks")

    # 2. GATE 1 -------------------------------------------------------------
    if not _decide(state, "gate1", ask):
        return state

    # 3. DEVELOP + REVIEW LOOP ---------------------------------------------
    # Two exits, and they are not the same outcome, so they do not share a log
    # line: one says the reviewer approved this diff, the other says the run
    # ran out of chances to make one it would.
    while True:
        state.dev = developer.run(state, poisoned=poisoned)
        _log(state, "developer", "develop", "proposed", summary=state.dev.summary)

        state.review = reviewer.run(state)
        if state.review.verdict == "approve":
            _log(state, "reviewer", "review", "reviewed", verdict="approve",
                 summary="reviewer approved the diff")
            break
        if state.revision_count >= config.MAX_REVISION_LOOPS:
            _log(state, "reviewer", "review", "reviewed", verdict="changes_requested",
                 summary=f"revision cap of {config.MAX_REVISION_LOOPS} reached, "
                         f"changes still requested")
            break
        state.revision_count += 1
        _log(state, "reviewer", "review", "reviewed", verdict="changes_requested",
             summary=f"revision {state.revision_count}")

    # 3b. THE REVIEWER'S VERDICT IS TERMINAL --------------------------------
    # Reached only by the cap exit above: the budget is spent and the reviewer
    # is still asking for changes, so nobody has approved this change. Promoting
    # it would claim an approval that never happened, and opening a PR would
    # publish a diff its own reviewer objected to — three times. Treated exactly
    # as sre.verdict == "no_go" is below, and "failed" rather than "rejected"
    # because no human was asked; see the module docstring.
    if state.review.verdict != "approve":
        state.status = "failed"
        _log(state, "system", "review", "blocked", verdict=state.review.verdict,
             summary=f"not promoting: {state.revision_count} revisions spent and "
                     f"the reviewer never approved")
        return state

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
    if not _decide(state, "gate2", ask):
        return state

    # 7. SRE ----------------------------------------------------------------
    state.sre = sre.run(state)
    _log(state, "sre", "sre", "reviewed", verdict=state.sre.verdict)
    if state.sre.verdict == "no_go":
        state.status = "failed"
        return state

    # 8. GATE 3 + PROMOTE ---------------------------------------------------
    if not _decide(state, "gate3", ask):
        return state
    state.status = "promoted"
    _log(state, "system", "promote", "promoted", summary="change promoted")
    return state


if __name__ == "__main__":
    import sys
    poisoned = "--poisoned" in sys.argv
    # Without this flag the interactive gate is reachable only by importing
    # run_pipeline, which makes "the gates are real" a claim nobody can check
    # from a terminal. The default is unchanged: no flag, no prompts.
    interactive = "--interactive" in sys.argv
    tid = "DEMO-POISON" if poisoned else "DEMO-CLEAN"
    final = run_pipeline(tid, "Add a per-IP login rate limit.", poisoned=poisoned,
                         auto_approve=not interactive)
    print(f"\nrun_id={final.run_id}")
    print(f"status={final.status}")
    if final.security:
        print(f"security verdict={final.security.verdict}, blocking={len(final.security.blocking)}")
