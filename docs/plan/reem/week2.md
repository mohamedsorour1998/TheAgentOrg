# Reem — Week 2 (Aug 15–21): the "no-checks" baseline + happy path

---

## Mon–Tue Aug 15–16 — the no-checks baseline

**Task: build `tests/test_baseline.py`.**
A stripped path where one agent writes code and it "merges" with no gates
and no security scanning at all — the "before" picture Aya compares the
Agent Org against in the DORA table.
```python
def run_baseline(ticket_text: str) -> RunState:
    # plan -> develop -> merge. No review, no security, no gates.
    ...
```
**Done when:** the baseline runs and (correctly) lets the poisoned change
through — that failure is the point you're proving: without checks, the bad
change ships.
**Pair with Aya:** you build the baseline path; she feeds it into the metrics
harness in `test_dora_*.py`.

---

## Wed Aug 17 — happy path + revision loop

**Task: write `tests/test_functional_flow.py`.**
Assert: clean ticket → `promoted`; the revision loop fires when the reviewer
requests changes and terminates within `MAX_REVISION_LOOPS`.
**Done when:** both behaviors are asserted and green against Sorour's now-real
agents (week 2 is when his stubs become real — re-run this test as his agents
land, not just once).

---

## Thu–Fri Aug 18–21 — CI hookup

**Task: confirm your tests run in Mariam's CI.**
Make sure `target_repo/tests` and `tests/test_functional_*` are included in
`.github/workflows/ci.yml`.
**Done when:** every PR shows your tests passing (or failing loudly) in the
CI check Mariam built this week.

---

## End of week 2 — done when

- `test_baseline.py` exists and demonstrably lets the poisoned change ship
  (proving the "before" case).
- `test_functional_flow.py` asserts the happy path and the revision loop.
- Your tests run automatically in CI on every PR.
