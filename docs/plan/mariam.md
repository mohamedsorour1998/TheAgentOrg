# Plan — Mariam

**Your lane:** the integration seam between Git/GitHub and the pipeline.
`agentorg/github_ops.py` and `.github/workflows/` (CI), plus you co-own the
AgentCore deploy with Sorour in week 3.

Your code is what connects the graph to the outside world — every run opens a PR
and posts comments through you. It plugs directly into Sorour's graph, so you two
coordinate often — but it's fully stubbed today, so you can **never** block him.

You don't need AWS for weeks 1–2. Everything you build runs against a throwaway
GitHub repo and local git.

---

## Week 1 — Aug 8 to 14: get the seam working on a real repo

Open `agentorg/github_ops.py` — it has three functions with frozen signatures
and `# TODO(Mariam)` markers showing exactly what to fill in.

- [ ] **Create a throwaway GitHub repo** to test against (e.g. `demo-app`).
  *Done when:* you can push to it and open a PR by hand.

- [ ] **Implement `open_pr(state)`** with PyGithub: create a branch, commit the
  diff from `state.dev.diff`, open a PR, set `dev.pr_url` to the real URL.
  *Done when:* calling it opens a real PR and returns its URL.
  *You're unblocked because:* it takes a `RunState` (already exists) and returns
  a `DevResult` (already defined). No dependency on anyone's real code.

- [ ] **Implement `post_comment(state, body, finding=None)`** with PyGithub.
  *Done when:* a comment appears on the PR; returns a ref string.

*End of week 1:* Sorour's graph, unchanged, opens a real PR when it reaches your
node. (Test: run `python -m agentorg.graph` with your code in place.)

---

## Week 2 — Aug 15 to 21: CI + offline mode

- [ ] **Flesh out `.github/workflows/ci.yml`.** It already installs, regenerates
  fixtures, and runs pytest. Add: lint, and a job that runs Habiba's scanners on
  the PR diff.
  *Done when:* every PR shows a green (or red) CI check automatically.

- [ ] **Offline mode** (`config.OFFLINE == "true"`): make `open_pr` and
  `post_comment` work against a **local git repo with no network** — branch,
  commit, and write comments to a local NOTES file.
  *Done when:* `OFFLINE=true python -m agentorg.graph` runs with wifi off.
  *Why it matters:* the live demo must not depend on the venue's network.

- [ ] **Post the security finding to the PR.** When the graph blocks, your
  `post_comment` writes the explanation onto the PR. Pair with Sorour on the
  exact call site in `graph.py`.
  *Done when:* a blocked run leaves a visible "blocked: hardcoded AWS key"
  comment on the PR.

*End of week 2:* real PRs online, and the whole thing also runs offline.

---

## Week 3 — Aug 22 to 27: deploy + hand off

- [ ] **AgentCore deploy, with Sorour.** Build the arm64 agent images, push to the
  ECR repos his Terraform created, create the AgentCore runtimes.
  *Done when:* the graph runs against AgentCore-hosted agents.
  *You're unblocked because:* Sorour's `terraform apply` (week 1) already made
  the ECR repos + IAM role; you're pushing into them.

- [ ] **Prove the offline demo end-to-end.** Run the full clean + poisoned demo
  with the network off, twice.
  *Done when:* both runs behave identically to online.

- [ ] **After freeze (Tue Aug 25):** fix only what the dry runs find. No new work.

---

## Where you plug into Sorour

He calls your two functions from `graph.py`:

```python
state.dev = github_ops.open_pr(state)          # you return DevResult w/ pr_url
github_ops.post_comment(state, explanation)     # you post the block reason
```

The signatures are frozen in the stub, so his graph runs whether your insides are
stubs or real. Coordinate with him on: what fields the PR node reads from
`DevResult`, and the AgentCore deploy in week 3. Ask him early what he expects the
PR comment to contain — cheaper to agree than to redo.
