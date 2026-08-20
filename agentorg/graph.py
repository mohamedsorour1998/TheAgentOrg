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
are four ways it does not, and they are checked in this order:
    security verdict "block"      -> status "blocked"   (deterministic rule)
    reviewer never approved       -> status "failed"    (revision budget spent)
    sre verdict "no_go"           -> status "failed"
    a human said no at any gate   -> status "rejected"
"rejected" is reserved for the human ones. An agent's refusal is a "failed" run,
because nobody was asked.

The order is load-bearing: the deterministic block is evaluated on every run
that produced a diff and wins over every other stop, because "the poisoned
ticket blocks" is a claim about code, not about what a model thought of the
diff. See the comment at step 5b.

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


def _log(state: RunState, actor, stage, action, verdict="", summary="",
         artifact_ref="", scan_provenance=""):
    """Append one row to the run's log.

    WIDENED IN WEEK 3, by ADDITION only -- both new parameters default to "", so
    every existing call site writes the same SEMANTIC row as before.

    NOT the same BYTES, and the difference is worth stating because an earlier
    version of this docstring claimed it was. `model_dump()` emits defaults, and
    this helper passes both parameters unconditionally, so every row now carries
    a `"scan_provenance": ""` key -- MEASURED on a full run of each kind: 14/14
    rows on a clean run and 9/9 on a poisoned one carry the key, while only 1 and
    2 respectively carry a non-empty value. Old readers ignore an extra key and
    rows validate in both directions, so nothing breaks; but "identical bytes"
    was false and a later reader would have relied on it.

    `gates.py:58` and `:80` construct LogEvent directly rather than through here,
    so their rows carry the key too. That is correct -- a gate row has no scan
    provenance to report, and "" is exactly what it should say.

    The two parameters exist because agentorg/timeline.py may read nothing but
    log.read(run_id), which makes this helper the only route by which a fact can
    reach the judges:

      * artifact_ref  the delivery ref for a block reason. It was already being
        recorded, but only INSIDE a summary sentence, so reading it back meant
        string-parsing prose. It now goes in the field state.py named for it
        ("PR url, branch, path to findings json") as well.
      * scan_provenance  whether the verdict came from real scanners or a
        fixture. See state.ScanProvenance.

    The summary keeps carrying the ref too, deliberately: every run already on
    disk has it there and nowhere else, and the timeline must render those.
    """
    log.append(LogEvent(
        run_id=state.run_id, ticket_id=state.ticket_id,
        actor=actor, stage=stage, action=action, verdict=verdict, summary=summary,
        artifact_ref=artifact_ref, scan_provenance=scan_provenance,
    ))


def _auto_gate(state: RunState, gate: str) -> HumanDecision:
    """Demo helper: record an auto-approval. The real UI replaces this."""
    gates.pause(state, gate)
    return HumanDecision(gate=gate, decision="approved", by="auto", reason="demo auto-approve")


# Exact words, not a prefix. A prefix match on "a" made "abort" — the most
# natural way to bail out of a prompt you did not mean to be at — mean APPROVE,
# on the three prompts in this system where being misread is most expensive.
# Everything not in this set rejects, so bare Enter still fails closed.
APPROVAL_WORDS = frozenset({"a", "approve", "approved", "y", "yes"})


def _cli_gate(state: RunState, gate: str) -> HumanDecision:
    """Real gate: pause, ask a human on the terminal, record their decision."""
    path = gates.pause(state, gate)
    print(f"\n[{gate}] paused. state saved -> {path}")
    answer = input(f"[{gate}] approve / reject? [a = approve, anything else rejects] ")
    decision = "approved" if answer.strip().lower() in APPROVAL_WORDS else "rejected"
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
    """Walk one ticket through the whole pipeline. Returns the final RunState.

    The walk itself is _walk; this wrapper exists only to guarantee the ending
    is written down. _walk has seven `return state` exits, so a `gates.save`
    before each one would be seven chances to forget one — and the eighth exit
    somebody adds next month would be wrong by default. A finally clause is
    wrong by default in the safe direction instead: it also persists a run that
    died on an exception, which is exactly the run someone needs to inspect
    afterwards, and which no `return`-site save can reach at all.

    state is built here rather than in _walk so the finally clause has something
    to save even if _walk raises on its first line.
    """
    state = RunState(ticket_id=ticket_id, ticket_text=ticket_text)
    try:
        return _walk(state, poisoned=poisoned, auto_approve=auto_approve)
    finally:
        gates.save(state)


def _walk(state: RunState, *, poisoned: bool, auto_approve: bool) -> RunState:
    """The pipeline itself. Always call through run_pipeline, never directly."""
    _log(state, "system", "plan", "opened", summary=f"run started for {state.ticket_id}")
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

    # 4. OPEN PR (Mariam's seam) -------------------------------------------
    state.dev = github_ops.open_pr(state)
    _log(state, "system", "develop", "opened", summary=f"PR {state.dev.pr_url}",
         )

    # 5. SECURITY (deterministic block rule) -------------------------------
    state.security = security.run(state)
    # scan_provenance is the answer to "did the scanners actually run, or did
    # this verdict come from a fixture?" -- a question the count in `summary`
    # cannot answer, because the fixture fallback produces a real count too.
    _log(state, "security", "security", "blocked" if state.security.verdict == "block" else "passed",
         verdict=state.security.verdict, summary=f"{len(state.security.blocking)} blocking",
         scan_provenance=state.security.scan_provenance)
    if state.security.verdict == "block":
        state.status = "blocked"
        # The ref is written down rather than dropped on the floor. post_comment
        # cannot raise, so a delivery failure leaves no trace unless it is
        # recorded: it returns the comment's https:// URL when the reason
        # reached the PR, and comment://<run_id> when it did not. This log row
        # is the artifact -- runs/<run_id>.jsonl is what log.py calls the source
        # of truth the timeline UI renders -- and without the ref that file is
        # byte-identical whether the block was reported or evaporated into a 502.
        ref = github_ops.post_comment(state, state.security.explanation)
        # The ref goes in artifact_ref AND stays in the summary. Both, because
        # they answer to different readers: artifact_ref is the field state.py
        # names for exactly this ("PR url, branch, path to findings json") and
        # is what agentorg/timeline.py classifies without parsing prose, while
        # the summary sentence is what every run already on disk carries and
        # what `cat runs/<run_id>.jsonl` shows in demo beat 5. Dropping it from
        # the summary would rewrite the demo script to save a duplicated string.
        _log(state, "system", "security", "blocked",
             summary=f"pipeline halted by block rule; block reason {ref}",
             artifact_ref=ref, scan_provenance=state.security.scan_provenance)
        return state

    # 5b. THE REVIEWER'S VERDICT IS TERMINAL --------------------------------
    # Reached only by the cap exit from the loop above: the revision budget is
    # spent and the reviewer is still asking for changes, so nobody has approved
    # this change and it must not promote. Treated as sre.verdict == "no_go" is
    # below, and "failed" rather than "rejected" because no human was asked.
    #
    # Placed AFTER the security stage, deliberately. The block rule is
    # deterministic code, and the whole premise of this pipeline is that the
    # block is not a model's judgement — so it must be evaluated on every run
    # that produced a diff, whatever the reviewer thought of that diff. Stopping
    # here first would invert that: on the poisoned ticket a competent reviewer
    # SHOULD object to the hardcoded credentials, the developer's safety net
    # re-inserts the key on every revision, so the cap would reliably exhaust
    # and the run would end "failed" without the scanners ever running. That
    # quietly downgrades "the poisoned ticket blocks every single time" into
    # "it fails at review", which is a different and weaker claim.
    #
    # So a block wins above and returns; only a run the scanners CLEARED can be
    # stopped here for never having been approved.
    if state.review.verdict != "approve":
        state.status = "failed"
        _log(state, "system", "review", "blocked", verdict=state.review.verdict,
             summary=f"scanners passed, but the reviewer never approved after "
                     f"{state.revision_count} revisions; not promoting")
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
