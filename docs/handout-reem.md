# Handout — Reem · the subject app, the tickets, and correctness

**Your lane:** `target_repo/`, `tickets/`, the functional-contract and baseline tests, and
the demo script.
**Your line:** *"I built the thing the agents edit, and the two tickets that make the demo
a comparison instead of a claim."*

---

## Your three weeks, in one minute

**Week 1 — the subject.** Finished the target app (a small Flask login handler), then
wrote the clean ticket. This is what the agents actually read and patch.

**Week 2 — the "before" picture and correctness.** Built the no-checks baseline — plan,
develop, merge, with no review, no security, no gates — then the happy-path and
revision-loop tests, then CI hookup.

**Week 3 — the script.** Wrote the demo script, beat by beat, with the spoken line, the
exact command, and the verified on-screen result for each.

---

## What you built, and the two decisions to name

### The two tickets are the same feature request

`tickets/clean.md` and `tickets/poisoned.md` ask for the identical thing. They differ in
one respect: the poisoned one's reference implementation hardcodes AWS credentials.

> That is what makes the demo a controlled comparison. Same request, same five agents,
> same gates — one ships and one is refused. If the two tickets asked for different
> things, the block would prove nothing about the pipeline; it would just be two
> unrelated runs.

The credential is `AKIAIOSFODNN7EXAMPLE` — **AWS's own published documentation
placeholder**.

> Nothing sensitive is anywhere in this repository. It is a real-shaped key that real
> scanners genuinely detect, which is exactly what we need and nothing more.

### The baseline is what makes the numbers mean something

`run_baseline()` is the same pipeline with the checks removed.

> Without a "before", "we blocked it" is a claim about a system nobody can compare
> anything to. The baseline ships the credential every single time — and every job is
> green while it does. That is the failure mode this whole project exists to prevent:
> not a red build, a *silent* success.

Aya's metrics consume this directly — it is the other half of the 10-vs-10 table.

### The functional contract

Every result an agent produces is validated against the frozen schema in `state.py`.

> An agent that returns something plausible but structurally wrong is the failure that
> would surface as a crash three stages later, in the stage that did nothing wrong. These
> tests catch it at the boundary.

---

## Your numbers

| | |
|---|---|
| `target_repo/app/auth.py` | the file every agent reads and patches |
| `target_repo/tests/test_auth.py` | the app's own tests, run by its CI |
| `tickets/clean.md` · `tickets/poisoned.md` | the same request, one poisoned |
| `tests/test_functional_contract.py` | 9 — every result matches the frozen schema |
| `tests/test_baseline.py` | 3 — the no-checks "before" |
| `tests/test_pipeline_smoke.py` | 3 — the stubbed pipeline end to end |

**The deployed copy** of the target app is `mohamedsorour1998/auth-service`, and it had
**no CI at all** until this month — GitHub reported `pending` with zero checks.

> Which is its own lesson: zero checks must read as *unknown*, never as *passing*. A green
> CI line for a repository that has never run a test would be a fabricated fact.

---

## If asked

**"Is the target app realistic?"**
> It is deliberately small — a `login()` view reading `request.form`, an `authenticate()`
> helper, a `create_app()` factory. Small enough to read on a projector, real enough that
> a Flask developer recognises it. The pipeline does not care about size; it cares that
> the agents are patching a real file with real contents.

**"Why does the ticket wording matter so much?"**
> Because the reviewer is a real model and it withholds approval when the diff does not
> match what was asked. We measured a run where the reviewer wanted email-based rate
> limiting, the developer produced IP-based, and the run correctly ended `failed` with the
> scanners reporting PASS. Nobody approved it. So the demo ticket is specific enough to be
> satisfiable — that is a property of the ticket, not a workaround.

**"Did you write the diff the developer produces?"**
> No. The clean diff is model-written every run — you can see it change between runs. Only
> the *poisoned* reference diff is fixed, so the block is deterministic.
