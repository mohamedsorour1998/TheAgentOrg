# Aya — Week 1 (Aug 8–14): prove the block is deterministic

Nothing here needs AWS or anyone else's real code — the stubbed pipeline
already blocks correctly, and you test against that from day 1.

---

## Sat Aug 8 — kickoff (with everyone)

**Task: attend the 90-minute kickoff.** Agree `state.py`, the log table, the
poisoned flaw, and directory ownership.
**Done when:** `pip install -e ".[dev]" && pytest -q` is green on your machine.

---

## Sun–Mon Aug 9–10 — block determinism test

**Task: write `tests/test_block_determinism.py`.**
Assert the poisoned ticket ends `status == "blocked"` with 2 blocking
findings — and run it many times (20+) to prove it never flips.
```python
def test_poisoned_always_blocks():
    for _ in range(20):
        state = run_pipeline("poisoned", ..., poisoned=True)
        assert state.status == "blocked"
        assert len(state.security.blocking_findings) == 2
```
**Done when:** the test is green across 20+ repeated runs.
**You're unblocked because:** the stubbed pipeline already blocks correctly —
you don't wait for Habiba's real scanners; when they land in week 2, this
same test starts guarding them for free.

---

## Tue–Wed Aug 11–12 — stability runs

**Task: run each agent 10× on the same input; assert the output shape never
changes** (field presence/types, not exact text — LLM output varies, shape
shouldn't).
**Done when:** 10 identical-shape runs per agent, or a filed drift report if
something's unstable (file it as a note for Sorour, not a blocker for you).

---

## Thu–Fri Aug 13–14 — buffer

**Task: re-run the determinism suite once more** after Sorour's end-of-week
buffer verification (his dry run), to catch any regression from his infra
work before week 2 starts.
**Done when:** `pytest -q tests/test_block_determinism.py` still green.

---

## End of week 1 — done when

- `test_block_determinism.py` is green across 20+ repeated runs.
- Stability runs done for each agent (or drift filed).
- The demo's core promise — "it blocks every time" — is under a repeatable
  test.
