# Week 1 Verification Log

Per-engineer record of what week 1 actually delivered, and how it was checked.

---

# Sorour

**Date:** 2026-08-01

## AWS live

- [x] S3 backend bucket: `theagentorg-shared-terraform-backend` (versioned)
- [x] `terraform apply` succeeded from `infra/Terraform/environments/shared/`
- ECR repos (5):
  - developer = `339712964409.dkr.ecr.us-east-1.amazonaws.com/theagentorg-shared-developer-agent`
  - planner = `339712964409.dkr.ecr.us-east-1.amazonaws.com/theagentorg-shared-planner-agent`
  - reviewer = `339712964409.dkr.ecr.us-east-1.amazonaws.com/theagentorg-shared-reviewer-agent`
  - security = `339712964409.dkr.ecr.us-east-1.amazonaws.com/theagentorg-shared-security-agent`
  - sre = `339712964409.dkr.ecr.us-east-1.amazonaws.com/theagentorg-shared-sre-agent`
- AgentCore runtime role ARN: `arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role`
- GitHub Actions OIDC role ARN: `arn:aws:iam::339712964409:role/github-actions-role`

> Note: the `github-actions-role` and its GitHub OIDC provider already existed
> in this AWS account (shared with other repos' CI), so `terraform apply`
> returned `EntityAlreadyExists` for those two resources. They are no longer
> Terraform-owned — `main.tf` now looks the role up via a
> `data "aws_iam_role" "github_actions"` data source rather than creating it
> (see commit `eb2f045`). The `repo:mohamedsorour1998/TheAgentOrg:*` trust
> subject and the `AmazonEC2ContainerRegistryFullAccess` +
> `AmazonBedrockFullAccess` policies were added to the existing role via the
> AWS CLI, leaving the role's other trust subjects untouched.

## Bedrock

- [x] `python scripts/bedrock_smoke_test.py` → `OK: Bedrock is reachable.`
      (reply: `Hello! How can I help you today?`)

## Pipeline still green

- [x] `pytest -q` → 3 passed
- [x] `python make_fixtures.py` → fixtures regenerate clean, block rule check
      `verdict=block, blocking findings=2`
- [x] `python -m agentorg.graph` → status=promoted (security verdict=pass, blocking=0)
- [x] `python -m agentorg.graph --poisoned` → status=blocked (security verdict=block, blocking=2)

## Handed off to Mariam

Sent her the AgentCore runtime role ARN and the `github-actions-role` ARN
(both above) — she needs them for `agentcore configure -er ...` and her CI
workflow's `role-to-assume` (see `docs/plan/mariam/week3.md`).

---

# Mariam

**Date:** 2026-08-15 · PR #2 `feature/github-ops` · plan: `docs/plan/mariam/week1.md`

## Delivered

- [x] `open_pr(state)` — real PyGithub: branches off `main`, commits the diff to
      `changes/<ticket_id>.diff`, opens the PR, sets `dev.pr_url`. Re-runs are
      idempotent (422 on an existing ref/PR is caught and reused).
- [x] `post_comment(state, body, finding=None)` — real PyGithub issue comment,
      returns the comment `html_url`. A `Finding` renders as a bold
      `[tool · severity] rule (file:line)` header above the body.
- [x] `PyGithub` moved into `pyproject.toml` dependencies.
- [x] `GITHUB_TOKEN` / `GITHUB_REPO` added to `agentorg/common/config.py`
      (add-only — no existing field renamed, contract intact).

## Target repo

Created and seeded on 2026-08-15: **<https://github.com/mohamedsorour1998/auth-service>**
— public, `main` default branch, holding the Flask app the agents modify
(`app/auth.py`, `tests/test_auth.py`, `requirements.txt`), seeded from
`target_repo/`. Shared by the whole team; this is the repo shown to the judges.
Opening PRs against it needs **write access**, so every engineer must be a
collaborator — a fork is not enough (`open_pr` creates a branch in the repo).

## Verified without credentials

- [x] `pytest -q` → 3 passed
- [x] `python -m agentorg.graph` → status=promoted
- [x] `python -m agentorg.graph --poisoned` → status=blocked, blocking=2
- [x] Branch convention `agent-org/<ticket_id>-<short_sha>`; `short_sha` is
      stable per diff (re-runs reuse the branch) and changes when the diff does.

## Verified against the live repo

With `GITHUB_TOKEN` + `DEMO_REPO` set, on `auth-service`:

- [x] `open_pr` → real PR
      <https://github.com/mohamedsorour1998/auth-service/pull/1>
      on branch `agent-org/DEMO-CLEAN-2add769`
- [x] `post_comment` → real comment, returns its `html_url`
      (`.../pull/1#issuecomment-5303845179`); the `Finding` variant renders the
      `[gitleaks · critical] aws-access-key-id (app/auth.py:4)` header
- [x] **clean** run → `status=promoted`, real PR
      <https://github.com/mohamedsorour1998/auth-service/pull/2>
- [x] **poisoned** run → `status=blocked`, real PR
      <https://github.com/mohamedsorour1998/auth-service/pull/3>,
      2 critical gitleaks findings (`aws-access-key-id` app/auth.py:4,
      `aws-secret-access-key` app/auth.py:5), and the block explanation posted
      back onto the PR as a comment

That last one is the whole demo, running against real GitHub: the pipeline
opened the PR, scanned it, blocked it, and explained why on the PR itself.

## Fixed during review (commit `d7f3c37`)

`open_pr`/`post_comment` constructed a PyGithub client unconditionally on the
online path, and `OFFLINE` defaults to `false`. PyGithub asserts on an empty
token, so **every run without credentials died inside the PR node** — CI went
3-failed, and so would any other lane's `python -m agentorg.graph`. The plan's
own sample code had this flaw, so it was inherited, not introduced.

`_use_local()` now takes the local path when `OFFLINE` is set **or** the
credentials are missing. With credentials present the real GitHub path is
unchanged. Also switched to `Auth.Token` (drops the PyGithub deprecation
warning) and fixed PEP8/EOF nits.

## Week 1 status

**Complete.** Every acceptance criterion in `docs/plan/mariam/week1.md` is met
and verified against real GitHub, not fixtures.

---

# Week 2

**Date:** 2026-08-19 · branch `worktree-week2-agents-and-ci` · HEAD `00f1997`
· plan: `.superpowers/sdd/2026-08-15-week2-agents-and-ci/` (task 11)

The claim under test is the one the demo is built on: **a poisoned ticket
carrying hardcoded AWS credentials blocks on every run, and the block comes
from `compute_security_verdict()` — pure Python in `state.py` — not from a
model's judgement.**

It holds with the model off — 30 credential-free runs, no deviation. **It does
not hold with a live model: 2 of 5 live poisoned runs blocked, 2 promoted and 1
failed.** The cause is found, is deterministic, is reproducible without a model,
and is *not* in the block rule — it is in `developer.py`'s poisoned safety net.
See "What did not hold".

## How this was measured

Every run below is a real `python -m agentorg.graph` subprocess with an
explicitly-constructed environment (nothing inherited from the shell), and every
number is read back from the run's own stdout or from its own artifacts in
`runs/`. Python 3.14.6.

**Scanner binaries — this decides whether the verdict is real.**
`security.run()` catches *any* scanner failure and falls back to
`fixtures_loader.security(block="AKIA" in diff)`. On that path the verdict comes
out of `fixtures/security_result_block.json` and **`compute_security_verdict()`
is never called at all**. Both paths block the poisoned ticket, but only one of
them is evidence for the claim, so they are reported separately throughout. The
fallback is detected by the WARNING `scanners failed (...)` that `security.run()`
prints to stderr.

- Present and used: `gitleaks 8.21.2`, `semgrep 1.172.0`, `trivy 0.74.0`.
- None of the three is on this machine's default `PATH`; they were put on it
  explicitly. **A run on the default PATH therefore takes the fixture
  fallback** — `FileNotFoundError: [Errno 2] No such file or directory:
  'semgrep'`.

**The instrument was checked before it was trusted** — four checks, each
required to produce the *unflattering* answer at least once:

| check | result |
|---|---|
| clean ticket must be reportable as not-blocked | `status=promoted`, `blocking=0` |
| fallback detector must fire when scanners are missing | fired, cause `FileNotFoundError: 'semgrep'` |
| fallback detector must stay quiet when they are present | quiet |
| a genuinely failing run must be reported as failing | `rc=1`, `status=None` (`OFFLINE_REPO` pointed at a repo offline mode did not create) |

**What the real scanners actually produce** on the poisoned diff — 3 findings,
trivy contributing none:

```
semgrep   low       agentorg.security.python.flask.missing-timeout   app/auth.py:6
gitleaks  critical  aws-access-key-id                                app/auth.py:3
gitleaks  critical  aws-secret-access-key                            app/auth.py:4
```

and `compute_security_verdict` over exactly those findings is threshold-
sensitive, which is what shows it is being consulted rather than rubber-stamping:
`low → block/3`, `medium → block/2`, `high → block/2`, `critical → block/2`.
On the clean diff: 0 findings → `pass/0`. On an empty list → `pass/0`, which is
why a scanner failure must never degrade to `[]`.

## Result 1 — poisoned, no model, 10 runs

`LLM_DISABLED=true`, no `GITHUB_TOKEN`, no `DEMO_REPO`. Run twice, once each way:

- [x] **scanners on PATH — 10/10 `status=blocked`, `blocking=2`, fixture
      fallback 0/10.** The verdict came from `compute_security_verdict()` over
      real gitleaks findings on all ten. 1.5–3.3 s per run.
- [x] **scanners absent (the machine's default PATH) — 10/10 `status=blocked`,
      `blocking=2`, fixture fallback 10/10.** Still blocked ten out of ten, but
      by the fixture, not by the block rule. 0.25–0.4 s per run.

Ten out of ten in both sets. Twenty runs, no deviation.

## Result 2 — poisoned, OFFLINE, 10 runs

`OFFLINE=true LLM_DISABLED=true`, scanners on PATH, after `rm -rf runs/offline-demo`.

- [x] **10/10 `status=blocked`, `blocking=2`, fixture fallback 0/10.**
- [x] `grep -c '^## DEMO-POISON' runs/offline-demo/NOTES.md` → **10**. The count
      was taken after *every* run, not only at the end, and went
      `1,2,3,4,5,6,7,8,9,10` — a counter that could only ever say "10" would
      have been visible as one.
- [x] All 10 NOTES entries carry a real block reason (`grep -c 'Blocked:'` → 10).
- [x] The offline workspace did real git work: branch
      `agent-org/DEMO-POISON-6dab07b`, re-created by `checkout -B` on each run.

## Result 3 — poisoned, LIVE model + LIVE GitHub, 5 runs — **2 of 5 blocked**

Real Bedrock (`us.amazon.nova-2-lite-v1:0`, `us-east-1`) and real PRs on
`mohamedsorour1998/auth-service`. Scanners on PATH; fixture fallback 0/5, so
every one of these verdicts is a real-scanner verdict.

| run | status | verdict | branch | PR | block comment |
|---|---|---|---|---|---|
| 1 | **promoted** | pass/0 | `agent-org/DEMO-POISON-9bddc49` | [#4](https://github.com/mohamedsorour1998/auth-service/pull/4) | — |
| 2 | blocked | block/2 | `agent-org/DEMO-POISON-6dab07b` | [#5](https://github.com/mohamedsorour1998/auth-service/pull/5) | [`issuecomment-5334081748`](https://github.com/mohamedsorour1998/auth-service/pull/5#issuecomment-5334081748) |
| 3 | blocked | block/2 | `agent-org/DEMO-POISON-6dab07b` | [#5](https://github.com/mohamedsorour1998/auth-service/pull/5) | [`issuecomment-5334088124`](https://github.com/mohamedsorour1998/auth-service/pull/5#issuecomment-5334088124) |
| 4 | **promoted** | pass/0 | `agent-org/DEMO-POISON-ac25aaa` | [#6](https://github.com/mohamedsorour1998/auth-service/pull/6) | — |
| 5 | **failed** | pass/0 | `agent-org/DEMO-POISON-771a82a` | [#7](https://github.com/mohamedsorour1998/auth-service/pull/7) | — |

Verified on GitHub afterwards: PR #5 carries exactly 2 comments; PRs #4, #6 and
#7 carry 0.

Runs 2 and 3 share a branch and a PR because the branch name is
`agent-org/<ticket>-<sha1(diff)[:7]>` and both landed on the same reference
diff — so five live runs produce four PRs, not five, and #5 collects two
comments. That part is by design.

Run 5 ended `failed` rather than `promoted` only because the reviewer never
approved within the 3-revision cap — the scanners had already cleared it. That
is a weaker stop than the block rule and depends on a model's opinion.

## Result 4 — clean ticket, LIVE — promoted

- [x] `status=promoted`, `security verdict=pass, blocking=0`, no fixture
      fallback, all three gates approved, real PR
      **<https://github.com/mohamedsorour1998/auth-service/pull/8>**
      on `agent-org/DEMO-CLEAN-7a5e7ea`. The model wrote a 1900-byte diff over 6
      files, no `AKIA` anywhere, and the real scanners found nothing.

## What did not hold

**1. The poisoned safety net checks the wrong side of the diff. (blocker)**

`agentorg/agents/developer.py`:

```python
if poisoned and not _AWS_KEY.search(dev.diff):
    ...substitute the reference poisoned diff...
```

`_AWS_KEY.search(dev.diff)` runs over the **whole diff text, removal lines
included**. By the second loop iteration the reviewer has (correctly) told the
developer to remove the hardcoded credentials, so the model returns a diff whose
only `AKIA...` occurrence is on a `-` line. The safety net reads that as "the key
is present", declines to substitute, and the change reaches the scanners with no
secret in it — the three wrappers materialise only **added** lines.
`compute_security_verdict([])` is then correctly `pass`. **The block rule is not
at fault; it was handed nothing to block.**

All three non-blocking live runs match that shape exactly: each carried
`AKIAIOSFODNN7EXAMPLE` on a removal line and nowhere else.

Reproduced deterministically with no model at all (a stub developer returning a
credential-*removing* diff): safety net fires? `False` · `AKIA` in the diff text?
`True` · `AKIA` on an added line? `False` · real scanners → 0 findings →
`compute_security_verdict → pass, blocking=0`.

Whether a live-model poisoned run blocks is therefore decided by what the model
happened to write on its **last** revision. Runs 2 and 3 blocked because that
last diff contained no `AKIA` at all, so the safety net did fire.

Not fixed here: task 11 is verification, `state.py` is frozen, and this is a
change to `developer.py`. The shape of the fix is to search only added lines
rather than the whole diff string. It needs its own task and its own test.

**2. The block explanation is not byte-stable.** Across the 10 offline runs, 6
explanations led with `aws-secret-access-key` and 4 with `aws-access-key-id` —
gitleaks does not order its JSON report deterministically. The verdict, the
finding count and the finding *set* were identical on all 10; only the sentence
order moved. Cosmetic, but the demo puts this string on a projector.

**3. `semgrep_tool` leaks the scratch path into `Finding.file`.** Semgrep
findings come back as
`/var/folders/.../agentorg-semgrep-3dj53n5a/app/auth.py`, where gitleaks findings
correctly say `app/auth.py` — `gitleaks_tool` has a `_repo_relative()` helper and
`semgrep_tool` has no equivalent. Harmless today because no semgrep rule in the
set is above `low`, so one never reaches a PR comment. Habiba's lane.

**4. Line numbers differ from the fixture by one.** Real gitleaks reports
`app/auth.py:3` and `:4`; `fixtures/security_result_block.json` says `:4` and
`:5`. The scanners materialise only added lines, so their numbering is not the
post-merge file's. Cosmetic; the fixture is the stale one.

## Live spend

- **65 Bedrock invocations** of `us.amazon.nova-2-lite-v1:0`: 46 across the five
  live poisoned runs, 8 for the live clean run, 10 for a call-count validation
  run, 1 for `scripts/bedrock_smoke_test.py`. Counted as
  `1 planner + 2 per develop/review iteration + 1 security explainer`, with the
  iteration count read from each run's own decision log; the formula was checked
  against a counter placed on `llm._complete` (derived 10, counted 10), and that
  counter was itself checked against `LLM_DISABLED=true`, where it reported 0.
  Botocore-level retries, if any, are not included.
- **5 pull requests opened** on `mohamedsorour1998/auth-service`: #4, #5, #6, #7
  (poisoned) and #8 (clean). **2 PR comments** posted, both on #5.

## Suite

- [x] `pytest -q` → **90 passed** (5.2 s, real git)
- [x] `ruff check agentorg scripts tests` → exit 0
- [x] No production code was modified by this task; `git status` is clean apart
      from this file.

## Week 2 status

**The Friday gate is met offline and not met live.** `OFFLINE=true
LLM_DISABLED=true python -m agentorg.graph --poisoned` blocks 10 out of 10 and
is safe to demo on 25 Aug. The live-model poisoned path must not be demoed until
the `developer.py` safety net is fixed and this section is re-run.
