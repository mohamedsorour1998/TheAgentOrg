# Handout — Habiba · the security scanners

**Your lane:** `agentorg/security/` — the three scanner wrappers and the fan-out.
**Your line:** *"My findings are what block the poisoned ticket."*

---

## Your three weeks, in one minute

**Week 1 — make one scanner real.** Installed gitleaks, trivy and semgrep, ran them by
hand on a bad file, then wrote `gitleaks_tool.scan()` first *because it is the demo* —
it is the tool that finds the AWS key.

**Week 2 — the other two, then wire it in.** `trivy_tool.scan()`, `semgrep_tool.scan()`,
then `run_all_scanners` into the security agent. Proved determinism: the same diff gives
the same findings every time.

**Week 3 — make it fail safely.** The part that took the longest and matters most: what
happens when a scanner is *absent* versus *broken*. Plus a cache so the fan-out does not
re-scan the same diff.

---

## What you built, and the one design decision to name

You produce `Finding` objects. **You do not decide pass/block** — that is
`compute_security_verdict()`, five lines of pure Python in `state.py`.

> I deliberately do not return a verdict. My job is evidence; the decision is a severity
> comparison with no model in it. That separation is why a prompt-injected diff cannot
> talk its way past the gate.

### Absent vs broken — the thing to be proud of

Two different faults that must not get the same answer:

| situation | answer |
|---|---|
| binary **not installed** (a dev machine) | fall back to the fixture, and *say so* via `provenance` |
| binary **installed but broken** (bad shebang, lost `+x`) | a blocking finding, severity `high` |

The classifier is a **conjunction**: absent means `FileNotFoundError` **and**
`shutil.which()` finds nothing.

> Either signal alone misclassifies real cases, and always in the fail-open direction.
> `which` misreads a lost execute bit as absent. The exception type misreads a broken
> shebang as absent, because errno 2 names the missing *interpreter*, not the tool.

And the rule that protects everything: `compute_security_verdict([])` returns **pass**.

> So a scanner failure must never become an empty findings list. It raises instead. An
> empty list is one careless `return` away from sending a poisoned change green with the
> whole test suite still passing — and that `return` would look like correct code in
> review.

---

## Your numbers

| | |
|---|---|
| `tests/test_scanner_resilience.py` | **82 tests** — the absent/broken matrix |
| `tests/test_chaos_scanner.py` | 5 — broken scanners from outside the pipeline |
| `tests/test_provenance.py` | 7 — the discriminator itself |
| scanners | gitleaks · trivy · semgrep, all real binaries in the container |

**In the demo:** the poisoned run's `BLOCK` comment, `2 blocking finding(s)`,
`app/auth.py:3` and `:4`.

> Those line numbers are mine and they are the proof. The fixture reports lines 4 and 5.
> Real gitleaks reports 3 and 4. That pair is the only field that distinguishes a real
> scan from a canned answer — everything else is identical between the two.

---

## If asked

**"Could the scanners miss something?"**
> Yes. They catch credentials, known CVEs and injectable patterns — not logic bugs. What
> they miss falls to the reviewer, whose verdict is advisory, and then to three human
> gates. I would rather state that limit than overclaim.

**"Why is a dead scanner `high` and not `critical`?"**
> `high` is exactly the block threshold, so a dead scanner still stops the run. Not
> `critical`, because a tooling fault should not impersonate a discovered secret in a
> list a human is reading.

**"Does the scan slow the pipeline down?"**
> The fan-out is cached by diff hash, so the same diff is scanned once. The whole
> security stage is part of a ~60-second job.
