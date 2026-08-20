"""Chaos: a gate that never returns. Owner: Aya.

The fault must FAIL SAFE: a run whose gate never produces a decision must not
promote, and it must leave a readable record behind rather than vanishing.

BLACK BOX. Both tests drive `graph.run_pipeline` end to end and assert only on
the RunState it persisted and the JSONL log it wrote. Nothing here calls a gate
helper directly. That is the side of the line this file sits on; the inside view
of a seam under fault is tests/test_scanner_resilience.py's job, not this file's.

SCANNER PROVENANCE: not controlled here, and it does not need to be. Both runs
abort at gate 1, which is upstream of the security stage, so no scanner is
reached in either mode and `state.security is None` is asserted below. This file
is the one chaos test that has no provenance mode to declare.

WHY THIS FILE IS SHORT, AND WHY THAT IS THE FINDING. Aya's week-2 spec also asks
for a runaway-reviewer test, and Reem's week-2 spec asks for three flow tests.
All four are already pinned, in stronger form, by tests written for other tasks:

    revision_count == MAX_REVISION_LOOPS      test_agent_fallbacks.py:731
                                              test_gates_cli.py:247
    the log is bounded at cap + 2             test_gates_cli.py:299
    loop fires once, then approves            test_agent_fallbacks.py:824
    clean ticket promotes                     test_pipeline_smoke.py:12-13
                                              test_block_determinism.py:37-38

Re-asserting them here would add five run_pipeline calls (measured 59.9, 66.8 and
63.7 ms for three full runs, against 1.2 ms for the aborted run below) and pin
nothing new. One of them would also be WRONG as specified: the spec's
`len(changes_requested events) <= MAX_REVISION_LOOPS` measures 5 against a cap of
3, because the graph emits three mid-loop lines at graph.py:158, a cap-exit line
at graph.py:153 and a terminal `action="blocked"` line at graph.py:205, all with
verdict="changes_requested". Measured on a capped run, their (stage, action) pairs
are three `("review", "reviewed")`, a fourth `("review", "reviewed")` and one
`("review", "blocked")`. The correct count is MAX_REVISION_LOOPS + 2 and
test_gates_cli.py:299 already asserts exactly that.

A gate seam that RAISES is the one fault in this area nothing covers.
test_gates_cli.py:104 covers a human who says no -- an orderly stop, status
"rejected". This file covers a gate that never answers at all.

Run: pytest -q tests/test_chaos_gate_and_loop.py
"""

import pytest

from agentorg import gates, graph, log

TICKET_TEXT = "Add a per-IP login rate limit."


def test_a_gate_that_never_returns_aborts_before_promoting(monkeypatch):
    """A stuck human is a gate seam that never hands back a decision.

    Modelled as `gates.pause` raising, which is what an exhausted wait looks
    like from the graph's side: no HumanDecision is ever produced. The graph
    does not catch it -- `agentorg/graph.py` contains no `except` clause at all
    -- so the run cannot reach step 8's `status = "promoted"` at graph.py:224.

    ASSERTING THE EXCEPTION ALONE WOULD PROVE NOTHING -- a function patched to
    raise, raises. So the assertions below are about the STATE the aborted run
    left behind: run_pipeline's finally clause at graph.py:122-123 calls
    gates.save, so a run that died at gate 1 must still be on disk, and it must
    not say "promoted".

    The state is captured through the `gates.save` seam rather than read back out
    of `runs/`. Reading the directory cannot work: the call raised, so the run id
    is never returned to the test, and `runs/` is shared and grows on every
    pipeline test in the suite -- so "a state file was written" would be true
    there no matter what this test did.
    """
    saved = []
    real_save = gates.save

    def recording_save(state):
        saved.append((state.run_id, state.status))
        return real_save(state)

    def hung_gate(state, gate):
        raise TimeoutError(f"gate {gate} never got a human decision")

    monkeypatch.setattr(gates, "save", recording_save)
    monkeypatch.setattr(graph.gates, "pause", hung_gate)

    with pytest.raises(TimeoutError):
        graph.run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)

    # THE INSTRUMENT FIRST: if the recorder never fired, every assertion below
    # is vacuously true and this test measures nothing. This repo has shipped
    # exactly that bug -- a recorder on a seam a fixture had already replaced,
    # reporting a reassuring zero.
    assert saved, (
        "run_pipeline's finally clause must have saved the aborted run; if this "
        "is empty the recorder is on the wrong seam and nothing below is real"
    )

    run_id, status = saved[-1]
    assert status != "promoted", (
        f"a run whose gate never answered ended {status!r}; the graph reaches "
        f"status='promoted' only after passing gate3, which a raising gate "
        f"makes unreachable"
    )
    assert status == "running", (
        f"expected the aborted run to be persisted mid-flight as 'running', "
        f"got {status!r} -- if this changed, the graph grew a handler for a "
        f"raising gate and that is what needs asserting instead"
    )

    # And nothing was logged as promoted, on the artifact the judges read.
    assert "promoted" not in [e.action for e in log.read(run_id)]


def test_the_hung_gate_stops_the_run_at_the_first_gate(monkeypatch):
    """Which gate it died at is the difference between a stop and a near-miss.

    Gate 1 sits after PLAN and before DEVELOP, so a run that aborts there never
    produced a diff at all. Without this, the test above would pass identically
    for a run that died at gate 3 with everything else already done -- a far
    weaker property.
    """
    def hung_gate(state, gate):
        raise TimeoutError(f"gate {gate} never got a human decision")

    monkeypatch.setattr(graph.gates, "pause", hung_gate)

    states = []
    real_save = gates.save
    monkeypatch.setattr(gates, "save", lambda s: (states.append(s), real_save(s))[1])

    with pytest.raises(TimeoutError):
        graph.run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)

    assert states, "the recorder never fired; see the note in the test above"
    state = states[-1]
    assert state.plan is not None, "the plan stage runs before gate 1"
    assert state.dev is None, "gate 1 is before DEVELOP: no diff can exist"
    assert state.review is None
    assert state.security is None
    assert state.sre is None
    assert state.decisions == [], "no decision was ever recorded, which is the fault"
