"""The two ways a run ENDS without shipping and without being blocked.

`state.py` and `timeline.py` already carry the `failed` action, its `✗` glyph and
its `FAILED — the change did not ship` banner (commit b32ea5c). This file is
about the rows: which action the two failing endings actually WRITE.

TWO DEFECTS, both of which render the wrong claim on the surface a judge reads.

1. `run_stage._OUTCOME_ACTIONS["failed"]` mapped to `"blocked"`, so a
   revision-cap run rendered `⛔ BLOCKED — the change was stopped` while its
   security verdict was `pass` with 0 blocking findings. That is the pipeline's
   CENTRAL CLAIM -- the deterministic rule stopped this change -- asserted about
   a change the scanners cleared.

2. The SRE `no_go` exit wrote NO log row at all, in either implementation, so
   the run rendered `… INCOMPLETE — run stopped at sre without an ending`. NO
   TEST COVERED THE no_go PATH, which is why it survived.

EVERY ASSERTION HERE IS ON THE RENDERED BANNER, not on `RunState.status`.
`timeline._outcome` reads the action of the LAST log row and never sees
`RunState.status` -- so a test asserting `status == "failed"` passes against both
the bug and the fix, which is precisely how defect 1 stayed green.
"""

import argparse
import importlib.util
from pathlib import Path

import pytest

from agentorg import gates, graph, log, timeline
from agentorg.agents import sre as sre_agent
from agentorg.state import HumanDecision, RunState, SREResult

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_SCRIPT = REPO_ROOT / "scripts" / "run_stage.py"

TICKET = "Add a per-IP login rate limit."

# The banner words `timeline._OUTCOME` renders, read from that table rather than
# restated. A test that hardcoded "FAILED" would keep passing if the table's
# wording changed, while asserting about a banner nobody renders any more.
_FAILED_BANNER = timeline._OUTCOME["failed"][0]
_BLOCKED_BANNER = timeline._OUTCOME["blocked"][0]


def _stage_module():
    """Import scripts/run_stage.py without making scripts/ a package."""
    spec = importlib.util.spec_from_file_location("run_stage_failed_test", STAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(**kw):
    base = {"run_id": "", "ticket_id": "", "ticket_text": "",
            "poisoned": "false", "auto_approve": "false", "approver": "reviewer-1"}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture(autouse=True)
def _redirect_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(gates, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(log, "_LOG_DIR", tmp_path)


def _no_go(state):
    """An SRE agent that refuses. The shipped one CANNOT: it ignores its state
    and always returns fixtures/sre_result.json, which is `verdict: go`.

    That is exactly why no test covered this path -- the only way to reach it is
    to replace the agent, and nothing did.
    """
    return SREResult(verdict="no_go", ci_status="failing", slo_checks=[],
                     notes="error budget exhausted")


# --------------------------------------------------------------------------
# DEFECT 1: the revision-cap ending claimed the block rule stopped the change.
# --------------------------------------------------------------------------

def test_the_outcome_action_table_does_not_call_a_failed_run_BLOCKED():
    """The table itself, since it is the one line the defect lived on.

    `_OUTCOME_ACTIONS` exists so a recorder can re-state a run's real ending as
    the last row. Mapping `failed` to `"blocked"` made that re-statement a lie
    for every failed run -- and `timeline._MARK` and `_OUTCOME` both carry a
    `failed` entry now, so there is no longer any reason to borrow another
    ending's word.
    """
    module = _stage_module()
    assert module._OUTCOME_ACTIONS["failed"] == "failed", (
        f"_OUTCOME_ACTIONS['failed'] is "
        f"{module._OUTCOME_ACTIONS['failed']!r}, so a recorder re-stating a "
        f"FAILED run's ending writes the word BLOCKED -- the pipeline's central "
        f"claim, asserted about a change the scanners cleared."
    )


def test_every_outcome_action_is_a_real_timeline_ENDING():
    """The table's values must all be actions `timeline._OUTCOME` recognises.

    A value outside that table renders as `… INCOMPLETE` -- which is how the
    original defect was ALMOST caught: `blocked` IS in `_OUTCOME`, so the row
    rendered a banner, just the wrong one. A value that rendered nothing would
    have been noticed; a value that rendered the wrong thing was not.
    """
    module = _stage_module()
    unknown = {status: action for status, action in module._OUTCOME_ACTIONS.items()
               if action not in timeline._OUTCOME}
    assert not unknown, (
        f"these terminal statuses map to actions timeline._OUTCOME does not "
        f"know, so re-stating them renders `… INCOMPLETE`: {unknown}"
    )


def test_each_terminal_status_maps_to_the_action_of_the_SAME_name():
    """No ending may borrow another ending's word.

    Stated as an identity rather than four separate equalities, so a fifth status
    added to the contract is covered the moment `_OUTCOME_ACTIONS` gains it --
    and `_TERMINAL_STATUSES` is derived from the contract, so the two are checked
    against each other rather than against a copy.
    """
    module = _stage_module()
    assert set(module._OUTCOME_ACTIONS) == set(module._TERMINAL_STATUSES), (
        f"_OUTCOME_ACTIONS covers {sorted(module._OUTCOME_ACTIONS)} but the "
        f"contract's terminal statuses are {sorted(module._TERMINAL_STATUSES)}"
    )
    borrowed = {status: action for status, action in module._OUTCOME_ACTIONS.items()
                if status != action}
    assert not borrowed, (
        f"these statuses are re-stated using another ending's action: {borrowed}"
    )


def test_a_revision_cap_run_does_not_render_as_BLOCKED_in_the_cloud(monkeypatch,
                                                                   tmp_path):
    """The measured consequence, on the surface it appears on.

    A capped run's security verdict is `pass` with 0 blocking findings -- the
    ordering in `_stage_develop` guarantees the scanners ran and cleared the diff
    before this exit is reached. Rendering `⛔ BLOCKED` over that is the
    pipeline's central claim made about a change nothing blocked.
    """
    from test_agent_comments import _developer_per_pass, _never_approves

    from agentorg.agents import developer, reviewer

    module = _stage_module()
    monkeypatch.setattr(module.github_ops, "post_comment",
                        lambda state, body, finding=None: "local://x")
    monkeypatch.setattr(reviewer, "run", _never_approves)
    monkeypatch.setattr(developer, "run", _developer_per_pass)

    rc = module.STAGES["plan"](_args(ticket_id="CAP-1", ticket_text=TICKET))
    assert rc == module.EXIT_OK
    run_id = next(p.stem.removesuffix(".state") for p in tmp_path.glob("*.state.json"))
    module.STAGES["gate1"](_args(run_id=run_id))
    module.STAGES["develop"](_args(run_id=run_id))

    state = RunState.model_validate_json((tmp_path / f"{run_id}.state.json").read_text())
    assert state.status == "failed", (
        f"this test needs the revision-cap exit, which ends `failed`; the run "
        f"ended {state.status!r} instead, so it is pinning nothing"
    )
    assert state.security is not None and state.security.verdict == "pass", (
        "the capped run's scanners must have CLEARED the diff -- otherwise "
        "`BLOCKED` would be the honest banner and this test proves nothing"
    )
    assert not state.security.blocking

    rendered = timeline.render_text(run_id)
    assert _BLOCKED_BANNER not in rendered, (
        f"a run the scanners CLEARED (verdict=pass, 0 blocking) renders as "
        f"{_BLOCKED_BANNER}. That is the pipeline's central claim asserted about "
        f"a change nothing blocked.\n{rendered}"
    )
    assert _FAILED_BANNER in rendered, (
        f"the capped run does not render as {_FAILED_BANNER}:\n{rendered}"
    )


def test_a_revision_cap_run_does_not_render_as_BLOCKED_locally(monkeypatch):
    """The same claim on graph.py's path. Both, always -- see this file's header
    and the three mutations CLAUDE.md records surviving in the cloud path alone.
    """
    from test_agent_comments import _developer_per_pass, _never_approves

    from agentorg.agents import developer, reviewer

    monkeypatch.setattr(reviewer, "run", _never_approves)
    monkeypatch.setattr(developer, "run", _developer_per_pass)

    final = graph.run_pipeline("CAP-1", TICKET)
    assert final.status == "failed", (
        f"the local run ended {final.status!r}, not `failed`; this test needs "
        f"the revision-cap exit and is otherwise pinning nothing"
    )
    assert final.security is not None and final.security.verdict == "pass"

    rendered = timeline.render_text(final.run_id)
    assert _BLOCKED_BANNER not in rendered, (
        f"graph.py renders a scanner-cleared capped run as {_BLOCKED_BANNER}:"
        f"\n{rendered}"
    )
    assert _FAILED_BANNER in rendered, (
        f"graph.py's capped run does not render as {_FAILED_BANNER}:\n{rendered}"
    )


# --------------------------------------------------------------------------
# DEFECT 2: the SRE no_go path wrote no row, so the run had no ending at all.
#
# NO TEST COVERED THIS PATH before now. `sre.py` ignores its state and always
# returns fixtures/sre_result.json (`verdict: go`), so `no_go` is unreachable
# without replacing the agent -- and nothing did.
# --------------------------------------------------------------------------

def test_an_sre_no_go_run_has_an_ENDING_in_the_cloud(monkeypatch, tmp_path):
    """`… INCOMPLETE` and "the SRE refused this change" are different claims.

    INCOMPLETE says the run stopped without deciding -- a crash, an abandoned
    gate. A no_go is a DECISION, made by a stage that ran to completion. The
    renderer cannot tell them apart from the log, because the no_go exit wrote no
    row: it set `state.status = "failed"` and returned, and no row carries
    `RunState.status`.
    """
    module = _stage_module()
    monkeypatch.setattr(module.github_ops, "post_comment",
                        lambda state, body, finding=None: "local://x")
    monkeypatch.setattr(sre_agent, "run", _no_go)

    state = RunState(ticket_id="NOGO-1", ticket_text=TICKET)
    for gate in ("gate1", "gate2"):
        state.decisions.append(HumanDecision(gate=gate, decision="approved",
                                             by="reviewer-1", reason="ok"))
    gates.save(state)
    module._log(state, "system", "plan", "opened", summary="run started")

    rc = module.STAGES["sre"](_args(run_id=state.run_id))

    assert rc != module.EXIT_OK, (
        f"the sre stage exited {rc} on a no_go, so gate3 would run on a change "
        f"the SRE refused"
    )
    rendered = timeline.render_text(state.run_id)
    assert "INCOMPLETE" not in rendered, (
        f"an SRE no_go renders as INCOMPLETE, which claims the run stopped "
        f"without deciding. It decided -- it decided no.\n{rendered}"
    )
    assert _FAILED_BANNER in rendered, (
        f"the no_go run does not render as {_FAILED_BANNER}:\n{rendered}"
    )
    actions = [e.action for e in log.read(state.run_id)]
    assert actions[-1] == "failed", (
        f"the last log row is {actions[-1]!r}, so timeline._outcome cannot read "
        f"this run's ending. Rows: {actions}"
    )


def test_an_sre_no_go_run_has_an_ENDING_locally(monkeypatch):
    """The same, on graph.py's path -- where the no_go branch also wrote no row.

    CLAUDE.md records this branch as "defensive structure rather than exercised
    behaviour". It is exercised now.
    """
    monkeypatch.setattr(sre_agent, "run", _no_go)

    final = graph.run_pipeline("NOGO-1", TICKET)

    assert final.status == "failed", (
        f"the local no_go run ended {final.status!r}, not `failed`"
    )
    rendered = timeline.render_text(final.run_id)
    assert "INCOMPLETE" not in rendered, (
        f"graph.py's SRE no_go renders as INCOMPLETE:\n{rendered}"
    )
    assert _FAILED_BANNER in rendered, (
        f"graph.py's no_go run does not render as {_FAILED_BANNER}:\n{rendered}"
    )
    actions = [e.action for e in log.read(final.run_id)]
    assert actions[-1] == "failed", (
        f"the last row is {actions[-1]!r}, so the run has no readable ending. "
        f"Rows: {actions}"
    )


def test_the_no_go_row_carries_the_verdict_that_caused_it(monkeypatch):
    """A `failed` row with no verdict cannot say WHICH ending this was.

    Two endings write `failed` -- the revision cap and the SRE no_go -- and they
    render the same banner by design. The distinguishing information has to be
    on the row, or the timeline shows a run that failed for no stated reason.
    """
    monkeypatch.setattr(sre_agent, "run", _no_go)
    final = graph.run_pipeline("NOGO-1", TICKET)

    ending = [e for e in log.read(final.run_id) if e.action == "failed"]
    assert ending, "no `failed` row at all"
    assert ending[-1].verdict == "no_go", (
        f"the ending row's verdict is {ending[-1].verdict!r}, so nothing on the "
        f"timeline says the SRE is what stopped this run"
    )
    assert ending[-1].stage == "sre", (
        f"the ending row's stage is {ending[-1].stage!r}, not `sre`"
    )


def test_a_clean_run_still_renders_as_PROMOTED():
    """The anti-vacuity check for the whole file.

    Every assertion above is about a run that did NOT ship. A change that made
    every run render `FAILED` would satisfy all of them, and break the demo's
    clean half.
    """
    final = graph.run_pipeline("CLEAN-1", TICKET)
    assert final.status == "promoted"
    rendered = timeline.render_text(final.run_id)
    assert timeline._OUTCOME["promoted"][0] in rendered, (
        f"the clean run does not render as PROMOTED:\n{rendered}"
    )
    assert _FAILED_BANNER not in rendered
