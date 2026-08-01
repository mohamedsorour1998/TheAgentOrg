# Habiba — Week 2 (Aug 15–21): make the block deterministic

Hard deadline this week (shared with Sorour): **by end of Friday Aug 21 the
poisoned ticket blocks every time on real scanners.**

---

## Mon–Tue Aug 15–16 — trivy

**Task: implement `trivy_tool.scan(dev)`.**
`trivy fs --format json` over changed files / `requirements.txt`; map
vulnerabilities to `Finding`.
**Done when:** a diff that adds a known-vulnerable dependency produces a
finding (add a deliberately old package version to a test fixture to prove
it).

---

## Wed Aug 17 — wire into the security agent

**Task: pair with Sorour to wire `run_all_scanners` into his security agent.**
He calls it and applies `compute_security_verdict()`; verify the poisoned run
blocks on **your real findings**, not the stub.
```bash
python -m agentorg.graph --poisoned
```
**Done when:** the command above blocks using your scanners' output, visible
in the log (not the fixture path).

---

## Thu–Fri Aug 18–21 — the deadline

**Task: repeat-run to prove determinism.**
```bash
for i in $(seq 1 10); do python -m agentorg.graph --poisoned; done
```
**Done when:** all 10 runs block with the same 2+ critical findings from
gitleaks (plus whatever semgrep/trivy add).

**★ Hard deadline: by end of Friday Aug 21, the poisoned ticket blocks every
single time on your real scanners.** This is the demo — if it's flaky, drop
everything else until it isn't.

---

## End of week 2 — done when

- All three scanners (`gitleaks`, `semgrep`, `trivy`) return real findings.
- `python -m agentorg.graph --poisoned` blocks on real findings, every run,
  10/10.
