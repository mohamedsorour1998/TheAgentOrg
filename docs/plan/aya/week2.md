# Aya — Week 2 (Aug 15–21): chaos + the metrics harness

---

## Mon–Tue Aug 15–16 — chaos tests

**Task: write `tests/test_chaos_*.py`.**
Break things on purpose and assert the pipeline **fails safe** (never
promotes a bad change):
- **hang a gate** (never approved) → run does not promote, stays paused;
- **loop the reviewer** past `MAX_REVISION_LOOPS` → run terminates, doesn't
  spin forever;
- **kill a scanner mid-run** → security fails safe, doesn't silently pass.

**Done when:** each of the three faults is a passing test.
**Pair with Habiba** on the scanner-failure case — she makes the scanner
fail safe (her week 3 task), you assert the pipeline handles whatever she
returns without crashing or silently promoting.

---

## Wed–Thu Aug 17–18 — metrics harness

**Task: build the metrics harness** (`tests/test_dora_*.py` + a small
runner). Consume Reem's no-checks baseline (`test_baseline.py`) and the full
Agent Org path; collect per run:
- did a bad change ship (yes/no)
- how many steps it took
- lead time (ticket → merged/blocked)

**Done when:** you can run N tickets through both paths and get raw numbers
out — doesn't need to be pretty yet, just correct.

---

## Fri Aug 19–21 — response cache

**Task: add a response cache** so demo runs are fast (pair with Habiba on
scanner-side caching — see her week 3).
**Done when:** a full demo run completes in a couple of seconds, not
sequential-LLM-call slow.

---

## End of week 2 — done when

- All three chaos tests pass (hung gate, reviewer loop, killed scanner).
- The metrics harness produces raw numbers from both the baseline and the
  full Agent Org path.
