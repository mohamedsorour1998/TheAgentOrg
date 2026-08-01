# Plan — Reem

**Your lane (testing, half 1):** the test *inputs* and the **functional /
correctness** side of the suite. You own `target_repo/`, `tickets/`, and the
`tests/functional_*` + baseline tests. Aya owns the resilience + metrics half —
you two are the testing pair and split the work evenly.

You don't need AWS. You work in plain Python: build the app the agents edit,
write the two tickets, then prove the pipeline produces **correct** results.

**How you and Aya avoid collisions:** you each write your **own test files** in
`tests/` (yours are prefixed `test_functional_` and `test_baseline_`). Same
folder, different files — no merge conflicts.

---

## Week 1 — Aug 8 to 14: the inputs + first correctness tests

Starter versions exist (`target_repo/app/auth.py`, `tickets/clean.md`,
`tickets/poisoned.md`, `tests/test_pipeline_smoke.py`). Finish them and add yours.

- [ ] **Finish the target app.** Keep `app/auth.py` tiny but real — a Flask login
  handler with a couple of functions worth changing.
  *Done when:* `python -m pytest target_repo/tests` passes.

- [ ] **Write the clean ticket** (`tickets/clean.md`): "add a per-IP login rate
  limit," clear description + acceptance criteria.
  *Done when:* a developer could implement it from the ticket alone.

- [ ] **Write the poisoned ticket** (`tickets/poisoned.md`): same feature, but the
  attached reference hardcodes an AWS key (`AKIAIOSFODNN7EXAMPLE` — AWS's public
  placeholder, nothing sensitive).
  *Done when:* the poisoned reference diff, scanned on its own, trips gitleaks.

- [ ] **★ Hand the poisoned ticket to Habiba by Wed Aug 12.** She needs a diff
  that actually trips her scanner. This is the team's single cross-dependency.
  *Done when:* Habiba confirms gitleaks flags the AWS key on your ticket.

- [ ] **Write `tests/test_functional_contract.py`.** For each agent result, assert
  it validates against `state.py` and the values are sane (plan has tasks, dev
  has a diff + files, review has a verdict, etc.). The smoke test shows the shape.
  *Done when:* all five result types are covered; malformed output fails the test.
  *You're unblocked because:* the stubbed pipeline already runs — you assert on
  the frozen contract, not on anyone's real code.

*End of week 1:* both tickets exist, the poisoned one is confirmed, and the
correctness of every agent's output is under test.

---

## Week 2 — Aug 15 to 21: the "no-checks" baseline + happy path

- [ ] **Build the no-checks baseline** (`tests/test_baseline.py`): a stripped path
  where one agent writes code and it "merges" with no gates and no security. This
  is the "before" Aya compares the Agent Org against in the DORA table.
  *Done when:* the baseline runs and (correctly) lets the poisoned change through
  — that failure is the point you're proving.
  *Pair with Aya:* you build the baseline path; she feeds it into the metrics.

- [ ] **Happy-path assertions** (`tests/test_functional_flow.py`): clean ticket →
  `promoted`; the revision loop fires when the reviewer requests changes.
  *Done when:* both behaviours are asserted and green.

- [ ] **CI hookup with Mariam.** Make sure your `target_repo/tests` and your
  `tests/test_functional_*` run in her CI workflow.
  *Done when:* every PR shows your tests passing.

*End of week 2:* correctness is locked and the baseline exists for the metrics.

---

## Week 3 — Aug 22 to 27: demo script + rehearsal

- [ ] **Write the demo script** (you own the spoken walkthrough, since you know
  the tickets best). Tight 5–7 min in **English**: clean run passes, poisoned run
  blocks, show the timeline.
  *Done when:* the script is written and reviewed by Sorour.

- [ ] **Rehearse it with the team, twice.** Time it. Note every rough spot.
  *Done when:* two clean run-throughs, under time, in English.

- [ ] **After freeze (Tue Aug 25):** only rehearsal and small wording fixes.

---

## How you stay unblocked

You write plain Python inputs and correctness tests against the frozen contract —
nothing waits on anyone's real code. Your one outbound handoff is the poisoned
ticket → Habiba by Aug 12. You and Aya split `tests/` by filename, so you never
step on each other.
