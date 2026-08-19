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

**Date:** 2026-08-19 · branch `worktree-week2-agents-and-ci`
· plan: `.superpowers/sdd/2026-08-15-week2-agents-and-ci/` (task 11)

**Read this section in two layers.** Results 1–4 and "What did not hold" were
written at HEAD `00f1997` and are kept as the record of what was measured then,
including the failure they found. Two commits landed after them — `2d7913c` and
`b95cedb` — which fixed that failure, and **Result 5** is the live re-measurement
on the fixed code. Statements the fix overtook are marked `[SUPERSEDED at
b95cedb]` where they stand rather than rewritten, so the sequence
measure → diagnose → fix → re-measure stays legible.

The claim under test is the one the demo is built on: **a poisoned ticket
carrying hardcoded AWS credentials blocks on every run, and the block comes
from `compute_security_verdict()` — pure Python in `state.py` — not from a
model's judgement.**

**At HEAD `b95cedb` it holds on every configuration measured: 5 of 5 live
poisoned runs blocked (Result 5), 10 of 10 with no model, 10 of 10 offline, and
the live clean control still promoted.** All of those ran with the real scanner
binaries on PATH, so the verdict came from `compute_security_verdict()` and not
from a fixture — see "Scanner binaries" below for why that distinction decides
whether any of this is evidence.

> **[SUPERSEDED at `b95cedb`]** Written at `00f1997`: *"It holds with the model
> off — 30 credential-free runs, no deviation. **It does not hold with a live
> model: 2 of 5 live poisoned runs blocked, 2 promoted and 1 failed.** The cause
> is found, is deterministic, is reproducible without a model, and is *not* in
> the block rule — it is in `developer.py`'s poisoned safety net."* The cause
> was exactly that, and it is fixed; the 2/5 figure is superseded by the 5/5 in
> Result 5. The diagnosis is preserved under "What did not hold" because the
> 2/5 is the evidence that the defect was real.

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

> **[SUPERSEDED at `b95cedb`]** This is the measurement that *found* the defect,
> taken at `00f1997`. It records how the code behaved before the fix and is kept
> for that reason — the 2/5 is the evidence that the safety net was broken, and
> deleting it would leave the fix looking like a precaution rather than a
> repair. The re-run on the fixed code is **Result 5**.

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

## Result 5 — poisoned, LIVE, re-run on the FIXED code — **5 of 5 blocked**

**This is the re-run the earlier controller ruling required, and it supersedes
the 2/5 in Result 3.** Same ticket, same model, same repository, same command;
the only difference is the two commits in between. HEAD `b95cedb`.

Real Bedrock (`us.amazon.nova-2-lite-v1:0`, `us-east-1`) and real pull requests
on the public repo `mohamedsorour1998/auth-service`, both authorised by the repo
owner. Plain `python -m agentorg.graph --poisoned`, five times, uninstrumented —
every number below is read back out of each run's own stdout and its own
artifacts in `runs/`.

**Scanner mode: the three real binaries were ON PATH for all six live runs**
(`gitleaks 8.21.2`, `semgrep 1.172.0`, `trivy 0.74.0`, resolved from
`scanbin/` and confirmed with `which` before spending anything). **Fixture
fallback fired 0 times out of 5**, so all five verdicts came from
`compute_security_verdict()` over real scanner findings. That is the whole point
of running it this way: on the fallback path the verdict comes out of
`fixtures/security_result_block.json` and the block rule is never called, so a
5/5 obtained there would be no evidence for the claim at all.

| run | status | verdict | fixture fallback | key on an ADDED line | block comment |
|---|---|---|---|---|---|
| 1 | **blocked** | block/2 | no | yes | [`issuecomment-5336234898`](https://github.com/mohamedsorour1998/auth-service/pull/5#issuecomment-5336234898) |
| 2 | **blocked** | block/2 | no | yes | [`issuecomment-5336239799`](https://github.com/mohamedsorour1998/auth-service/pull/5#issuecomment-5336239799) |
| 3 | **blocked** | block/2 | no | yes | [`issuecomment-5336244327`](https://github.com/mohamedsorour1998/auth-service/pull/5#issuecomment-5336244327) |
| 4 | **blocked** | block/2 | no | yes | [`issuecomment-5336248479`](https://github.com/mohamedsorour1998/auth-service/pull/5#issuecomment-5336248479) |
| 5 | **blocked** | block/2 | no | yes | [`issuecomment-5336252757`](https://github.com/mohamedsorour1998/auth-service/pull/5#issuecomment-5336252757) |

All five: `rc=0`, `blocking=2`, 4 loop iterations, `revision_count=3`, 35–41 s
per run, branch `agent-org/DEMO-POISON-6dab07b`, PR
**<https://github.com/mohamedsorour1998/auth-service/pull/5>**. Five runs, one
branch and one PR, because the branch name is `agent-org/<ticket>-<sha1(diff)
[:7]>` and the safety net now substitutes the same reference diff every time —
which is the fix working, not a coincidence. Each run added its own comment, so
#5 collected five more.

**The column that shows the fix is "key on an ADDED line": yes on all five.**
Before `2d7913c` the three non-blocking runs carried `AKIAIOSFODNN7EXAMPLE` on a
removal line and nowhere else, so the scanners were handed a change with no
secret in it. Both readings are recorded per run — the whole diff text and the
added lines only — precisely because the old defect was that those two answers
disagreed. They now agree on every run.

The 3-revision cap was reached on all five (`revision_count=3`), the same shape
as old run 5, which had ended `failed` on a model's opinion rather than on the
block rule. It no longer matters what the reviewer concludes: the key is in the
change on every iteration, so the security stage blocks regardless.

## Result 6 — clean ticket, LIVE, re-run on the FIXED code — promoted

The converse control, and the reason 5/5 is not simply "everything blocks now".
Same live configuration, scanners on PATH, fixture fallback 0/1.

- [x] `status=promoted`, `security verdict=pass, blocking=0`, all three gates
      approved, real PR
      **<https://github.com/mohamedsorour1998/auth-service/pull/9>**
      on `agent-org/DEMO-CLEAN-73c56d4`. The model wrote a 1930-byte diff over 5
      files, no `AKIA` anywhere, the real scanners found nothing, 1 loop
      iteration, `revision_count=0`, 17.2 s.

A pass and a block on the same code path, same binaries, same session: the
verdict still tracks what is in the change.

### Live spend for this re-run

- **62 Bedrock invocations** of `us.amazon.nova-2-lite-v1:0`: 50 across the five
  poisoned runs (4 loop iterations each → `1 planner + 2×4 + 1 explainer` = 10
  per run), 4 for the clean run, and 8 for a derivation check. The formula is
  not assumed: it was re-validated on this HEAD against a counter placed on
  `llm._complete` — derived 8, counted 8 — and that counter was itself checked
  under `LLM_DISABLED=true`, where it reported 0, before any number it produced
  was believed. Botocore-level retries, if any, are not included.
- **1 pull request opened** (#9, clean) and **5 PR comments posted**, all on the
  pre-existing #5. Verified against the GitHub API afterwards: #5 carries 7
  comments — the 2 from Result 3 plus these 5 — and #9 carries 0.
- Cumulative across Result 3, Result 4 and this re-run, the branch has opened
  PRs #4–#9 on that repository.

### One environmental wobble, reported rather than hidden

Run 3 printed a `MaxTokensReachedException` from `strands` on stderr — the model
hit its output-token limit on one call mid-run. It did not change the outcome:
`llm.text()` catches it, logs it, returns None, and the caller falls back to its
fixture, which is exactly the degradation `llm.py` is built for. That run still
ended `blocked` with `blocking=2` from real scanner findings and posted a real
comment. Recording it because a reader comparing run 3's stderr against the
others should not have to wonder whether it was suppressed.

## What did not hold

**1. The poisoned safety net checks the wrong side of the diff. (blocker)
— FIXED in `2d7913c`; re-measured live in Result 5.**

The diagnosis below is the original one, written at `00f1997`. It is still the
correct account of *why* the live path failed; only the last paragraph ("Not
fixed here") has been superseded.

`agentorg/agents/developer.py`, **as it stood at `00f1997`**:

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

> **[SUPERSEDED at `b95cedb`]** Written at `00f1997`: *"Not fixed here: task 11
> is verification, `state.py` is frozen, and this is a change to `developer.py`.
> The shape of the fix is to search only added lines rather than the whole diff
> string. It needs its own task and its own test."*

It got its own task and its own tests. Two commits landed after this section was
written, both on this branch, and `state.py` was not touched by either:

**`2d7913c` — the safety net now reads added lines only, through one shared
materialiser.** The predicted shape of the fix was right but too small. The
question "is the key in this change?" had been written out four separate times —
the three scanner wrappers plus the safety net — and the four copies had drifted
apart, which is what let the safety net answer differently from the scanners it
was feeding. So the answer lives in one place now, `agentorg/common/diff.py`,
and all four call sites ask it. That closes a second disagreement of the same
class for free: added lines appearing before the first `+++ b/` header belong to
no file, so no scanner ever reads them, while a search over the diff string
finds them. Scanned trees are otherwise unchanged — 12 diff shapes compared
against verbatim reimplementations of both old wrapper bodies, 0 mismatches, and
`scripts/scan_gate.py` on real binaries still reports `app/auth.py:3` and `:4`.
Suite 90 → 94; three of the four new tests are RED before the change, including
an end-to-end reproduction that asserted `'promoted' == 'blocked'`. The fourth
is the converse guard — a key on an *added* line must NOT be substituted — so
"it always blocks now" cannot be bought by deleting the feature.

**`b95cedb` — a `+++ b/` header that escapes the scanned directory now raises.**
Found while reviewing the materialiser the commit above created. The path in
that header is written by the model, and `Path(dest_dir) / relative` follows an
absolute target or a `..` escape straight out of the scratch directory. Measured
against `2d7913c`: both `+++ b/../escaped.py` and an absolute target wrote
model-chosen bytes outside the directory being scanned, as whatever user CI runs
as, **and left the scanned tree empty** — so the scanners found nothing and
`compute_security_verdict([])` returned `pass`. An LLM-controlled arbitrary file
write that also silently disarms the block rule, in the lane whose whole job is
to catch that class of thing. It raises now rather than dropping the file,
because a dropped file is a smaller tree and an empty tree is a clean scan; the
raise reaches `security.run`'s handler, which logs one bounded WARNING naming
the cause and falls back to the fixture verdict, which still blocks a diff
carrying an AWS key. Suite 94 → 95, RED before the guard with "DID NOT RAISE",
and the new assertion is on the filesystem — the escaped file must not exist —
since a wrapper that wrote the file and *then* raised would satisfy "it raised".

Note what this did **not** change. The block rule was never at fault and was
never edited; `state.py` is untouched on this branch. What was fixed is the
input it was handed.

*Items 2, 3 and 4 below were re-checked at `b95cedb` and all three still stand.*
None of them was in scope for either fix. Item 3 in particular was worth
re-checking, since `2d7913c` did touch `semgrep_tool` — but only its
materialiser call, not its `Finding` construction, and the live re-run's own
artifacts still show a `/var/folders/.../agentorg-semgrep-*/app/auth.py` path
next to gitleaks' clean `app/auth.py`.

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

### At `00f1997` — Results 3 and 4

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

### At `b95cedb` — Results 5 and 6, the re-run

- **62 Bedrock invocations**: 50 poisoned (10 per run × 5), 4 clean, 8 for the
  derivation check. Same formula, re-validated on this HEAD (derived 8, counted
  8; counter proved able to report 0 first).
- **1 pull request opened** (#9, clean) and **5 PR comments** posted, all on #5.

**Branch total: 127 Bedrock invocations, PRs #4–#9, 7 PR comments.**

## Suite

Measured at HEAD `b95cedb` with these documentation changes in the tree:

- [x] `pytest -q`, credentials present in the environment → **95 passed** (5.7 s)
- [x] `pytest -q` with AWS and GitHub credentials unset → **95 passed** (6.2 s).
      Both configurations matter: the three autouse guards in `tests/conftest.py`
      are what make those two numbers the same, and CI only ever exercises the
      second one.
- [x] `pytest -q` with the three scanner binaries on PATH → **95 passed**
      (48.7 s). Same 95 either way; the ~43 s is real scanners and a trivy
      database, which is why the `test` job in CI deliberately has none.
- [x] `ruff check agentorg scripts tests` → exit 0
- [x] Results 5 and 6 modified no production code. The commit carrying them
      touches this file plus three stale comments and one false comment
      elsewhere — see the merge-blocker report in
      `.superpowers/sdd/2026-08-15-week2-agents-and-ci/`.

## Week 2 status

**The Friday gate is met, offline and live.** Both of these block 5+ runs out of
5 with the verdict coming from `compute_security_verdict()` over real scanner
findings:

- `OFFLINE=true LLM_DISABLED=true python -m agentorg.graph --poisoned` — 10/10
- `python -m agentorg.graph --poisoned` with live Bedrock and live GitHub — 5/5

**Both are safe to demo on 25 Aug**, with one caveat that is about the demo
rather than the code: put the scanner binaries on PATH for it. Without them
every run still blocks, but from the fixture fallback, and the central claim
about `compute_security_verdict()` would then be unsupported by what the
audience is watching.

> **[SUPERSEDED at `b95cedb`]** Written at `00f1997`: *"**The Friday gate is met
> offline and not met live.** ... The live-model poisoned path must not be
> demoed until the `developer.py` safety net is fixed and this section is
> re-run."* The safety net was fixed in `2d7913c`, hardened in `b95cedb`, and
> this section has been re-run — that is Result 5. The restriction is lifted.
