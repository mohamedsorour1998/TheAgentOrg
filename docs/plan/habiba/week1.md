# Habiba — Week 1 (Aug 8–14): scanners running by hand, then wrapped

Each tool file has a working stub and a `# TODO(Habiba)` showing what to
build. Nothing here needs AWS or anyone else's real code.

---

## Sat Aug 8 — kickoff (with everyone)

**Task: attend the 90-minute kickoff.** Agree `state.py`, the log table, the
poisoned flaw (hardcoded AWS key), and directory ownership.
**Done when:** `pip install -e ".[dev]" && pytest -q` is green on your machine.

---

## Sun–Mon Aug 9–10 — install + run by hand

**Task: install and run all three scanners by hand** on one bad file
containing a hardcoded AWS key.
```bash
semgrep --config auto path/to/bad_file.py
gitleaks detect --no-git --source path/to/bad_file.py
trivy fs path/to/bad_file.py
```
**Done when:** each tool prints findings on the file. This is your baseline
for "what does a real finding look like" before you write any wrapper code.

---

## Tue–Wed Aug 11–12 — gitleaks (do this first, it's the demo)

**Task: implement `gitleaks_tool.scan(dev)`.**
- Write `dev.diff` to a temp dir.
- Run `gitleaks detect --no-git --report-format json`.
- Parse the JSON, map each leak to a `Finding(severity="critical", ...)`.
```python
def scan(dev: DevResult) -> list[Finding]:
    ...
```
**Done when:** the poisoned diff yields 2 critical findings; the clean diff
yields 0.
**You're unblocked because:** `DevResult` and `Finding` already exist in
`state.py`. Load `fixtures/dev_result_poisoned.json` and
`fixtures/dev_result_clean.json` to test — you don't need Sorour's real
developer agent.

**★ Task: confirm the real ticket trips gitleaks, with Reem, by Wed Aug 12.**
Run gitleaks on her actual poisoned ticket's diff, on its own — not the
fixture.
**Done when:** gitleaks reports the AWS keys on her real ticket.
**This is the team's one cross-dependency** — flag it early to the group if
her ticket slips past Wednesday.

---

## Thu–Fri Aug 13–14 — semgrep

**Task: implement `semgrep_tool.scan(dev)`.**
Run `semgrep --json` over the diff, map results, translate semgrep severity
into our `Severity` enum.
**Done when:** it returns at least one low/medium finding on the demo diff
(semgrep should catch something beyond just the hardcoded key).

---

## End of week 1 — done when

- `gitleaks_tool.scan()` returns 2 critical findings on the poisoned fixture,
  0 on clean.
- `semgrep_tool.scan()` returns real findings, not a stub.
- Reem's actual poisoned ticket (not just the fixture) is confirmed to trip
  gitleaks.
- `run_all_scanners(dev)` (combining both) returns real findings from real
  tools.
