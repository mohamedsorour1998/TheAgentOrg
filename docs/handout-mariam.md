# Handout — Mariam · the GitHub seam and the deploy

**Your lane:** `agentorg/github_ops.py` and `.github/workflows/`, plus the AgentCore
deploy with Sorour.
**Your line:** *"Everything a judge can see, my code wrote."*

---

## Your three weeks, in one minute

**Week 1 — the pipeline reaches GitHub.** Target repo access and a token, then real
`open_pr` and `post_comment` with PyGithub. Ran the graph end to end for the first time
against a real repository.

**Week 2 — CI, and a path that needs no network.** `ci.yml` (lint + scanner job), then
OFFLINE mode: real local git and a NOTES file, so the whole pipeline runs with no
network at all. Then the block explanation posting to the PR.

**Week 3 — the deploy.** All five agents onto AgentCore, and a GitHub Actions deploy job
through OIDC with **zero static AWS keys**.

---

## What you built, and the two decisions to name

`github_ops.py` is **1,132 lines** and holds every outbound write: branch, commit, PR,
nine stage comments, the merge, and the issue's closing verdict.

### `post_comment` returns a ref in every case and never raises

> That is a hard requirement, not politeness. `graph.py` sets `status="blocked"` and on
> the very next line records the ref my function returns. The block is the product; the
> comment is only how a human learns why. A comment that cannot be delivered must not be
> able to turn a correctly-blocked run into a traceback — on stage, in front of judges.

Four ref shapes, and the distinction is deliberate: `https://` delivered, `local://`
written to disk offline, `comment://` **not** delivered.

> A `local://` ref on a run whose bytes never reached disk would be the artifact claiming
> a delivery that did not happen. So the ref is only returned after the write succeeds.

### The issue is a complete record — the newest work

The PR body carries `Closes #<n>`, which is what populates GitHub's Development sidebar.
When the run ends, the issue gets a verdict comment and closes itself: `completed` for a
merged change, `not_planned` for a refused one.

> Before this, an issue learned only how a run *began* — the plan and the gate decision.
> Everything after landed on the pull request, so the issue that asked for the work
> stayed open forever with no statement of what happened. On a poisoned run that is the
> worst case: the block is the whole point and the issue never learned it was refused.

**In the demo, that is the closing slide:** two issues side by side, one `COMPLETED` with
a merged PR, one `NOT_PLANNED` with the PR left open.

---

## Your numbers

| | |
|---|---|
| `github_ops.py` | 1,132 lines, the only module that writes to GitHub |
| `tests/test_deploy_workflow.py` | **108 tests** — the two files that can spend money |
| `tests/test_agent_comments.py` | 19 — one labelled comment per stage, issue vs PR |
| `tests/test_offline_mode.py` | 25 — a real local branch, no network |
| `tests/test_issue_lifecycle.py` | 13 — the PR link, the verdict, the auto-close |
| workflows | `ci.yml` · `deploy.yml` · `run-pipeline.yml` · `terraform.yml` |
| static AWS keys | **zero** — OIDC everywhere |

---

## If asked

**"How do you know the deploy is reproducible?"**
> It runs from a workflow on every push to main, not from anyone's laptop. Five ECR tags
> from one arm64 image differing only by `AGENT_ROLE` — five images would multiply build
> time for no difference in content and leave five Dockerfiles to drift apart.

**"Why arm64?"**
> AgentCore runs arm64. An amd64 image pushes and deploys and then fails to start with an
> exec format error that reads like a broken entrypoint. We paid for that once.

**"What if GitHub is down mid-demo?"**
> OFFLINE mode does real local git — branch, commit, and a NOTES file recording every
> comment and which surface it was meant for. The pipeline completes; nothing is faked.

**"Does the token have more access than it needs?"**
> It is scoped to two repositories: contents, issues and pull requests on the target, and
> `actions:write` here. Narrowed to either one alone, the other half fails silently —
> which we found from a dead-letter queue message, not from a red build.
