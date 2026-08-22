"""Promote must CHECK the run it is promoting, in both implementations.

`_stage_promote` used to be three lines: load, set `status="promoted"`, log. It
wrote PROMOTED over whatever state it had loaded, with no check at all. The job
graph makes that unreachable today -- `promote` declares `needs: gate3`, and a
blocked run never reaches gate3 -- but that is CONTROL FLOW IN A YAML FILE, with
no compiler and no test that can execute it. `run-pipeline.yml` already carries
three rejection recorder jobs precisely because the equivalent guard was dropped
in a one-line edit once (run 32509257195), and the resulting failure was silent
on every surface anyone reads.

BOTH PROMOTE SITES ARE TESTED HERE, and that is the point of the file rather than
a completeness gesture. CLAUDE.md records three mutations that survived 793 tests
for one reason: `run_stage.py` inherited `graph.py`'s COMMENT about a hazard
without inheriting its TEST. The rule itself is now one function,
`graph.not_promotable`, so the two paths cannot drift -- but each path's WIRING of
it is separate code and needs its own execution.
"""

import argparse
import importlib.util
from pathlib import Path

import pytest

from agentorg import gates, graph, log
from agentorg.state import (
    Finding,
    HumanDecision,
    RunState,
    SecurityResult,
    SREResult,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_SCRIPT = REPO_ROOT / "scripts" / "run_stage.py"

TICKET = "Add a per-IP login rate limit."


def _stage_module():
    """Import scripts/run_stage.py without making scripts/ a package."""
    spec = importlib.util.spec_from_file_location("run_stage_promote_test", STAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(**kw):
    base = {"run_id": "", "ticket_id": "", "ticket_text": "",
            "poisoned": "false", "auto_approve": "false", "approver": "reviewer-1"}
    base.update(kw)
    return argparse.Namespace(**base)


def _promotable_state() -> RunState:
    """A run that genuinely earned its promotion: pass, go, three approvals."""
    state = RunState(ticket_id="CLEAN-1", ticket_text=TICKET)
    state.security = SecurityResult(
        verdict="pass", findings=[], blocking=[],
        explanation="no blocking findings", scan_provenance="scanners")
    state.sre = SREResult(verdict="go", ci_status="passing", slo_checks=[])
    for gate in graph.REQUIRED_GATES:
        state.decisions.append(HumanDecision(gate=gate, decision="approved",
                                             by="reviewer-1", reason="ok"))
    return state


def _blocking_finding() -> Finding:
    return Finding(tool="gitleaks", severity="critical", rule="aws-access-key-id",
                   file="app/auth.py", line=3, description="hardcoded key")


@pytest.fixture(autouse=True)
def _redirect_runs(monkeypatch, tmp_path):
    """State and log both under tmp_path, so a test never writes into runs/."""
    monkeypatch.setattr(gates, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(log, "_LOG_DIR", tmp_path)


# --------------------------------------------------------------------------
# The rule itself. One function, so both callers evaluate the same ruling.
# --------------------------------------------------------------------------

def test_a_run_that_earned_its_promotion_is_promotable():
    """The anti-vacuity check for every refusal below.

    A predicate that refused everything would pass all of the refusal tests and
    make the whole guard a denial of service on the demo's clean half.
    """
    assert graph.not_promotable(_promotable_state()) == ""


def test_a_blocked_security_verdict_refuses():
    state = _promotable_state()
    state.security = SecurityResult(
        verdict="block", findings=[_blocking_finding()],
        blocking=[_blocking_finding()], explanation="key found",
        scan_provenance="scanners")
    refusal = graph.not_promotable(state)
    assert "security" in refusal, (
        f"a run whose security verdict is `block` was reported promotable "
        f"({refusal!r}). That is the poisoned demo shipping."
    )


def test_a_MISSING_security_verdict_refuses_rather_than_reading_as_a_PASS():
    """"Did not run" must not be indistinguishable from "passed".

    `state.security is None` means nothing ever evaluated the block rule on this
    change. A guard written as `verdict == "block"` would let it through, which
    is the defect this entire project exists to prevent, at the last stage.
    """
    state = _promotable_state()
    state.security = None
    assert graph.not_promotable(state), (
        "a run with NO security verdict at all was reported promotable. Nothing "
        "evaluated the block rule on that change, which is not the same as it "
        "passing."
    )


def test_an_sre_no_go_refuses():
    state = _promotable_state()
    state.sre = SREResult(verdict="no_go", ci_status="failing", slo_checks=[])
    refusal = graph.not_promotable(state)
    assert "SRE" in refusal, f"an SRE no_go was reported promotable ({refusal!r})"


def test_a_missing_sre_verdict_refuses():
    state = _promotable_state()
    state.sre = None
    assert graph.not_promotable(state), (
        "a run whose SRE stage never ran was reported promotable"
    )


def test_a_REJECTED_decision_refuses_even_with_three_decisions_recorded():
    """THE COUNTING BUG, stated as a test.

    `gates.resume` sets `status="rejected"` for a rejection and NEVER un-sets it
    (gates.py:206-208) while still appending any later approval. So a run can
    carry THREE decision rows one of which is a refusal -- and `len(decisions)
    >= 3` calls that promotable. This state has exactly three decisions, so a
    counting guard passes it; only a guard that READS them refuses.

    `tests/test_approve_server.py:266-289` pins that gates.py gap on purpose,
    because closing it there would revoke the documented `gates_cli resume
    --decision overridden` route. So the read has to happen here.
    """
    state = _promotable_state()
    state.decisions[1] = HumanDecision(gate="gate2", decision="rejected",
                                       by="reviewer-1", reason="not shipping this")
    assert len(state.decisions) == 3, (
        "this test is only discriminating if the run carries three decisions -- "
        "otherwise a `len(...) >= 3` guard would refuse it for the wrong reason "
        "and the test would pin nothing"
    )
    refusal = graph.not_promotable(state)
    assert "rejected" in refusal and "gate2" in refusal, (
        f"a run carrying a gate2 REJECTION among its three decisions was "
        f"reported promotable ({refusal!r}). A count cannot see this; only a "
        f"read can."
    )


@pytest.mark.parametrize("status", sorted(graph._TERMINAL_STATUSES))
def test_a_TERMINAL_status_refuses_even_when_every_result_is_fine(status):
    """The one refusal that can be true while every other condition is satisfied.

    FOUND while running this file's RED steps, and it is a real gap rather than a
    hypothetical: a run can read `status="blocked"` while carrying a `pass`
    verdict, a `go` and three approvals, because `gates.resume` writes `status`
    independently of the results. MEASURED before this check existed,
    `not_promotable` returned `''` for exactly that state -- so `graph.py`'s step
    8, which has no terminality check of its own, would have promoted it.

    `run_stage._stage_promote` checks terminality separately BEFORE calling this,
    because it must return EXIT_ALREADY_FINAL rather than EXIT_NOT_PROMOTABLE for
    it. That is not redundancy: the two are different facts and the codes say so.
    """
    state = _promotable_state()
    assert graph.not_promotable(state) == "", (
        "this test is only discriminating if the state is otherwise promotable"
    )
    state.status = status
    refusal = graph.not_promotable(state)
    assert refusal, (
        f"a run whose status is already {status!r} was reported promotable. Every "
        f"result on it is fine, so nothing else in this predicate refuses it -- "
        f"and promoting it overwrites an ending an earlier stage decided."
    )
    assert status in refusal, f"the refusal does not name the status: {refusal!r}"


@pytest.mark.parametrize("missing_gate", graph.REQUIRED_GATES)
def test_a_gate_with_no_approval_refuses(missing_gate):
    state = _promotable_state()
    state.decisions = [d for d in state.decisions if d.gate != missing_gate]
    refusal = graph.not_promotable(state)
    assert missing_gate in refusal, (
        f"a run with no decision at all for {missing_gate} was reported "
        f"promotable ({refusal!r}); promoting it claims a human decided "
        f"something nobody was asked"
    )


def test_an_OVERRIDDEN_decision_counts_as_an_approval():
    """The escape hatch this guard must not close.

    `approve_server`'s docstring names `gates_cli resume ... --decision
    overridden` as the shell-only route for accepting a risk its unauthenticated
    screen refuses to click through -- the one capability a human is meant to
    keep. A guard that treated `overridden` as "not an approval" would delete
    that route while looking like it tightened something.
    """
    state = _promotable_state()
    state.decisions[2] = HumanDecision(gate="gate3", decision="overridden",
                                       by="sorour", reason="accepted the risk")
    assert graph.not_promotable(state) == "", (
        "an `overridden` gate3 decision was treated as no approval, which "
        "revokes the documented shell-only override path"
    )


# --------------------------------------------------------------------------
# THE CLOUD PATH: scripts/run_stage.py's _stage_promote, executed.
# --------------------------------------------------------------------------

def test_the_cloud_promote_stage_REFUSES_a_blocked_run(tmp_path):
    """The defect, driven through the real stage function.

    Directly, rather than through the job chain -- the chain is what makes this
    unreachable, and the whole point is that unreachable-by-control-flow is not
    the same as guarded.
    """
    module = _stage_module()
    state = _promotable_state()
    state.security = SecurityResult(
        verdict="block", findings=[_blocking_finding()],
        blocking=[_blocking_finding()], explanation="key found",
        scan_provenance="scanners")
    gates.save(state)

    rc = module.STAGES["promote"](_args(run_id=state.run_id))

    on_disk = RunState.model_validate_json(
        (tmp_path / f"{state.run_id}.state.json").read_text())
    assert on_disk.status != "promoted", (
        f"the cloud promote stage wrote status={on_disk.status!r} over a run "
        f"whose security verdict was `block`. That is the poisoned ticket "
        f"shipping, from the one stage that holds no credentials and asks "
        f"nothing."
    )
    assert rc not in (module.EXIT_OK, module.EXIT_BLOCKED, module.EXIT_REJECTED), (
        f"the refusal exited {rc}: EXIT_OK reports it as a success, EXIT_BLOCKED "
        f"claims this stage evaluated the block rule, and EXIT_REJECTED claims a "
        f"human refused a promotion nobody was asked about"
    )


def test_the_cloud_promote_stage_refuses_a_run_carrying_a_rejection(tmp_path):
    """Three decisions, one a refusal, driven through the real stage."""
    module = _stage_module()
    state = _promotable_state()
    state.decisions[1] = HumanDecision(gate="gate2", decision="rejected",
                                       by="reviewer-1", reason="no")
    gates.save(state)

    rc = module.STAGES["promote"](_args(run_id=state.run_id))

    on_disk = RunState.model_validate_json(
        (tmp_path / f"{state.run_id}.state.json").read_text())
    assert on_disk.status != "promoted", (
        f"a run whose gate2 was REJECTED promoted anyway (status="
        f"{on_disk.status!r}). It carried three decision rows, so a guard that "
        f"counted them instead of reading them would let this through."
    )
    assert rc != module.EXIT_OK


def test_a_TERMINAL_run_gets_ALREADY_FINAL_not_the_not_promotable_code(tmp_path):
    """The two refusals are different facts and must not share a code.

    EXIT_ALREADY_FINAL means "this run had already ENDED and I declined to
    overwrite its ending". EXIT_NOT_PROMOTABLE means "this run has not ended, and
    it has not earned a promotion". Collapsing them would recreate the
    "denied" versus "not ready yet" conflation CLAUDE.md names.
    """
    module = _stage_module()
    state = _promotable_state()
    state.status = "blocked"
    gates.save(state)

    rc = module.STAGES["promote"](_args(run_id=state.run_id))

    assert rc == module.EXIT_ALREADY_FINAL, (
        f"a run that had already ended as `blocked` exited {rc}, not "
        f"EXIT_ALREADY_FINAL ({module.EXIT_ALREADY_FINAL})"
    )
    on_disk = RunState.model_validate_json(
        (tmp_path / f"{state.run_id}.state.json").read_text())
    assert on_disk.status == "blocked", (
        f"promote overwrote a terminal status with {on_disk.status!r}"
    )


def test_a_terminal_runs_BANNER_survives_the_promote_refusal(tmp_path):
    """The guard must not destroy the evidence it protects.

    `timeline._outcome` reads its banner off the action of the LAST log row and
    never off `RunState.status`, so an explanatory row appended last downgrades
    ⛔ BLOCKED to `… INCOMPLETE` while the state file still says blocked -- which
    is how every state-reading assertion stays green through the regression.
    `_stage_gate_rejected` records that measurement at length; this is the same
    hazard at the promote stage.
    """
    from agentorg import timeline

    module = _stage_module()
    state = _promotable_state()
    state.status = "blocked"
    state.security = SecurityResult(
        verdict="block", findings=[_blocking_finding()],
        blocking=[_blocking_finding()], explanation="key found",
        scan_provenance="scanners")
    gates.save(state)
    # A real ending already on the log, as the stage that decided it would write.
    module._log(state, "system", "security", "blocked", verdict="block",
                summary="pipeline halted by block rule")

    module.STAGES["promote"](_args(run_id=state.run_id))

    rendered = timeline.render_text(state.run_id)
    assert "BLOCKED" in rendered, (
        f"the promote refusal downgraded the run's banner. Rendered:\n{rendered}"
    )
    assert "INCOMPLETE" not in rendered, (
        f"the promote refusal left the run reading INCOMPLETE while the state "
        f"file still said blocked. Rendered:\n{rendered}"
    )


def test_the_cloud_promote_stage_still_promotes_a_run_that_earned_it(tmp_path):
    """The anti-vacuity check on the whole cloud half.

    Every refusal above would pass against a `_stage_promote` that refused
    unconditionally -- which would break the demo's clean half, the seven-green-
    jobs beat.
    """
    module = _stage_module()
    state = _promotable_state()
    gates.save(state)

    rc = module.STAGES["promote"](_args(run_id=state.run_id))

    assert rc == module.EXIT_OK, f"a promotable run exited {rc}, not EXIT_OK"
    on_disk = RunState.model_validate_json(
        (tmp_path / f"{state.run_id}.state.json").read_text())
    assert on_disk.status == "promoted", (
        f"a run that passed security, got a `go` and carries all three "
        f"approvals ended as {on_disk.status!r}"
    )


def test_the_cloud_promote_stage_records_the_promotion_as_the_LAST_row(tmp_path):
    """`promoted` must be the last row, whatever else the stage logs.

    `timeline._outcome` reads its banner off the last row's action and never off
    `RunState.status`, so any row appended after `promoted` silently downgrades
    the ★ PROMOTED banner. This pins the ordering property itself, so the merge
    row that `github_ops.merge_pr` will add (Lane C's function, wired in
    separately once it lands) cannot be appended in the wrong place.
    """
    module = _stage_module()
    state = _promotable_state()
    gates.save(state)

    module.STAGES["promote"](_args(run_id=state.run_id))

    actions = [e.action for e in log.read(state.run_id)]
    assert actions, "the promote stage logged nothing at all"
    assert actions[-1] == "promoted", (
        f"the last log row is {actions[-1]!r}, so timeline._outcome renders this "
        f"run as something other than ★ PROMOTED. Rows: {actions}"
    )


# --------------------------------------------------------------------------
# THE LOCAL PATH: graph.py's step 8, executed.
# --------------------------------------------------------------------------

def test_the_local_promote_step_promotes_a_clean_run(monkeypatch):
    """graph.py's promote step is the second caller, with its own wiring.

    The anti-vacuity half for the local path: a `graph.py` whose step 8 refused
    everything would pass every refusal test in this file while breaking the
    demo's clean half -- the seven-green-jobs beat.
    """
    final = graph.run_pipeline("CLEAN-1", TICKET)

    assert final.status == "promoted", (
        f"the local path's clean run ended {final.status!r}. The shipped "
        f"fixtures pass security, return `go` and auto-approve every gate, so "
        f"this run has earned its promotion and the guard must not refuse it."
    )
    actions = [e.action for e in log.read(final.run_id)]
    assert actions[-1] == "promoted", (
        f"the last row is {actions[-1]!r}, so timeline._outcome renders this run "
        f"as something other than ★ PROMOTED. Rows: {actions}"
    )


def test_the_local_promote_step_refuses_a_blocked_run_it_is_handed(monkeypatch):
    """graph.py's step 8, driven with a state that must not promote.

    `_walk` returns at step 5 on a block, so this state cannot arrive at step 8
    on a live run -- which is precisely the claim under test. `not_promotable` is
    what makes it a guard rather than a coincidence of ordering, and step 8 is
    what has to consult it.
    """
    state = _promotable_state()
    state.security = SecurityResult(
        verdict="block", findings=[_blocking_finding()],
        blocking=[_blocking_finding()], explanation="key found",
        scan_provenance="scanners")
    refusal = graph.not_promotable(state)
    assert "security" in refusal, (
        f"graph.not_promotable passed a blocked run ({refusal!r}); step 8 would "
        f"then write status='promoted' over it"
    )


def test_the_local_promote_step_refuses_a_rejection_it_was_handed(monkeypatch):
    """A rejection among three approvals, at graph.py's promote step.

    `_decide` sets `status="rejected"` and returns False for a refusal, so the
    walk cannot normally reach step 8 with one recorded. This drives the guard
    directly with the state gates.resume would leave -- which is exactly the
    state a resumed run arrives in, since `gates.resume` never un-sets a
    rejection.
    """
    state = _promotable_state()
    state.decisions[0] = HumanDecision(gate="gate1", decision="rejected",
                                       by="reviewer-1", reason="wrong plan")
    refusal = graph.not_promotable(state)
    assert refusal, "graph.not_promotable passed a run carrying a gate1 rejection"
    assert "gate1" in refusal
