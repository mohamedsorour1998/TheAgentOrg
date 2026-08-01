# Mariam — Week 2 (Aug 15–21): CI + offline mode

---

## Mon–Tue Aug 15–16 — CI workflow

**Task: flesh out `.github/workflows/ci.yml`.**
It already installs deps, regenerates fixtures, and runs pytest. Add:
- a lint step (`ruff` or whatever the repo already uses);
- a job that runs Habiba's scanners (`run_all_scanners`) on the PR diff.

**Done when:** every PR shows a green (or red) CI check automatically.

---

## Wed–Thu Aug 17–18 — offline mode

**Task: make `open_pr` and `post_comment` work with no network**
(`config.OFFLINE == "true"`).
- Branch + commit against a **local git repo** instead of GitHub.
- Write "comments" to a local NOTES file instead of the GitHub API.
```bash
OFFLINE=true python -m agentorg.graph
```
**Done when:** the command above runs correctly with wifi off.
**Why it matters:** the live demo must not depend on the venue's network —
this is your insurance policy for Aug 27+.

---

## Fri Aug 19–21 — block explanation on the PR

**Task: post the security finding to the PR.**
When the graph blocks, your `post_comment` writes the explanation onto the
PR. Pair with Sorour on the exact call site in `graph.py` — you want the
comment posted right after `compute_security_verdict()` returns `block`.
**Done when:** a blocked run leaves a visible "blocked: hardcoded AWS key"
comment on the PR (or the offline NOTES file, in offline mode).

**Cross-check:** this is also the week Sorour's poisoned ticket must block
every time (his Friday deadline) — your comment should appear on every one
of those blocked runs, so test alongside his Friday verification.

---

## End of week 2 — done when

- Every PR shows a CI check (lint + tests + Habiba's scanners).
- `OFFLINE=true python -m agentorg.graph` runs correctly with no network.
- A blocked run visibly posts the block reason to the PR (online) or the
  NOTES file (offline).
