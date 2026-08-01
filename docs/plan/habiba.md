# Plan — Habiba

**Your lane:** the security scanners. `agentorg/security/` — three wrappers
(`semgrep_tool.py`, `gitleaks_tool.py`, `trivy_tool.py`) and `run_all_scanners`
in `__init__.py`.

Your lane is the most self-contained on the team: it depends only on `state.py`
and the scanner CLIs, and never imports the graph. You can build and test it
completely on your own. **It is also the most important — your findings are what
block the poisoned ticket.**

You do NOT decide pass/block. You only produce `Finding` objects. The verdict is
computed by `compute_security_verdict()` in `state.py` (pure code). That
separation is what makes the demo deterministic — good answer when a judge asks
"how do you know it isn't the model guessing?"

---

## Week 1 — Aug 8 to 14: get the scanners running by hand, then wrap them

Each tool file has a working stub and a `# TODO(Habiba)` showing what to build.

- [ ] **Install and run all three by hand** on one bad file:
  `semgrep --config auto`, `gitleaks detect`, `trivy fs`.
  *Done when:* each prints findings on a file with a hardcoded AWS key.

- [ ] **Implement `gitleaks_tool.scan(dev)`** (do this one first — it's what
  blocks the demo): write the diff to a temp dir, run
  `gitleaks detect --no-git --report-format json`, parse the JSON, map each leak
  to a `Finding` with severity `critical`.
  *Done when:* the poisoned diff yields 2 critical findings; the clean diff
  yields 0.
  *You're unblocked because:* `DevResult` and `Finding` already exist in
  `state.py`. You don't need any teammate's real code — load
  `fixtures/dev_result_poisoned.json` to test.

- [ ] **Implement `semgrep_tool.scan(dev)`**: run `semgrep --json`, map results,
  translate semgrep severity → our `Severity`.
  *Done when:* it returns a low/medium finding on the demo diff.

*End of week 1:* `run_all_scanners(dev)` returns real findings from real tools.

---

## Week 2 — Aug 15 to 21: make the block deterministic

- [ ] **Implement `trivy_tool.scan(dev)`**: `trivy fs --format json` over changed
  files / requirements; map vulnerabilities to `Finding`.
  *Done when:* a diff adding a known-vulnerable dependency produces a finding.

- [ ] **Confirm the real ticket trips gitleaks** — with Reem, by **Wed Aug 12**
  (start of week 2). Run gitleaks on her poisoned ticket's diff on its own.
  *Done when:* gitleaks reports the AWS keys on her actual ticket, not just the
  fixture.
  *This is the team's one cross-dependency* — flag it early if her ticket slips.

- [ ] **Wire into Sorour's security agent.** He calls `run_all_scanners`; verify
  the poisoned run blocks on your **real** findings.
  *Done when:* `python -m agentorg.graph --poisoned` blocks with your scanners,
  not the stub.

*End of Friday Aug 21:* the poisoned ticket blocks **every** run on real
scanners. This is the hard deadline the whole demo rests on.

---

## Week 3 — Aug 22 to 27: harden + hand off

- [ ] **Handle the edge cases:** scanner missing, scanner times out, scanner
  returns malformed JSON — fail safe (surface an error finding, don't crash the
  graph).
  *Done when:* killing a scanner mid-run doesn't take down the pipeline (pair
  with Aya's chaos test).

- [ ] **Speed:** cache scanner results for the demo diffs so the live run is fast.
  *Done when:* a demo run returns findings in under a second.

- [ ] **After freeze (Tue Aug 25):** fix only what dry runs find.

---

## How you stay unblocked

You test entirely against `fixtures/dev_result_poisoned.json` and
`fixtures/dev_result_clean.json` — no need for Sorour's developer agent or
Mariam's PRs to exist. The only thing you wait on is Reem's real ticket (Aug 12),
and even then the fixture keeps you moving.
