# Plan — Aya

**Your lane:** testing and metrics. `tests/` — contract tests, the block test,
the no-checks baseline, chaos tests, and the DORA metrics batch.

You test the pipeline as a **black box**: call `run_pipeline(...)` and assert on
the final `RunState`. You never need to know how an agent works inside. Starter
tests already pass (`tests/test_pipeline_smoke.py`) — build out from there.

---

## Week 1 — Aug 8 to 14: contract tests + the baseline

- [ ] **Contract/shape tests.** For each agent stub, assert its output validates
  against `state.py` and the field values are sane. (The smoke test shows the
  pattern.)
  *Done when:* `pytest -q` covers all five result types; any drift fails loudly.
  *You're unblocked because:* the stubbed pipeline already runs — you don't wait
  for real agents.

- [ ] **The no-checks baseline.** A stripped path: one agent writes code, it
  "merges," nothing stops it. This is the "before" you compare the Agent Org
  against.
  *Done when:* you can run the baseline and measure it (it lets the poisoned
  change through — that's the point).

- [ ] **Measure the baseline.** Record: does the poisoned change reach "merged"?
  How many steps? This needs nobody else.
  *Done when:* you have a baseline number to compare against.

*End of week 1:* the contract is guarded by tests, and the baseline exists.

---

## Week 2 — Aug 15 to 21: determinism + chaos

- [ ] **Stability runs.** Run each agent 10× on the same input; assert the output
  **shape** never changes. Report anything that drifts.
  *Done when:* 10 identical-shape runs per agent, or a filed drift report.

- [ ] **The block test (most important).** Assert the poisoned ticket ends
  `status == "blocked"` with 2 blocking findings — as a repeatable test, run many
  times.
  *Done when:* the block is proven deterministic across 20+ runs.

- [ ] **Chaos tests.** Break things on purpose: hang a gate, loop the reviewer
  past `MAX_REVISION_LOOPS`, kill a scanner mid-run. Assert the pipeline fails
  safe (never promotes a bad change).
  *Done when:* each fault is a test that passes (pipeline degrades safely).

- [ ] **Response cache** so demo runs are fast (pair with Habiba on scanner
  caching).
  *Done when:* a full demo run completes in a couple of seconds.

*End of week 2:* the block is provably deterministic and the pipeline survives
chaos.

---

## Week 3 — Aug 22 to 27: DORA metrics + the number that sells it

- [ ] **DORA batch.** Run 10 tickets with no checks (baseline) and 10 through the
  Agent Org. Collect: change-failure rate, lead time, how many bad changes each
  path let through.
  *Done when:* you have both columns of raw data.

- [ ] **Build the comparison table.** The headline: the Agent Org blocks the
  poisoned change 10/10; the baseline ships it. Make it one clean table/chart.
  *Done when:* the table is in the demo deck.

- [ ] **Record the backup video by Monday Aug 25.** A full clean + poisoned run,
  in case the live demo hits trouble.
  *Done when:* the video exists and plays start to finish.

- [ ] **After freeze (Tue Aug 25):** only re-run metrics and fix flakiness.

---

## How you stay unblocked

You call `run_pipeline(...)` and read `RunState` + the log (`agentorg.log.read`).
That's the whole surface. It's worked since day 1 on stubs and keeps working as
real code lands — because you assert on the frozen contract, not on internals. You
wait on nobody.
