"""The no-checks baseline: plan -> develop -> merge, with NO review, NO security,
NO gates. Owner: Reem.

This is the deliberate "before" picture. Without the Agent Org's checks the
poisoned change ships untouched -- that failure is the point. Aya's DORA harness
(tests/dora_runner.py) consumes run_baseline() as the baseline path.

WHY THIS FILE IS BOTH A MODULE AND A TEST FILE. `run_baseline` is imported by
tests/dora_runner.py, so it has to live somewhere importable; pyproject's
`pythonpath = ["."]` makes `from tests.test_baseline import run_baseline` work
from the repository root. Keeping it here rather than in a separate module is
what the spec asks for and keeps the "before" path in the same file as the two
tests that describe it.

WHAT THIS FILE DELIBERATELY DOES NOT DO: it never calls reviewer.run,
security.run, sre.run, gates.pause or github_ops.open_pr. That absence IS the
baseline. A future edit that "fixes" the baseline by adding a check destroys the
demo's contrast -- see test_the_baseline_runs_no_checks_at_all, which fails if
any of the five result fields is ever populated.

Run: pytest -q tests/test_baseline.py
"""

from agentorg.agents import developer, planner
from agentorg.state import RunState

# AWS's own public documentation placeholder -- nothing sensitive. Named here
# rather than inlined so the poisoned assertion below says what it is looking
# for.
POISON_KEY = "AKIAIOSFODNN7EXAMPLE"


def run_baseline(ticket_text: str, *, poisoned: bool = False) -> RunState:
    """Plan -> develop -> merge. No review, no security, no gates.

    Mirrors the shape of run_pipeline but strips every check, so a poisoned diff
    is never inspected and the change is marked promoted regardless.

    KEYWORD-ONLY `poisoned`, matching developer.run's own signature and
    run_pipeline's. Aya's DORA runner passes it by keyword; a positional second
    parameter here would let a caller silently pass a ticket id into it.
    """
    state = RunState(ticket_id="BASELINE", ticket_text=ticket_text)
    state.plan = planner.run(state)
    state.dev = developer.run(state, poisoned=poisoned)
    # "Merge" with no review/security/gates: just declare it shipped.
    state.status = "promoted"
    return state


def test_baseline_promotes_a_clean_change():
    state = run_baseline("Add a per-IP login rate limit.", poisoned=False)
    assert state.status == "promoted"
    assert state.plan is not None
    assert state.dev is not None


def test_the_baseline_runs_no_checks_at_all():
    """The absence of checks is the baseline's whole content, so assert it.

    Split out from the promotion test because these two assertions fail for
    different reasons: the one above breaks if the baseline stops shipping, this
    one breaks if the baseline starts checking. A single test conflating them
    would report "the baseline is wrong" without saying which way.
    """
    state = run_baseline("Add a per-IP login rate limit.", poisoned=True)
    assert state.review is None, "the baseline must not review"
    assert state.security is None, "the baseline must not scan"
    assert state.sre is None, "the baseline must not assess"
    assert state.decisions == [], "the baseline must not ask a human"
    assert state.revision_count == 0, "the baseline has no revision loop"


def test_baseline_ships_the_poisoned_change():
    """The whole point: with no security stage, the hardcoded key sails through.

    THIS TEST ASSERTS AN UNSAFE OUTCOME ON PURPOSE. It is the "before" picture
    the DORA table contrasts against, not a bug report. If it ever goes red
    because the baseline stopped shipping the poison, the contrast is gone and
    the demo's headline comparison has no left-hand column -- fix the baseline,
    do not relax the assertion.
    """
    state = run_baseline("Add a per-IP login rate limit.", poisoned=True)
    assert state.status == "promoted"          # it SHIPPED
    assert state.security is None              # nothing scanned it
    assert POISON_KEY in state.dev.diff        # the secret is right there

    # The negative control. Without it, a developer stub that planted the key in
    # BOTH diffs would satisfy the line above while destroying the contrast the
    # DORA table is built on.
    clean = run_baseline("Add a per-IP login rate limit.", poisoned=False)
    assert POISON_KEY not in clean.dev.diff, (
        "the clean baseline diff must not carry the key, or 'poisoned' means "
        "nothing and the baseline column of the DORA table is a coincidence"
    )
