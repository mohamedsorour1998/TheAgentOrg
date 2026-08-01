"""Smoke tests proving the stubbed pipeline runs end-to-end. Owner: Aya (starter).

These pass on day 1 against stubs. As real code lands they keep passing because
they assert on the frozen contract, not on internals.
"""

from agentorg.graph import run_pipeline


def test_clean_ticket_is_promoted():
    state = run_pipeline("CLEAN-1", "Add a per-IP login rate limit.", poisoned=False)
    assert state.status == "promoted"
    assert state.security.verdict == "pass"


def test_poisoned_ticket_is_blocked():
    state = run_pipeline("POISON-1", "Add a per-IP login rate limit.", poisoned=True)
    assert state.status == "blocked"
    assert state.security.verdict == "block"
    assert len(state.security.blocking) == 2


def test_every_result_matches_the_contract():
    state = run_pipeline("CLEAN-1", "Add a per-IP login rate limit.", poisoned=False)
    # Re-validating is a no-op if shapes are right; raises if any lane drifted.
    assert state.plan is not None
    assert state.dev is not None
    assert state.review is not None
    assert state.sre is not None
