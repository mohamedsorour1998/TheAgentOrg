from agentorg.graph import run_pipeline
from agentorg import log

TICKET_TEXT = "Add a per-IP login rate limit."


def test_poisoned_always_blocks_20x():
    for i in range(20):
        state = run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)
        assert state.status == "blocked", f"run {i}: status was {state.status!r}"
        assert state.security is not None
        assert state.security.verdict == "block", f"run {i}: verdict {state.security.verdict!r}"
        assert len(state.security.blocking) == 2, (
            f"run {i}: expected 2 blocking findings, got {len(state.security.blocking)}"
        )
        # A blocked run must never reach SRE or promote.
        assert state.sre is None, f"run {i}: SRE ran on a blocked ticket"


def test_blocked_run_never_promotes_in_the_log():
    # The append-only log is what the judges score. Prove it records the block
    # and never a promotion for a poisoned run.
    state = run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)
    events = log.read(state.run_id)
    assert events, "no log events were written"
    actions = [e.action for e in events]
    assert "blocked" in actions
    assert "promoted" not in actions
    # The security event carries the block verdict.
    sec = [e for e in events if e.stage == "security"]
    assert any(e.verdict == "block" for e in sec)


def test_clean_ticket_promotes_for_contrast():
    # The other half of the demo: the clean ticket sails through.
    state = run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)
    assert state.status == "promoted"
    assert state.security.verdict == "pass"
    assert state.security.blocking == []
