"""Human gate behaviour: interactive halt, async resume, and an unapproved stop.

Owner: Sorour.

Three things are pinned here, and they are the three ways a run can stop without
being promoted:

  * a human says no at gate 1, 2 or 3 -> status "rejected";
  * a human says no later, out of band, through gates_cli -> status "rejected";
  * the REVIEWER never said yes and the revision budget ran out -> status
    "failed". No human is involved in that one, which is why it is not
    "rejected": in this contract "rejected" denotes a human decision.

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
from agentorg.agents import reviewer
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
    # The decision the CLI took must reach the append-only log, which is the
    # only durable record of it -- resume() hands the RunState back to its
    # caller and this process is about to exit.
    human = [e for e in log.read(state.run_id) if e.actor == "human"]
    assert [(e.stage, e.verdict, e.summary) for e in human] == [
        ("gate1", "rejected", "the plan targets the wrong handler"),
    ]


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

    # No PR for a change nobody approved. The witness is dev.branch: open_pr
    # rewrites it to "agent-org/<ticket>-<sha>" before it does anything else,
    # so the developer's own branch name surviving means open_pr never ran.
    # dev.pr_url is NOT a witness -- fixtures/dev_result_clean.json ships with a
    # real pull-request URL already in it, so `pr_url is None` fails on a
    # correct implementation and `is not None` passes on a broken one.
    assert state.dev is not None, "the developer did produce a diff"
    assert not state.dev.branch.startswith("agent-org/"), \
        "no PR may be opened for a change the reviewer never approved"
    assert state.security is None, "the run must stop at the review, not at the scanners"
    assert state.sre is None

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
    assert not state.dev.branch.startswith("agent-org/"), "open_pr must not run"


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


def test_the_gate_writes_a_state_file_a_human_can_pick_up(monkeypatch):
    """Every gate pauses to disk BEFORE asking, so a walk-away is resumable.

    This is the whole reason pause() and the CLI are the same seam: whoever
    abandons the terminal at gate 2 leaves behind a state another human can
    decide on later with `gates_cli resume`.
    """
    _answer_gates(monkeypatch, ["a", "r"])
    state = graph.run_pipeline("CLEAN-1", TICKET, auto_approve=False)

    saved = RunState.model_validate_json(
        gates._state_path(state.run_id).read_text()
    )
    assert saved.run_id == state.run_id
    assert saved.security is not None, "gate 2 saved what the human was looking at"
    assert [d.gate for d in saved.decisions] == ["gate1"], (
        "the file is written before the pending decision, never after it"
    )


def test_open_pr_is_never_reached_for_an_unapproved_change(monkeypatch):
    """Belt and braces on the pr_url assertion above, from the other side.

    pr_url being None proves open_pr did not SET it; this proves open_pr was
    not CALLED, which is the property that matters once Task 9 makes the
    offline branch do real local git work.
    """
    calls: list[str] = []
    real_open_pr = github_ops.open_pr
    monkeypatch.setattr(github_ops, "open_pr", lambda state: (
        calls.append(state.ticket_id) or real_open_pr(state)
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
    assert calls == []

    # The recorder is live rather than vacuously empty: with the same patch in
    # place, an approving reviewer still carries the run through open_pr.
    approving["now"] = True
    assert graph.run_pipeline("CLEAN-2", TICKET).status == "promoted"
    assert calls == ["CLEAN-2"]
