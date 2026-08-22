# Handout — Aya · resilience and the metrics

**Your lane:** the determinism tests, the chaos tests, and the DORA metrics.
**Your line:** *"I prove it blocks every time, and I put a number on what that is worth."*

---

## Your three weeks, in one minute

**Week 1 — determinism.** The poisoned ticket must block *every* time, not usually. Ran
it 20 consecutive times and asserted the verdict, plus per-agent shape stability — the
field and type fingerprint of every result, stable across 10 runs.

**Week 2 — chaos.** What happens when things break: a gate that never returns, a
reviewer loop that never converges, a killed scanner (paired with Habiba). Then built the
DORA harness.

**Week 3 — the numbers.** Ran the 10-vs-10 batch and built the comparison table for the
deck.

---

## What you built, and the two ideas to name

You test the pipeline as a **black box**: call `run_pipeline(...)`, assert on the final
`RunState`. You never need to know how an agent works inside.

### Determinism is a claim that needs repetition, not one green run

> A demo that blocks once proves nothing — a model can be lucky. I run the poisoned
> ticket 20 times in a row and assert every one blocks. That is the difference between
> "it worked when we tried it" and "it is deterministic."

### Chaos: a gate that never returns must fail safe

> The gates are where a human is in the loop, so they are also where a demo can hang.
> I test the case where nobody ever clicks. It must fail closed — not proceed, and not
> hang forever with no failing test to point at.

### The measurement trap you found and fixed

> I committed a timing number as "measured" and the next run could not reproduce it —
> 116s, then 149s, then 102s for the same test snapshot, purely load-dependent. So
> "measured" is a property of a number **plus its conditions and spread**. I quote a
> range now, never a point.

---

## Your numbers

| | |
|---|---|
| `tests/test_dora_batch.py` | **14 tests** — the headline claim under test |
| `tests/test_dora_harness.py` | 10 — the harness's raw numbers |
| `tests/test_block_shape_stability.py` | 6 — field/type fingerprint stable over 10 runs |
| `tests/test_block_determinism.py` | 3 — poisoned → blocked, **20 consecutive runs** |
| `tests/test_chaos_gate_and_loop.py` | 2 — a gate that never returns |
| The headline | **10/10 poisoned runs blocked** vs the no-checks baseline |

**The comparison that lands:** Reem's baseline is the same pipeline with no review, no
security and no gates — plan, develop, merge. It ships the credential every time. With
the checks on, 10 out of 10 are stopped.

> The interesting number is not that we block. It is that the "before" picture merges a
> hardcoded AWS key ten times out of ten, and nothing anywhere says so.

---

## If asked

**"Is 10 runs enough?"**
> For a demo claim, yes — and I say 10 rather than implying more. The determinism test is
> 20 consecutive runs and it is the one I would point at if someone doubted the block.

**"Could the tests be passing for the wrong reason?"**
> That is the thing we guard hardest. Every test change here carries a mandatory step:
> break the code deliberately, watch the *named* test fail, then revert. We found
> nineteen-plus assertions that pinned nothing — a test that cannot fail is worse than no
> test, because it reads as coverage and stops anyone from looking.

**"What is DORA?"**
> Four standard delivery metrics. We report change lead time, deployment frequency, change
> failure rate and time to restore — measured across the two paths so the security gate's
> cost and benefit are both visible.

**"Did the chaos tests find anything real?"**
> Yes — the scanner cache was shared process-wide, so a stale cache hit looked exactly
> like a fresh scan. Three tests failed in the full suite while passing alone. That is now
> cleared around every test.
