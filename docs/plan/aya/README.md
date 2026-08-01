# Aya — General Plan

**Role:** testing (half 2) — resilience + metrics. **Lane:** the
block-determinism test, the chaos tests, and the DORA metrics.

Reem owns the correctness half + inputs — you two are the testing pair and
split the work evenly. You test the pipeline as a **black box**: call
`run_pipeline(...)` and assert on the final `RunState`. You never need to
know how an agent works inside.

**How you and Reem avoid collisions:** you each write your **own test files**
in `tests/` (yours are prefixed `test_block_`, `test_chaos_`, `test_dora_`).
Same folder, different files — no merge conflicts. Reem builds the no-checks
baseline; you consume it in the metrics.

## The shape of your 3 weeks

| Week | Theme | The one thing that must be true by Friday |
|---|---|---|
| [1](week1.md) | Prove the block is deterministic | 20+ repeated poisoned runs all block the same way |
| [2](week2.md) | Chaos + metrics harness | The pipeline fails safe under 3 fault types; raw metrics come out of both paths |
| [3](week3.md) | DORA table + backup video | Comparison table + a full backup demo video, in English |

## Where you plug into everyone else

You call `run_pipeline(...)` and read `RunState` + `agentorg.log.read(...)` —
the whole surface has worked since day 1 on stubs and keeps working as real
code lands, because you assert on the frozen contract, never on internals.
Your only inbound dependency is Reem's baseline (week 2), and even that has a
stub-shaped placeholder you can start against immediately.

## The one rule

Never touch `agentorg/state.py`. If you need a field to assert on, ask
Sorour — add-only, never rename.
