"""Human gate behaviour: interactive halt, async resume, and an unapproved stop.

Owner: Sorour.

Three things are pinned here, and they are the three ways a run can stop without
being promoted:

  * a human says no at gate 1, 2 or 3 -> status "rejected";
  * a human says no later, out of band, through gates_cli -> status "rejected";
  * the REVIEWER never said yes and the revision budget ran out -> status
    "failed". No human is involved in that one, which is why it is not
    "rejected": in this contract "rejected" denotes a human decision.

The third one is checked AFTER the security stage, and two tests here exist to
hold it there: a poisoned diff must still reach the deterministic block rule and
end "blocked" even when the reviewer refused it on every pass. The block is code,
not a model's judgement, so nothing a model decided may skip it.

On patching builtins.input: every script below is FINITE and records what it
was asked. conftest's autouse fixture makes an unpatched read fail loudly, and
these scripts make an over-eager read fail loudly, so neither a gate that stops
asking nor a gate that starts asking twice can pass quietly -- and neither can
block the suite on stdin. See the SEAM 3 section of tests/conftest.py.
"""

import builtins
import sys

import pytest

from agentorg import gates, gates_cli, github_ops, graph, log
from agentorg.agents import reviewer, security, sre
from agentorg.common import config
from agentorg.state import HumanDecision, PlanResult, ReviewResult, RunState

TICKET = "Add a per-IP login rate limit."


def _answer_gates(monkeypatch, answers: list[str]) -> list[str]:
    """Script the gate prompts in order. Returns the list of prompts asked.

    Runs out deliberately: a code path that asks more often than the test
    scripted fails by name instead of being answered forever by a lambda.
    """
    asked: list[str] = []
    remaining = list(answers)

    def _scripted_input(prompt: str = "") -> str:
        asked.append(prompt)
        if not remaining:
            pytest.fail(
                f"the pipeline asked {len(asked)} questions but only "
                f"{len(answers)} answers were scripted; the unscripted prompt "
                f"was {prompt!r}",
                pytrace=False,
            )
        return remaining.pop(0)

    monkeypatch.setattr(builtins, "input", _scripted_input)
    return asked


def _decided(state: RunState) -> list[tuple[str, str]]:
    return [(d.gate, d.decision) for d in state.decisions]


def _on_disk(state: RunState) -> RunState:
    """The run as the next process would find it — the CLI's whole input."""
    return RunState.model_validate_json(gates._state_path(state.run_id).read_text())


def _explode(state: RunState) -> RunState:
    """Stand-in for any stage dying mid-run. Must not be a lambda: it raises."""
    raise RuntimeError("ci runner exploded mid-run")


def _capture_run(monkeypatch) -> list[RunState]:
    """Record the live RunState, for runs that raise instead of returning one.

    Hooks gates.pause rather than gates.save, so a test about what save() wrote
    is not reading its answer out of the thing under test.
    """
    captured: list[RunState] = []
    real_pause = gates.pause
    monkeypatch.setattr(gates, "pause", lambda state, gate: (
        captured.append(state) or real_pause(state, gate)
    ))
    return captured


def _never_approves(monkeypatch) -> None:
    """A reviewer that objects on every pass, with a real objection attached."""
    monkeypatch.setattr(reviewer, "run", lambda state: ReviewResult(
        verdict="changes_requested",
        must_fix=["still returns 200 on the 6th attempt"],
    ))


# --------------------------------------------------------------------------
# The interactive gate: a human on the terminal can stop the run at any of three
# points, and each point stops something specific from happening.
# --------------------------------------------------------------------------

def test_rejecting_gate1_stops_the_run(monkeypatch):
    asked = _answer_gates(monkeypatch, ["r"])
    state = graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)

    assert state.status == "rejected"
    assert state.dev is None, "a gate-1 reject must stop before the developer runs"
    assert _decided(state) == [("gate1", "rejected")]
    assert len(asked) == 1, "gate 1 must actually ask, and nothing after it may"


def test_rejecting_gate2_stops_the_run_before_the_sre_agent(monkeypatch):
    asked = _answer_gates(monkeypatch, ["a", "r"])
    state = graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)

    assert state.status == "rejected"
    assert _decided(state) == [("gate1", "approved"), ("gate2", "rejected")]
    # gate 2 sits between the scanners and the SRE agent: the security result is
    # what the human was shown, and the SRE agent is what they stopped.
    assert state.security is not None and state.security.verdict == "pass"
    assert state.sre is None, "a gate-2 reject must stop before the SRE agent runs"
    assert len(asked) == 2


def test_rejecting_gate3_stops_a_run_the_sre_agent_cleared(monkeypatch):
    asked = _answer_gates(monkeypatch, ["a", "a", "r"])
    state = graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)

    # Every agent said go. Gate 3 exists precisely so that is not sufficient.
    assert state.sre is not None and state.sre.verdict == "go"
    assert state.status == "rejected"
    assert _decided(state) == [
        ("gate1", "approved"), ("gate2", "approved"), ("gate3", "rejected"),
    ]
    assert len(asked) == 3


def test_approving_every_gate_promotes(monkeypatch):
    asked = _answer_gates(monkeypatch, ["a", "a", "a"])
    state = graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)

    assert state.status == "promoted"
    assert _decided(state) == [
        ("gate1", "approved"), ("gate2", "approved"), ("gate3", "approved"),
    ]
    assert len(asked) == 3, "three gates, three questions"
    assert all(d.by for d in state.decisions), "every decision must name a decider"


def test_auto_approve_still_works():
    """The demo path must not consult a terminal.

    Deliberately no input patch: conftest's autouse guard turns builtins.input
    into a loud failure, so an auto_approve run that reached the interactive
    prompt fails by name here rather than blocking on stdin. The `by` field is
    what separates the two paths once both record decisions.
    """
    state = graph.run_pipeline("CLEAN-1", TICKET)

    assert state.status == "promoted"
    assert [d.decision for d in state.decisions] == ["approved"] * 3
    assert [d.by for d in state.decisions] == ["auto"] * 3


# --------------------------------------------------------------------------
# The async path: pause writes a state a human comes back to later.
# --------------------------------------------------------------------------

def test_resume_records_a_rejection():
    state = RunState(ticket_id="CLEAN-1", ticket_text=TICKET)
    state.plan = PlanResult(
        tasks=["add a token bucket to the login handler"],
        acceptance_criteria=["429 on the 6th attempt within a minute"],
        target_files=["app/auth.py"],
    )
    gates.pause(state, "gate1")

    resumed = gates.resume(
        state.run_id,
        HumanDecision(gate="gate1", decision="rejected", by="sorour", reason="wrong plan"),
    )

    assert resumed.status == "rejected"
    assert resumed.decisions[-1].by == "sorour"
    assert resumed.decisions[-1].reason == "wrong plan"
    # The round trip must carry the run's WORK across the pause, not just its
    # id -- a human deciding on gate 1 is deciding about exactly this plan.
    assert resumed.run_id == state.run_id
    assert resumed.plan is not None
    assert resumed.plan.tasks == ["add a token bucket to the login handler"]


def test_the_cli_records_a_decision_against_a_paused_run(monkeypatch, capsys):
    state = RunState(ticket_id="CLEAN-1", ticket_text=TICKET)
    gates.pause(state, "gate1")

    monkeypatch.setattr(sys, "argv", [
        "agentorg.gates_cli", "resume", state.run_id,
        "--gate", "gate1", "--decision", "rejected",
        "--by", "sorour", "--reason", "the plan targets the wrong handler",
    ])
    gates_cli.main()

    assert f"run_id={state.run_id}" in capsys.readouterr().out
    # The decision the CLI took must outlive the process, in BOTH records: the
    # append-only log, which is what the timeline renders, and the state file,
    # which is what the next decision resumes from.
    human = [e for e in log.read(state.run_id) if e.actor == "human"]
    assert [(e.stage, e.verdict, e.summary) for e in human] == [
        ("gate1", "rejected", "the plan targets the wrong handler"),
    ]
    on_disk = RunState.model_validate_json(gates._state_path(state.run_id).read_text())
    assert on_disk.status == "rejected"
    assert [d.reason for d in on_disk.decisions] == ["the plan targets the wrong handler"]


def test_the_cli_lists_a_paused_run_by_id(monkeypatch, capsys):
    state = RunState(ticket_id="CLEAN-1", ticket_text=TICKET)
    gates.pause(state, "gate2")

    monkeypatch.setattr(sys, "argv", ["agentorg.gates_cli", "list"])
    gates_cli.main()

    listed = capsys.readouterr().out.splitlines()
    assert state.run_id in listed, "a paused run must be findable without its path"


# --------------------------------------------------------------------------
# The third way to stop: nobody approved, and the revision budget ran out.
# --------------------------------------------------------------------------

def test_a_change_the_reviewer_never_approved_is_not_promoted(monkeypatch):
    """Burning the revision budget must not open a PR or claim a promotion.

    The develop<->review loop exits two ways -- on approval, and on the cap --
    and before this task everything after the loop ran regardless. So a run
    whose reviewer objected on every single pass still opened a PR and reported
    status "promoted", carrying a changes_requested verdict as it did.
    """
    _never_approves(monkeypatch)
    state = graph.run_pipeline("CLEAN-1", TICKET)

    assert state.status == "failed"
    assert state.review.verdict == "changes_requested"
    assert state.revision_count == config.MAX_REVISION_LOOPS

    # The scanners DID run and they cleared it -- that is what makes this run
    # stoppable here rather than at the block rule, and the security stage is
    # never skipped on the strength of a reviewer's opinion. See the poisoned
    # counterpart below for the case where the block rule wins instead.
    assert state.security is not None and state.security.verdict == "pass"

    # Everything downstream of the security gate is what the missing approval
    # stops: no SRE assessment, no gate 2 or 3, and above all no promotion.
    assert state.sre is None, "the run must stop before the SRE agent"

    # And "failed" is not "rejected": no human was asked, so none is recorded
    # beyond gate 1, which came before the loop.
    assert _decided(state) == [("gate1", "approved")]


def test_a_never_approved_change_stops_even_when_a_human_approved_gate1(monkeypatch):
    """The reviewer's verdict is terminal on its own terms.

    Gate 1 approves a PLAN. It cannot stand in for an approval of the diff that
    plan produced, so a human "yes" at gate 1 must not carry an unapproved
    change through to promotion.
    """
    _never_approves(monkeypatch)
    asked = _answer_gates(monkeypatch, ["a"])
    state = graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)

    assert state.status == "failed"
    assert _decided(state) == [("gate1", "approved")]
    assert len(asked) == 1, "gates 2 and 3 are downstream of a stop and must not ask"
    assert state.sre is None


def test_the_log_tells_the_two_review_exits_apart(monkeypatch):
    """Reading the log alone must answer: approved, or out of revisions?

    Both exits leave the loop through the same `action="reviewed"` line, and the
    cap exit additionally sits at the end of a run of mid-loop revision lines
    carrying the same actor/stage/action/verdict. Without something that says
    which happened, a reviewed-and-stopped run is indistinguishable in the log
    from a reviewed-and-shipped one.
    """
    approved = graph.run_pipeline("CLEAN-1", TICKET)
    _never_approves(monkeypatch)
    capped = graph.run_pipeline("CLEAN-2", TICKET)

    approved_lines = [e for e in log.read(approved.run_id) if e.stage == "review"]
    capped_lines = [e for e in log.read(capped.run_id) if e.stage == "review"]

    # One review per pass, plus the terminal line the cap exit adds.
    assert [e.verdict for e in approved_lines] == ["approve"]
    assert len(capped_lines) == config.MAX_REVISION_LOOPS + 2
    assert {e.verdict for e in capped_lines} == {"changes_requested"}

    # The two loop exits: same action, different story.
    approved_exit, capped_exit = approved_lines[-1], capped_lines[-2]
    assert approved_exit.action == "reviewed" and capped_exit.action == "reviewed"
    assert "cap" in capped_exit.summary, "the cap exit must say why it exited"
    assert approved_exit.summary != capped_exit.summary

    # And only the run that stopped carries a terminal line saying so.
    assert capped_lines[-1].action == "blocked"
    assert "blocked" not in [e.action for e in approved_lines]


def test_a_rejected_gate_is_recorded_in_the_log(monkeypatch):
    """A human's no is durable, not just an in-memory field on the RunState."""
    _answer_gates(monkeypatch, ["a", "r"])
    state = graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)

    events = log.read(state.run_id)
    assert [(e.actor, e.stage) for e in events if e.action == "rejected"] == [
        ("human", "gate2"),
    ]
    # The approval before it is recorded too, and attributed to the same actor.
    assert [(e.actor, e.stage) for e in events if e.action == "approved"] == [
        ("human", "gate1"),
    ]


def test_a_run_abandoned_at_a_gate_is_left_resumable(monkeypatch):
    """Walking away mid-prompt must leave a state another human can pick up.

    Ctrl-D at the terminal raises EOFError out of input(), which is the literal
    "walked away" gesture and the reason pause() writes before asking rather
    than after. The run never completes, so nothing may invent a terminal status
    for it -- but everything done up to the prompt has to survive, or the async
    CLI has nothing to resume and the two halves of the gate stop being the same
    seam.

    Scope, stated because it is easy to overclaim: this pins that an unfinished
    run keeps "running" and stays resumable. It does NOT pin the finally clause
    in run_pipeline -- pause() wrote these exact bytes just before the prompt,
    so this test passes with or without the terminal save. The finally is pinned
    by test_a_crash_mid_run_still_leaves_the_decisions_already_taken, which
    crashes AFTER a decision instead of at one.
    """
    scripted = iter(["a"])

    def _walk_away(prompt: str = "") -> str:
        try:
            return next(scripted)
        except StopIteration:
            raise EOFError("user pressed ctrl-D") from None

    monkeypatch.setattr(builtins, "input", _walk_away)
    captured = _capture_run(monkeypatch)

    with pytest.raises(EOFError):
        graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)

    assert [d.gate for d in captured[-1].decisions] == ["gate1"], "abandoned at gate 2"
    on_disk = _on_disk(captured[-1])
    assert on_disk.status == "running", "an abandoned run has no ending to record yet"
    assert [(d.gate, d.decision) for d in on_disk.decisions] == [("gate1", "approved")]
    assert on_disk.security is not None, "the work up to gate 2 survived the walk-away"


def test_a_poisoned_diff_blocks_even_when_the_reviewer_never_approves(monkeypatch):
    """The deterministic block rule outranks the reviewer, and must.

    This is the scenario that decides where the review-exhaustion halt goes. A
    competent reviewer SHOULD object to the poisoned diff -- it hardcodes AWS
    credentials -- and developer.run re-inserts that key on every revision, so
    a live poisoned run plausibly exhausts the cap on every pass. Stopping such
    a run at the review would mean the scanners never ran, and the demo's whole
    claim -- "the poisoned ticket blocks every single time" -- would quietly
    become "it fails at review", which is a claim about a model's judgement
    rather than about code.
    """
    _never_approves(monkeypatch)
    state = graph.run_pipeline("POISON-1", TICKET, poisoned=True)

    assert state.status == "blocked", "the block rule wins over an unapproved review"
    assert state.security.verdict == "block"
    assert len(state.security.blocking) == 2
    # ...and the reviewer really did refuse throughout, so this is the collision
    # of the two stops rather than a run that quietly approved itself.
    assert state.review.verdict == "changes_requested"
    assert state.revision_count == config.MAX_REVISION_LOOPS


def test_the_security_stage_runs_for_a_change_no_one_approved(monkeypatch):
    """The scanners are never skipped on the strength of a reviewer's opinion.

    Measured from the call side rather than from the result: open_pr and the
    security stage are both REACHED on an unapproved run, which is what keeps
    the block rule's coverage independent of what the reviewer decided.

    post_comment is recorded too, and it is the sharpest of the three: it is
    reachable ONLY from inside the block branch, so it distinguishes "the
    scanners ran" from "the block rule actually fired". A halt placed between
    security.run and that branch leaves the first two recorders satisfied and
    only this one empty.
    """
    opened: list[str] = []
    scanned: list[str] = []
    commented: list[str] = []
    real_open_pr = github_ops.open_pr
    real_security = security.run
    real_post_comment = github_ops.post_comment
    monkeypatch.setattr(github_ops, "open_pr", lambda state: (
        opened.append(state.ticket_id) or real_open_pr(state)
    ))
    monkeypatch.setattr(security, "run", lambda state: (
        scanned.append(state.ticket_id) or real_security(state)
    ))
    monkeypatch.setattr(github_ops, "post_comment", lambda state, body, finding=None: (
        commented.append(state.ticket_id) or real_post_comment(state, body, finding)
    ))

    # Toggled rather than undone: monkeypatch.undo() would also revert the
    # autouse guards in conftest.py, which share this same monkeypatch object.
    real_review = reviewer.run
    approving = {"now": False}
    monkeypatch.setattr(reviewer, "run", lambda state: (
        real_review(state) if approving["now"] else
        ReviewResult(verdict="changes_requested", must_fix=["still 200 on the 6th"])
    ))

    assert graph.run_pipeline("CLEAN-1", TICKET).status == "failed"
    assert opened == ["CLEAN-1"], "the diff is published for the scanners to read"
    assert scanned == ["CLEAN-1"], "the block rule is evaluated on every diff"
    assert commented == [], "a clean diff has nothing to explain"

    # The same run on the poisoned ticket, still with a reviewer that never
    # approves: the block branch must be entered, not merely approached.
    assert graph.run_pipeline("POISON-1", TICKET, poisoned=True).status == "blocked"
    assert opened == ["CLEAN-1", "POISON-1"]
    assert scanned == ["CLEAN-1", "POISON-1"]
    assert commented == ["POISON-1"], "the block reason is posted from inside the block"

    # The recorders are live rather than vacuously equal: the approving path
    # goes through the same patches and reaches the same stages.
    approving["now"] = True
    assert graph.run_pipeline("CLEAN-2", TICKET).status == "promoted"
    assert opened == ["CLEAN-1", "POISON-1", "CLEAN-2"]
    assert scanned == ["CLEAN-1", "POISON-1", "CLEAN-2"]
    assert commented == ["POISON-1"]


# --------------------------------------------------------------------------
# resume() writes back, so a UI deciding one gate per click does not lose the
# clicks before it.
# --------------------------------------------------------------------------

def test_two_sequential_resumes_both_survive():
    """A human approves gate 1, then gate 2, in two separate calls.

    Before the write-back, resume() reloaded the same untouched file every time
    and each decision silently replaced the one before it: measured as a second
    resume returning gates=['gate2'] alone, with decisions=0 still on disk. The
    log kept the history, but nothing could be RESUMED from it.
    """
    state = RunState(ticket_id="CLEAN-1", ticket_text=TICKET)
    path = gates.pause(state, "gate1")

    first = gates.resume(state.run_id, HumanDecision(
        gate="gate1", decision="approved", by="sorour", reason="plan is right"))
    second = gates.resume(state.run_id, HumanDecision(
        gate="gate2", decision="approved", by="mariam", reason="scanners clean"))

    expected = [("gate1", "approved", "sorour"), ("gate2", "approved", "mariam")]
    assert [(d.gate, d.decision, d.by) for d in first.decisions] == expected[:1]
    assert [(d.gate, d.decision, d.by) for d in second.decisions] == expected
    on_disk = RunState.model_validate_json(path.read_text())
    assert [(d.gate, d.decision, d.by) for d in on_disk.decisions] == expected


def test_a_resumed_rejection_persists_to_disk():
    """The status a rejection sets must outlive the process that set it."""
    state = RunState(ticket_id="CLEAN-1", ticket_text=TICKET)
    path = gates.pause(state, "gate1")
    assert RunState.model_validate_json(path.read_text()).status == "running"

    gates.resume(state.run_id, HumanDecision(
        gate="gate1", decision="rejected", by="sorour", reason="wrong plan"))

    on_disk = RunState.model_validate_json(path.read_text())
    assert on_disk.status == "rejected"
    assert [(d.decision, d.reason) for d in on_disk.decisions] == [
        ("rejected", "wrong plan"),
    ]


def test_resume_preserves_the_work_it_did_not_touch():
    """Writing the state back must not cost anything already in it.

    A round trip through resume() is a full deserialize/serialize of the run,
    so it is also the place a field silently stops surviving. Asserted against
    a real pipeline state rather than a hand-built one, so every populated
    field of the contract is in play.
    """
    original = graph.run_pipeline("CLEAN-1", TICKET)
    gates.pause(original, "gate3")

    resumed = gates.resume(original.run_id, HumanDecision(
        gate="gate3", decision="overridden", by="sorour", reason="accepted the risk"))

    assert resumed.plan == original.plan
    assert resumed.dev == original.dev
    assert resumed.review == original.review
    assert resumed.security == original.security
    assert resumed.sre == original.sre
    assert resumed.decisions[:-1] == original.decisions
    assert _on_disk(original) == resumed


# --------------------------------------------------------------------------
# The graph writes the ENDING, not only the pauses. Without this the file was
# a record of the last question asked rather than of how the run turned out.
# --------------------------------------------------------------------------

def test_every_completed_run_writes_its_ending_to_disk(monkeypatch):
    """On-disk state must match the returned state for every terminal outcome.

    gates.pause always runs BEFORE the decision it is pausing for, so until the
    graph wrote its own ending every finished run -- promoted, blocked and
    rejected alike -- was left on disk reading status="running" with its last
    decision missing. The file is what the CLI and the week-3 UI read, so that
    made the authoritative record disagree with the run that produced it.
    """
    promoted = graph.run_pipeline("CLEAN-1", TICKET)
    blocked = graph.run_pipeline("POISON-1", TICKET, poisoned=True)

    _answer_gates(monkeypatch, ["a", "r"])
    rejected = graph.run_pipeline("CLEAN-2", TICKET, auto_approve=False)

    _never_approves(monkeypatch)
    failed = graph.run_pipeline("CLEAN-3", TICKET)

    # Distinct outcomes, so the loop below is comparing four different endings
    # rather than four copies of one.
    assert [s.status for s in (promoted, blocked, rejected, failed)] == [
        "promoted", "blocked", "rejected", "failed",
    ]
    assert [len(s.decisions) for s in (promoted, blocked, rejected, failed)] == [3, 1, 2, 1]

    for state in (promoted, blocked, rejected, failed):
        on_disk = _on_disk(state)
        assert on_disk.status == state.status, f"{state.ticket_id}: status not persisted"
        assert len(on_disk.decisions) == len(state.decisions), (
            f"{state.ticket_id}: decision count not persisted"
        )
        assert on_disk == state, f"{state.ticket_id}: on-disk run differs from the real one"


def test_a_crash_mid_run_still_leaves_the_decisions_already_taken(monkeypatch):
    """The `finally` itself, pinned — not merely the saves on the return paths.

    This is deliberately NOT the abandonment test. That one raises at a gate
    prompt, where the pause() write immediately above it happens to leave the
    same bytes a terminal save would, so it passes just as happily against a
    save-on-normal-return-only wrapper and pins nothing about the finally.

    The discriminating case needs a crash AFTER a gate decision, because pause()
    always writes BEFORE the decision it is pausing for. So: approve gate 2,
    then have the next stage die. With the finally, both decisions are on disk.
    Without it, the newest write on disk is gate 2's pause — taken before gate 2
    was answered — and the gate-2 approval is simply gone, in the run someone is
    most likely to come back and inspect.
    """
    monkeypatch.setattr(sre, "run", _explode)
    captured = _capture_run(monkeypatch)

    with pytest.raises(RuntimeError, match="exploded"):
        graph.run_pipeline("CLEAN-1", TICKET)

    on_disk = _on_disk(captured[-1])
    assert [(d.gate, d.decision) for d in on_disk.decisions] == [
        ("gate1", "approved"), ("gate2", "approved"),
    ], "a crash must not lose a decision that was already taken"
    # Still "running": the run did not end, it stopped. Nothing may promote,
    # reject or fail a run on its behalf just because it raised.
    assert on_disk.status == "running"
    assert on_disk.security is not None, "the work up to the crash survived too"


def test_a_rejected_run_cannot_be_talked_out_of_its_rejection(monkeypatch):
    """The sharp end of persisting the ending.

    A run the graph rejected at gate 2 used to be left on disk as
    running/['gate1'], so `gates_cli resume <id> --gate gate2 --decision
    approved` succeeded and produced running/[gate1 approved, gate2 approved] --
    a completed, rejected run talked out of its own rejection by someone who
    came along afterwards.
    """
    _answer_gates(monkeypatch, ["a", "r"])
    state = graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)
    assert state.status == "rejected"
    assert _on_disk(state).status == "rejected", "the rejection is on disk to begin with"

    resumed = gates.resume(state.run_id, HumanDecision(
        gate="gate2", decision="approved", by="someone-else", reason="on reflection"))

    assert resumed.status == "rejected", "a later approval cannot undo the rejection"
    assert _on_disk(state).status == "rejected"
    # The attempt is not hidden -- it is appended, like every other decision.
    assert [(d.gate, d.decision) for d in resumed.decisions] == [
        ("gate1", "approved"), ("gate2", "rejected"), ("gate2", "approved"),
    ]


# --------------------------------------------------------------------------
# The approval token itself.
# --------------------------------------------------------------------------

def test_only_an_explicit_approval_approves(monkeypatch):
    """Typing "abort" at a gate must not approve it.

    The gate fails closed on anything it does not recognise, which is right. But
    a PREFIX match on "a" made the most natural way to bail out of a prompt you
    did not mean to be at -- "abort" -- the same keystroke as consent, on the
    three prompts in this system where being misread costs the most.
    """
    state = RunState(ticket_id="CLEAN-1", ticket_text=TICKET)
    cases = {
        "a": "approved", "approve": "approved", "approved": "approved",
        "y": "approved", "yes": "approved", "  A  ": "approved", "YES": "approved",
        "abort": "rejected", "actually no": "rejected", "away": "rejected",
        "r": "rejected", "reject": "rejected", "no": "rejected", "n": "rejected",
        "": "rejected", "   ": "rejected", "stop": "rejected", "quit": "rejected",
    }
    for answer, expected in cases.items():
        monkeypatch.setattr(builtins, "input", lambda prompt="", a=answer: a)
        assert graph._cli_gate(state, "gate1").decision == expected, (
            f"answering {answer!r} at a gate must be {expected}"
        )

    # The two halves of the guarantee, stated rather than implied by the table.
    assert "abort" not in graph.APPROVAL_WORDS
    assert "" not in graph.APPROVAL_WORDS, "bare Enter must fail closed"
