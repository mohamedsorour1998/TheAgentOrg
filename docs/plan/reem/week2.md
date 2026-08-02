# Reem — Week 2 (Aug 15–21): the "no-checks" baseline + the happy path

This week you build the two tests that anchor the demo's before/after story:
a **baseline** path with no review, no security, no gates (so the poisoned change
*ships* — the "before" picture Aya turns into a DORA table), and the
**happy-path** flow test (clean → promoted, plus the revision loop firing once
and terminating within the cap). Then you confirm your tests run in Mariam's CI.

Shared hard deadline: **end of Friday Aug 21, the poisoned ticket blocks every
single time on real scanners + real agents.** Your baseline is the deliberate
counter-example that ships it. **No AWS.**

Reminders from the frozen contract (`agentorg/state.py`):
- `RunState` fields: `plan`, `dev`, `review`, `security`, `sre` (all optional,
  default `None`), `decisions`, `revision_count`, `status`
  (`running`/`blocked`/`rejected`/`promoted`/`failed`).
- `config.MAX_REVISION_LOOPS` defaults to `3` (in `agentorg/common/config.py`).
- Add-only rule: never rename/remove a `state.py` field; ask Sorour to add one.

The real pipeline entry point (from `agentorg/graph.py`), which you treat as a
black box for the flow test:
```python
def run_pipeline(ticket_id: str, ticket_text: str, *, poisoned: bool = False,
                 auto_approve: bool = True) -> RunState:
    # plan -> gate1 -> develop -> review -(loop, capped MAX_REVISION_LOOPS)-> open_pr
    #      -> security -> gate2 -> sre -> gate3 -> promote
    # On a `block` security verdict: status="blocked", post_comment(...), return early.
```

---

## Sat–Mon Aug 15–17 — the no-checks baseline

**Task: write `tests/test_baseline.py` with `run_baseline(ticket_text) -> RunState`.**
This is a stripped path: the planner produces a plan, the developer produces a
diff, and it "merges" — **no reviewer, no security scan, no gates.** The point is
that with checks removed the poisoned change ships. This is Aya's "before" input.

You call the same real agent functions the graph calls, but only `plan` and
`develop`, then mark the run promoted. Their signatures (from
`agentorg/agents/`), which return validated fixtures today and real results as
Sorour's stubs land:
```python
# agentorg/agents/planner.py
def run(state: RunState) -> PlanResult: ...
# agentorg/agents/developer.py
def run(state: RunState, poisoned: bool = False) -> DevResult: ...
```

Write the full file:
```python
"""The no-checks baseline: plan -> develop -> merge, with NO review, NO security,
NO gates. Owner: Reem.

This is the deliberate "before" picture. Without the Agent Org's checks the
poisoned change ships untouched — that failure is the point. Aya's DORA harness
(tests/test_dora_*.py) consumes run_baseline() as the baseline path.

Run: pytest -q tests/test_baseline.py
"""

from agentorg.state import RunState
from agentorg.agents import planner, developer

POISON_KEY = "AKIAIOSFODNN7EXAMPLE"


def run_baseline(ticket_text: str, *, poisoned: bool = False) -> RunState:
    """Plan -> develop -> merge. No review, no security, no gates.

    Mirrors the shape of run_pipeline but strips every check, so a poisoned diff
    is never inspected and the change is marked promoted regardless.
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
    # No checks ran at all:
    assert state.review is None
    assert state.security is None
    assert state.sre is None
    assert state.decisions == []


def test_baseline_ships_the_poisoned_change():
    # The whole point: with no security stage, the hardcoded AWS key sails through.
    state = run_baseline("Add a per-IP login rate limit.", poisoned=True)
    assert state.status == "promoted"          # it SHIPPED
    assert state.security is None              # nothing scanned it
    assert POISON_KEY in state.dev.diff        # the secret is right there in the diff
```

**Done when:**
```bash
pytest -q tests/test_baseline.py
```
prints `2 passed`, and `test_baseline_ships_the_poisoned_change` demonstrably
promotes a diff that still contains `AKIAIOSFODNN7EXAMPLE`.
**You're unblocked because:** you reuse the real `planner.run` / `developer.run`
(fixtures today, real later) and never touch anyone's internals.
**Blocks / Hands off to:** Aya — her metrics harness (`tests/test_dora_*.py`)
imports `run_baseline` from this file as the "before" path. Keep the function
name and signature `run_baseline(ticket_text, *, poisoned=False) -> RunState`
stable so her import doesn't break.

---

## Tue–Wed Aug 18–19 — happy path + revision loop

**Task: write `tests/test_functional_flow.py`.**
Assert two behaviors through the real `run_pipeline` black box:
1. A clean ticket runs end to end and finishes `promoted`.
2. The developer⇄reviewer revision loop fires when the reviewer requests changes
   and terminates within `config.MAX_REVISION_LOOPS`.

The loop lives in `graph.py`: each time `reviewer.run(state)` returns
`changes_requested`, `state.revision_count` increments and the developer runs
again; the loop breaks on `approve` **or** when `revision_count >=
MAX_REVISION_LOOPS`. To exercise it deterministically without waiting on
Sorour's real reviewer, monkeypatch `reviewer.run` (you're driving the frozen
contract, not asserting on his internals). Relevant graph excerpt:
```python
while True:
    state.dev = developer.run(state, poisoned=poisoned)
    state.review = reviewer.run(state)
    if state.review.verdict == "approve" or state.revision_count >= config.MAX_REVISION_LOOPS:
        break
    state.revision_count += 1
```

Write the full file:
```python
"""Happy path + revision loop, driven through the real run_pipeline black box.
Owner: Reem.  Run: pytest -q tests/test_functional_flow.py

Re-run these as Sorour's stubs become real agents in week 2 — they assert on the
frozen contract (status, verdict, revision_count), so they stay valid.
"""

from agentorg import graph
from agentorg.agents import reviewer
from agentorg.common import config
from agentorg.state import ReviewResult


def test_clean_ticket_is_promoted():
    state = graph.run_pipeline("CLEAN-1", "Add a per-IP login rate limit.", poisoned=False)
    assert state.status == "promoted"
    assert state.security.verdict == "pass"
    assert state.sre is not None


def test_revision_loop_fires_once_then_approves(monkeypatch):
    """Reviewer asks for changes on the first pass, approves on the second."""
    calls = {"n": 0}

    def changes_then_approve(state):
        calls["n"] += 1
        if calls["n"] == 1:
            return ReviewResult(verdict="changes_requested", must_fix=["tighten the counter"])
        return ReviewResult(verdict="approve")

    monkeypatch.setattr(reviewer, "run", changes_then_approve)
    state = graph.run_pipeline("CLEAN-1", "Add a per-IP login rate limit.", poisoned=False)
    assert calls["n"] == 2                 # looped exactly once, then approved
    assert state.revision_count == 1       # one revision recorded
    assert state.status == "promoted"


def test_revision_loop_terminates_within_max_loops(monkeypatch):
    """A reviewer that never approves must still terminate at the cap."""
    def always_changes(state):
        return ReviewResult(verdict="changes_requested", must_fix=["never happy"])

    monkeypatch.setattr(reviewer, "run", always_changes)
    state = graph.run_pipeline("CLEAN-1", "Add a per-IP login rate limit.", poisoned=False)
    # It must stop looping at the cap, not spin forever.
    assert state.revision_count == config.MAX_REVISION_LOOPS
    assert config.MAX_REVISION_LOOPS == 3
```

**Done when:**
```bash
pytest -q tests/test_functional_flow.py
```
prints `3 passed`. As Sorour's real reviewer lands mid-week, re-run — the clean
test still promotes; the monkeypatched loop tests still hold because they assert
on the frozen `revision_count` / `status`, not his agent text.
**You're unblocked because:** `run_pipeline` already runs on stubs; monkeypatch
lets you force the loop without his real reviewer.

---

## Thu–Fri Aug 20–21 — CI hookup + Aug 21 deadline check

**Task: confirm your tests run in Mariam's CI (`.github/workflows/ci.yml`).**
Mariam owns the workflow; this week it runs: checkout, setup-python 3.12,
`pip install -e ".[dev]"`, `python make_fixtures.py`, `pytest -q`. Your top-level
tests (`tests/test_functional_contract.py`, `tests/test_functional_flow.py`,
`tests/test_baseline.py`) are under `tests/`, which `pyproject.toml`'s
`[tool.pytest.ini_options].testpaths = ["tests"]` already collects — so
`pytest -q` picks them up automatically.

The one gap is `target_repo/tests`, which needs `app` on the path (run from
`target_repo/`). Ask Mariam to add a step to `ci.yml` that runs them explicitly:
```yaml
      - name: target app tests
        run: cd target_repo && python -m pytest tests -q
```
Verify the whole top-level suite locally the way CI does:
```bash
pip install -e ".[dev]"
python make_fixtures.py
pytest -q
```
**Done when:** on a PR, the CI check shows your three `tests/*` files and the
`target_repo/tests` step, all passing (or failing loudly). Locally `pytest -q`
is green including your new files.

**Task: Aug 21 deadline check — confirm poisoned still blocks on the real path.**
As Sorour's real agents and Habiba's real scanners land this week, re-run the
poisoned pipeline to confirm the block still fires deterministically:
```bash
python -m agentorg.graph --poisoned
```
Expected tail: `status=blocked` and `security verdict=block, blocking=2`. If it
ever flips, flag Sorour/Habiba immediately — this is the shared Aug 21 gate.
**Done when:** poisoned prints `status=blocked` with `blocking=2`, and clean
(`python -m agentorg.graph`) prints `status=promoted`.

---

## End of week 2 — done when

- `tests/test_baseline.py` exists, exports `run_baseline(ticket_text, *,
  poisoned=False) -> RunState`, and `pytest -q tests/test_baseline.py` prints
  `2 passed` — the poisoned case demonstrably ships the `AKIAIOSFODNN7EXAMPLE`
  diff.
- `tests/test_functional_flow.py` asserts clean → promoted and the revision loop
  (fires once; terminates at `MAX_REVISION_LOOPS`); `pytest -q
  tests/test_functional_flow.py` prints `3 passed`.
- Your tests run automatically in Mariam's CI on every PR (top-level `tests/`
  via `pytest -q`, plus a `target_repo/tests` step).
- Poisoned still blocks on the real path (`python -m agentorg.graph --poisoned`
  → `status=blocked`, `blocking=2`).

**Cut/fallback note:** if Sorour's real reviewer is late, the revision-loop tests
still hold via the `monkeypatch` on `reviewer.run` — they assert on the frozen
`revision_count`/`status`, so they don't wait on his agent. Never weaken the
baseline test to "not ship" the poison; the shipped poison IS the before-picture.
