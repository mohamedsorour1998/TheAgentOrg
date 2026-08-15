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

## Verified here (no credentials needed)

- [x] `pytest -q` → 3 passed
- [x] `python -m agentorg.graph` → status=promoted
- [x] `python -m agentorg.graph --poisoned` → status=blocked, blocking=2
- [x] Branch convention `agent-org/<ticket_id>-<short_sha>`; `short_sha` is
      stable per diff (re-runs reuse the branch) and changes when the diff does.

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

## Not verified here

The **online path** (real PR + real comment on `demo-app`) needs Mariam's
`GITHUB_TOKEN` and her `demo-app` sandbox; neither exists on Sorour's machine.
Code-reviewed against the plan and matches it. Mariam to confirm the two
one-liners in `week1.md` (Tue–Wed and Thu–Fri) print a real `pull/N` URL and an
`#issuecomment-` URL.
