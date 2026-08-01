# Reem — General Plan

**Role:** testing (half 1) — inputs + correctness. **Lane:** `target_repo/`,
`tickets/`, and the `tests/test_functional_*` + baseline tests.

Aya owns the resilience + metrics half — you two are the testing pair and
split the work evenly. You don't need AWS. You work in plain Python: build
the app the agents edit, write the two tickets, then prove the pipeline
produces **correct** results.

**How you and Aya avoid collisions:** you each write your **own test files**
in `tests/` (yours are prefixed `test_functional_` and `test_baseline`). Same
folder, different files — no merge conflicts.

## The shape of your 3 weeks

| Week | Theme | The one thing that must be true by Friday |
|---|---|---|
| [1](week1.md) | Inputs + first correctness tests | Both tickets exist; poisoned one confirmed to trip gitleaks; all 5 result types under contract test |
| [2](week2.md) | No-checks baseline + happy path | Baseline exists for Aya's DORA table; revision loop asserted |
| [3](week3.md) | Demo script + rehearsal | Script written, two clean rehearsals, in English |

## The one cross-dependency

**You → Habiba, due Wed Aug 12:** hand her the poisoned ticket. She needs a
diff that actually trips gitleaks. This is the team's single hard handoff —
everything else is parallel-safe via fixtures.

## The one rule

Never touch `agentorg/state.py`. If a shape doesn't fit what you need to
test, ask Sorour to add a field — add-only, never rename.
