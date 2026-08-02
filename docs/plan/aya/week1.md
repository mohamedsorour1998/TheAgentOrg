# Aya — Week 1 (Aug 8–14): prove the block is deterministic

You own the black-box resilience + metrics tests for The Agent Org (team
RosettaTeam, DevOpsDays Cairo 2026). You never touch the pipeline internals —
you call the entry point `run_pipeline(...)` and read the append-only log with
`log.read(...)`, then assert on the final `RunState`. Nothing this week needs
AWS or anyone else's real code: the pipeline already runs end-to-end on stubs
and already blocks the poisoned ticket, so you can test the demo's core promise
— "it blocks every single time" — from day 1.

Two shapes you will assert against all week (from `agentorg/state.py`, the
frozen contract — copy field names EXACTLY):

```python
class SecurityResult(BaseModel):
    verdict: Literal["pass", "block"]
    findings: list[Finding] = []
    blocking: list[Finding] = []      # <-- the field is `blocking`, NOT `blocking_findings`
    explanation: str = ""

class RunState(BaseModel):
    run_id: str
    ticket_id: str
    ticket_text: str
    plan: PlanResult | None = None
    dev: DevResult | None = None
    review: ReviewResult | None = None
    security: SecurityResult | None = None
    sre: SREResult | None = None
    revision_count: int = 0
    status: Literal["running", "blocked", "rejected", "promoted", "failed"] = "running"
```

The entry point you drive (from `agentorg/graph.py`, do not change it):

```python
def run_pipeline(ticket_id: str, ticket_text: str, *, poisoned: bool = False,
                 auto_approve: bool = True) -> RunState: ...
```

On a `block` verdict the graph sets `status="blocked"` and returns immediately —
no gate2, no SRE, no promote. That is what makes the poisoned ticket safe.

---

## Sat Aug 8 — kickoff (with everyone)

**Task: attend the 90-minute kickoff.**
- Walk `agentorg/state.py` field by field with the team. Confirm the block rule
  lives in code (`compute_security_verdict` in `state.py`), not in a prompt —
  that is the answer to "how do you know it's not the model guessing?" and it is
  why your determinism test can exist at all.
- Confirm ownership: you own `tests/test_block_*`, `tests/test_chaos_*`,
  `tests/test_dora_*`. Your starter file is `tests/test_pipeline_smoke.py`.
- Write down the one rule: you may ADD optional fields to `state.py` models via
  Sorour, but never rename or remove one. Your tests assert on `state.security.blocking`
  and `state.status` — a rename there breaks every test you write.
- **Done when:** on your own machine,
  ```bash
  pip install -e ".[dev]"
  pytest -q
  ```
  prints `3 passed`.

---

## Sun–Mon Aug 9–10 — the determinism test

**Task: write `tests/test_block_determinism.py`.**
This is the demo's insurance policy: the poisoned ticket must end
`status == "blocked"` with exactly 2 blocking findings, and it must do so on
every single run, never flipping. Run it 20+ times in one test.

Your starter file `tests/test_pipeline_smoke.py` already asserts a single
poisoned run blocks (`assert len(state.security.blocking) == 2`). You are
turning that one shot into a determinism guarantee.

Create `tests/test_block_determinism.py` with this exact content:

```python
"""Determinism guard for the security block. Owner: Aya.

The poisoned ticket must block on EVERY run with exactly 2 blocking findings.
Black-box only: run_pipeline(...) + log.read(...), assert on final RunState.
"""

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
```

Steps:
1. Create the file above verbatim.
2. Run it repeatedly to confirm it never flips (see Done when).

**Done when:**
```bash
pytest -q tests/test_block_determinism.py
```
prints `3 passed`. Then prove non-flakiness by looping the suite:
```bash
for i in $(seq 1 5); do pytest -q tests/test_block_determinism.py || break; done
```
Every iteration prints `3 passed`.

**You're unblocked because:** the stubbed pipeline already blocks correctly (the
gitleaks stub returns 2 critical findings for the `AKIA...` key, and
`compute_security_verdict` turns that into a block). You do not wait for
Habiba's real scanners. When they land in week 2, this same test starts guarding
them for free.

**Blocks / hands off to:** nobody depends on this file, but it is the gate you
re-run on Fri Aug 21 (the hard deadline: poisoned blocks every time on real
scanners) and every day of week 3.

---

## Tue–Wed Aug 11–12 — stability (shape) runs per agent

**Task: write `tests/test_block_shape_stability.py` — run each agent 10× on the
same input and assert the output SHAPE is stable** (field presence + types), not
the exact text. LLM wording varies run to run; the contract shape must not.

You call the five agent stubs directly. Their signatures (from the frozen
contract) are:

```python
planner.run(state) -> PlanResult
developer.run(state, poisoned=False) -> DevResult
reviewer.run(state) -> ReviewResult
security.run(state, use_real_scanners=False) -> SecurityResult
sre.run(state) -> SREResult
```

Populate a `RunState` step by step so each agent has its prerequisites, then
loop each agent 10× and compare a shape fingerprint. `pydantic`'s
`model_dump()` gives a dict; `type(v).__name__` gives a stable type name for
each field (a list is `'list'` regardless of contents, so varying list contents
do not change the shape).

Create `tests/test_block_shape_stability.py`:

```python
"""Shape-stability guard. Owner: Aya.

Each agent, 10x on the same input, must return the same field names and field
types. We assert SHAPE (presence + type), never exact LLM text.
"""

import pytest

from agentorg.state import RunState
from agentorg.agents import planner, developer, reviewer, security, sre

TICKET_TEXT = "Add a per-IP login rate limit."


def _shape(model):
    """Field-name -> type-name fingerprint of a pydantic model."""
    return {k: type(v).__name__ for k, v in model.model_dump().items()}


def _fresh_state():
    return RunState(ticket_id="STAB-1", ticket_text=TICKET_TEXT)


def _populate(state):
    # Give each downstream agent the fields it may read.
    state.plan = planner.run(state)
    state.dev = developer.run(state, poisoned=False)
    state.review = reviewer.run(state)
    state.security = security.run(state)
    return state


@pytest.mark.parametrize("agent_name", ["planner", "developer", "reviewer", "security", "sre"])
def test_agent_output_shape_is_stable_over_10_runs(agent_name):
    calls = {
        "planner":   lambda s: planner.run(s),
        "developer": lambda s: developer.run(s, poisoned=False),
        "reviewer":  lambda s: reviewer.run(s),
        "security":  lambda s: security.run(s),
        "sre":       lambda s: sre.run(s),
    }
    run_agent = calls[agent_name]

    shapes = []
    for _ in range(10):
        state = _populate(_fresh_state())
        result = run_agent(state)
        shapes.append(_shape(result))

    first = shapes[0]
    for i, shape in enumerate(shapes):
        assert shape == first, (
            f"{agent_name} run {i} drifted in shape:\n  first={first}\n  got  ={shape}"
        )


def test_shapes_match_the_declared_types():
    # Presence + type sanity against the frozen contract.
    state = _populate(_fresh_state())
    assert set(_shape(state.plan)) == {"tasks", "acceptance_criteria", "target_files", "notes"}
    assert set(_shape(state.dev)) == {"branch", "diff", "summary", "files_changed", "pr_url"}
    assert set(_shape(state.review)) == {"verdict", "comments", "must_fix"}
    assert set(_shape(state.security)) == {"verdict", "findings", "blocking", "explanation"}
    assert state.review.verdict in ("approve", "changes_requested")
    assert state.security.verdict in ("pass", "block")
```

If a run drifts (an agent sometimes drops a field or changes a type), the test
fails and names the field. That is not your bug to fix — file a one-line drift
note for Sorour (he owns the agent stubs) and keep the failing test as the proof.

**Done when:**
```bash
pytest -q tests/test_block_shape_stability.py
```
prints `6 passed` (5 parametrized agents + the type-sanity test). If it fails,
you have a filed drift note naming the unstable agent + field.

**You're unblocked because:** the stubs return validated fixtures today, so all
five agents already have stable shapes — you are locking that in before real
LLM calls land, so any future drift trips your test immediately.

---

## Thu–Fri Aug 13–14 — buffer + re-verify after Sorour's dry run

**Task: re-run your whole week-1 suite after Sorour's end-of-week buffer dry
run** (his Terraform/AWS work happens this week; confirm it did not perturb the
stubbed graph your tests depend on).

Steps:
1. Pull the latest `main` after Sorour posts his Thu/Fri dry-run is green.
2. Re-run everything you own so far plus the smoke starter.

**Done when:**
```bash
pytest -q tests/test_pipeline_smoke.py tests/test_block_determinism.py tests/test_block_shape_stability.py
```
prints `9 passed` (3 smoke + 3 determinism + 6 stability = the count you get is
`12 passed`; if you added no extra tests it is exactly the sum of the three
files). Confirm the poisoned run still ends `blocked` with 2 blocking findings.

**Blocks / hands off to:** this is the clean baseline you build week-2 chaos
tests on top of. Tell the team your determinism guard is green so Reem/Habiba
know the block is under test before they swap in real code.

---

## End of week 1 — done when

- `tests/test_block_determinism.py` is green across 20+ repeated poisoned runs —
  `status == "blocked"`, `len(state.security.blocking) == 2`, never flips, and
  the log shows a `blocked` action and never a `promoted` one.
- `tests/test_block_shape_stability.py` is green: each agent's output shape is
  identical over 10 runs, or a drift note naming the field is filed for Sorour.
- `pytest -q` over your three files passes with no flakes across 5 back-to-back
  loops.
- The demo's core promise — "it blocks every time" — is under a repeatable test
  before any real scanner or agent lands.
