"""Record ONE gate decision. THE ONLY WRITE IN LANE I. OWNER: Lane I.

Invoked by `web/lib/approvals.ts` as a subprocess with a JSON request on stdin.

=========================================================================
ONE WRITE, TO THE QUEUE. NOT TO `gates.resume`.
=========================================================================
`scripts/worker.py:approve` records the measured defect this file exists to avoid,
and it is worth restating because getting it wrong is invisible. Its first version
called `gates.resume` as well as `queue.resume`, and the run's state then carried
the decision TWICE:

    status: blocked
    decisions:
       gate1 approved by tester
       gate1 approved by github-environment-reviewer

Two rows for one click, the second attributed to a reviewer who does not exist on
this path -- `run_stage._stage_gate` hardcodes that `by` for the GitHub Environment
it is named after. On a timeline a judge reads, one human decision renders as two.

THE CAUSE was writing at both layers. `queue.resume` makes the gate's job claimable
and the gate STAGE then runs, and that stage's entire body is `gates.resume(...)`.
The queue's job is to decide WHEN the recorder may run, not to duplicate WHAT it
writes. `gates.py:37`'s "one writer" rule is the same principle.

So: this file calls `queue.resume` and nothing else. It does NOT import `gates`.

=========================================================================
WHY `by` TRAVELS ON THE ROW
=========================================================================
`queue.resume(approver=...)` puts it there because "the person clicks in one process
and the stage that records their name runs in another, minutes later". Without it
every queued approval reaches `_stage_gate`'s default and is recorded as
`github-environment-reviewer` -- naming a GitHub Environment that never held this
job, on the one field whose whole purpose is attributing a decision to a human.

That field is the entire difference between this surface and
`agentorg/approve_server.py`, which records `by="ui-reviewer"` for every decision
because with no authentication it genuinely does not know who clicked.

=========================================================================
WHAT THIS FILE DOES NOT CHECK, AND WHY THAT IS CORRECT
=========================================================================
It does not re-decide whether the approval is permitted. `web/lib/authz.ts` does
that, over facts measured by `web/lib/reader/runs.py`, and its refusals are tested
against every case this repository has recorded. A second decision path here whose
only job is to agree with the first is the shape `scoring.score_findings` refuses
for the same reason: "an audit artifact that can disagree with the decision it
describes is worse than none: it reads as proof."

It DOES validate the vocabulary, because `queue.resume` is reached from here and a
malformed value must not travel: an unknown gate is a run nothing can release, and
an unknown decision must be a loud error rather than a quiet approval.
"""

from __future__ import annotations

import json
import logging
import sys

from agentorg import log, queue

# The three gates and the two decisions this surface will record. NARROWER than
# `queue.APPROVING_DECISIONS`, which includes `overridden` -- and that omission is
# deliberate and is checked here as well as in `authz.ts`, because this file is
# reachable independently of that one. `approve_server.py` made the same trade:
# overriding a security block requires shell access, not a network call.
_GATES = ("gate1", "gate2", "gate3")
_DECISIONS = ("approved", "rejected")


def _fail(message: str, detail: str = "") -> int:
    json.dump({"error": message, "detail": detail}, sys.stdout)
    return 0


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except Exception as error:
        logging.getLogger(__name__).warning(
            "the approval writer could not parse its request", exc_info=True)
        return _fail("the request could not be parsed", str(error))

    if not isinstance(request, dict):
        return _fail("the writer expects a JSON object")

    run_id = request.get("run_id")
    gate = request.get("gate")
    decision = request.get("decision")
    by = request.get("by")
    reason = request.get("reason", "")

    # THE RUN ID IS VALIDATED BEFORE IT TRAVELS. `queue.resume` addresses a paused
    # job by (run_id, gate) and a worker later hands that id to a subprocess as
    # `--run-id`, so it is the same guard `queue.enqueue` applies. The value is NOT
    # echoed: it is untrusted and this message can reach a rendered page.
    if not isinstance(run_id, str) or not log.is_safe_run_id(run_id):
        return _fail("no such run")

    if gate not in _GATES:
        return _fail(f"gate must be exactly one of {', '.join(_GATES)}")

    if decision == "overridden":
        # NAMED SEPARATELY from an unrecognised word, because "you named something
        # real that this surface will not do" and "you named nothing I recognise" are
        # different facts, and an operator reading an audit log needs to tell them
        # apart. Folding them would make a deliberate policy read as a typo.
        return _fail(
            "an override cannot be recorded from the web application; it requires "
            "shell access: python -m agentorg.gates_cli resume <run_id> "
            "--gate <gate> --decision overridden --by <you>"
        )

    if decision not in _DECISIONS:
        return _fail(f"decision must be exactly one of {', '.join(_DECISIONS)}")

    # A BLANK `by` IS REFUSED. It is the field this whole surface exists to populate
    # honestly, and a blank one reaching `queue.resume` becomes
    # `approver="queue-operator"` -- a constant, which is the `approve_server` defect
    # with a different word. The caller builds this from a verified session; a blank
    # here means the caller is broken, and proceeding would record a decision against
    # nobody.
    if not isinstance(by, str) or not by.strip():
        return _fail(
            "a decision needs the identity of the person who made it; nothing was "
            "recorded"
        )

    if not isinstance(reason, str):
        return _fail("reason must be text")

    try:
        job = queue.resume(run_id, gate=gate, decision=decision,
                           approver=by, reason=reason)
    except Exception as error:
        logging.getLogger(__name__).exception("the decision could not be recorded")
        # THE TYPE IS NAMED. `queue.resume` raises for a run that is not paused at
        # that gate, and a bare "it failed" would be indistinguishable from a crash --
        # the reassuring non-answer this repository refuses.
        return _fail("the decision could not be recorded",
                     f"{type(error).__name__}: {error}")

    json.dump({
        # THE QUEUE'S OWN VIEW, read back rather than echoed. If `queue.resume` ever
        # recorded a different approver than the one sent, this response would say so
        # instead of reassuring the caller.
        "status": job.status,
        "by": job.decided_by,
        "gate": job.awaiting_gate or gate,
        "stage": job.stage,
    }, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
