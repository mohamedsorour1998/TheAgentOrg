# Reem — Week 1 (Aug 8–14): the inputs + first correctness tests

Starter versions exist (`target_repo/app/auth.py`, `tickets/clean.md`,
`tickets/poisoned.md`, `tests/test_pipeline_smoke.py`). Finish them and add
yours. Nothing here needs AWS.

---

## Sat Aug 8 — kickoff (with everyone)

**Task: attend the 90-minute kickoff.** Agree `state.py`, the log table, the
poisoned flaw (hardcoded AWS key — yours to write), and directory ownership.
**Done when:** `pip install -e ".[dev]" && pytest -q` is green on your machine.

---

## Sun–Mon Aug 9–10 — finish the target app

**Task: finish `target_repo/app/auth.py`.** Keep it tiny but real — a Flask
login handler with a couple of functions worth changing (the kind of thing a
"add a per-IP login rate limit" ticket would touch).
**Done when:** `python -m pytest target_repo/tests` passes.

---

## Tue Aug 11 — write the clean ticket

**Task: write `tickets/clean.md`.** "Add a per-IP login rate limit" — clear
description + explicit acceptance criteria (e.g. "6th failed attempt from the
same IP within 60s returns 429").
**Done when:** a developer could implement it from the ticket text alone, no
extra context needed.

---

## Wed Aug 12 — the poisoned ticket + the handoff

**Task: write `tickets/poisoned.md`.** Same feature as the clean ticket, but
the attached reference diff hardcodes an AWS key
(`AKIAIOSFODNN7EXAMPLE` — AWS's public example placeholder, nothing
sensitive).
**Done when:** the poisoned reference diff, scanned on its own with
`gitleaks detect --no-git`, trips the scanner.

**★ Task: hand the poisoned ticket to Habiba today.** She needs a diff that
actually trips her scanner — this is the team's single cross-dependency.
```bash
gitleaks detect --no-git --source tickets/poisoned.md
```
**Done when:** Habiba confirms gitleaks flags the AWS key on your actual
ticket (not just a fixture).

---

## Thu–Fri Aug 13–14 — contract test

**Task: write `tests/test_functional_contract.py`.**
For each agent result type, assert it validates against `state.py` and the
values are sane:
- `PlanResult` has non-empty `tasks`
- `DevResult` has a diff + touched files
- `ReviewResult` has a verdict in the allowed set
- `SecurityResult` findings match `compute_security_verdict`'s output
- `SREResult` has an SLO check result

The smoke test (`tests/test_pipeline_smoke.py`) shows the shape to copy.
**Done when:** all five result types are covered; a deliberately malformed
fixture fails the test (prove the test actually catches something).
**You're unblocked because:** the stubbed pipeline already runs — you assert
on the frozen contract, not on anyone's real code.

---

## End of week 1 — done when

- Both tickets exist and are realistic.
- The poisoned ticket is confirmed by Habiba to trip gitleaks.
- `tests/test_functional_contract.py` covers all five result types and fails
  on malformed output.
