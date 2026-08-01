# tests/ — pipeline test + metrics harness

**Owner: Aya.**

You test the pipeline as a **black box**: you call `run_pipeline(...)` and assert
on the final `RunState`. You do not need to know how any agent works internally.

## What to build (see docs/plan/aya.md)

1. **Contract/shape tests** — run each agent stub and assert the output validates
   against `state.py` (never drifts).
2. **The block test** (most important) — the poisoned ticket must end
   `status == "blocked"` with 2 blocking findings, every run.
3. **No-checks baseline** — a one-agent "just write and merge, no gates" path, so
   you can compare it against the full Agent Org.
4. **Chaos** — hang a gate, loop the reviewer, kill a scanner mid-run; assert the
   pipeline fails safe.
5. **DORA batch** — run 10 tickets with no checks vs 10 through the Agent Org and
   build the metrics table.
