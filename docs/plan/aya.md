# Plan — Aya

**Your lane (testing, half 2):** the **resilience + metrics** side of the suite.
You own the block-determinism test, the chaos tests, and the DORA metrics. Reem
owns the correctness half + inputs — you two are the testing pair and split the
work evenly.

You test the pipeline as a **black box**: call `run_pipeline(...)` and assert on
the final `RunState`. You never need to know how an agent works inside.

**How you and Reem avoid collisions:** you each write your **own test files** in
`tests/` (yours are prefixed `test_block_`, `test_chaos_`, `test_dora_`). Same
folder, different files — no merge conflicts. Reem builds the no-checks baseline;
you consume it in the metrics.

---

## Week 1 — Aug 8 to 14: prove the block is deterministic

- [ ] **`tests/test_block_determinism.py`.** Assert the poisoned ticket ends
  `status == "blocked"` with 2 blocking findings — and run it many times (20+) to
  prove it never flips.
  *Done when:* the block is green across 20+ repeated runs.
  *You're unblocked because:* the stubbed pipeline already blocks correctly — you
  don't wait for Habiba's real scanners; when they land, this same test guards
  them.

- [ ] **Stability runs.** Run each agent 10× on the same input; assert the output
  **shape** never changes. File a report on anything that drifts.
  *Done when:* 10 identical-shape runs per agent, or a filed drift report.

*End of week 1:* the demo's core promise — "it blocks every time" — is under a
repeatable test.

---

## Week 2 — Aug 15 to 21: chaos + the metrics harness

- [ ] **`tests/test_chaos_*.py`.** Break things on purpose and assert the pipeline
  **fails safe** (never promotes a bad change):
  - hang a gate (never approved) → run does not promote;
  - loop the reviewer past `MAX_REVISION_LOOPS` → run terminates, doesn't spin;
  - kill a scanner mid-run → security fails safe, doesn't silently pass.
  *Done when:* each fault is a passing test.
  *Pair with Habiba* on the scanner-failure case (she makes the scanner fail
  safe; you assert the pipeline handles it).

- [ ] **Metrics harness** (`tests/test_dora_*.py` + a small runner). Consume
  Reem's no-checks baseline and the full Agent Org path; collect per run:
  did a bad change ship, how many steps, lead time.
  *Done when:* you can run N tickets through both paths and get raw numbers out.

- [ ] **Response cache** so demo runs are fast (pair with Habiba on scanner
  caching).
  *Done when:* a full demo run completes in a couple of seconds.

*End of week 2:* the pipeline survives chaos and the metrics harness produces
numbers.

---

## Week 3 — Aug 22 to 27: the DORA table + backup video

- [ ] **Run the DORA batch.** 10 tickets with no checks (Reem's baseline) vs 10
  through the Agent Org. Collect change-failure rate, lead time, and how many bad
  changes each path let through.
  *Done when:* you have both columns of raw data.

- [ ] **Build the comparison table/chart.** The headline: the Agent Org blocks the
  poisoned change **10/10**; the baseline ships it. One clean visual for the deck.
  *Done when:* the table is in the demo deck (the judges explicitly ask for DORA
  metrics — this is worth real points).

- [ ] **Record the backup video by Monday Aug 25.** A full clean + poisoned run,
  in **English**, in case the live demo hits trouble.
  *Done when:* the video plays start to finish.

- [ ] **After freeze (Tue Aug 25):** only re-run metrics and fix flakiness.

---

## How you stay unblocked

You call `run_pipeline(...)` and read `RunState` + the log (`agentorg.log.read`) —
the whole surface. It's worked since day 1 on stubs and keeps working as real
code lands, because you assert on the frozen contract. You and Reem split `tests/`
by filename. You wait on nobody; your only inbound is Reem's baseline (week 2),
and even that has a stub you can start against.
