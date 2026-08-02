# Sorour Week 1 — Kickoff Prep + AWS Bring-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare the Aug 8 kickoff materials and stand up the real AWS
infrastructure (S3 state backend, ECR repos, AgentCore runtime role, GitHub
OIDC role) so the team leaves week 1 with a live AWS account and a verified
Bedrock connection, per `docs/plan/sorour/week1.md`.

**Architecture:** No application code changes. This plan produces: (1) a
kickoff agenda doc the team can follow live, (2) a small verification script
proving Bedrock is reachable, (3) applied Terraform state creating the shared
AWS resources, and (4) a written verification log confirming the stubbed
pipeline is unaffected.

**Tech Stack:** Terraform (`infra/Terraform/environments/shared/`), AWS CLI,
Python (Strands `Agent` + `BedrockModel` via `agentorg.common.model`), pytest.

## Global Constraints

- AWS region: `us-east-1` (hardcoded across `providers.tf`, `backend.tf`, and `config.py`).
- Bedrock model id: `us.amazon.nova-2-lite-v1:0` (from `config.BEDROCK_MODEL`).
- S3 state bucket name: `theagentorg-shared-terraform-backend` (must exist before `terraform init`).
- `terraform.tfvars` is gitignored — never commit it; only `terraform.tfvars.example` is tracked.
- GitHub OIDC role subject scope: `repo:mohamedsorour1998/TheAgentOrg:*` (already set in `terraform.tfvars.example`).
- Never rename or remove a field in `agentorg/state.py` — add-only.
- Any step that creates or modifies real AWS resources (bucket creation, `terraform apply`) is a **billed, hard-to-reverse action** — confirm with the user before running it, per the "Executing actions with care" policy. Do not run these autonomously.

---

### Task 1: Kickoff agenda doc

**Files:**
- Create: `docs/plan/kickoff-agenda.md`

**Interfaces:**
- Consumes: `agentorg/state.py` field list, `docs/plan/00-timeline.md`, `docs/plan/OVERVIEW-for-meeting.md` (already exist — read, don't modify).
- Produces: a standalone agenda doc other tasks reference for "done when everyone has cloned + run pytest."

- [ ] **Step 1: Write the agenda doc**

```markdown
# Kickoff Agenda — Sat Aug 8, 90 minutes

**Attendees:** Sorour, Mariam, Habiba, Reem, Aya.
**Goal:** everyone leaves with a green local clone and agreement on the contract.

## Agenda

1. **(10 min) The idea + the demo.** One clean ticket ships, one poisoned
   ticket (hardcoded AWS key) blocks — every time, because the verdict is
   computed by code (`compute_security_verdict` in `agentorg/state.py`), not
   guessed by a model.

2. **(30 min) Walk `agentorg/state.py` field by field.**
   - `PlanResult` — planner output: `tasks: list[str]`
   - `DevResult` — developer output: `diff`, `files_changed`, `pr_url`
   - `ReviewResult` — reviewer output: `verdict` (`approved` / `changes_requested`), `comments`
   - `Finding` — one security finding: `severity`, `tool`, `description`
   - `SecurityResult` — `findings: list[Finding]`, `verdict` (`pass` / `block`), `blocking_findings`
   - `SLOCheck`, `SREResult` — SRE gate output
   - `HumanDecision` — recorded at each of the 3 gates
   - `RunState` — the whole run: ticket, plan, dev, review, security, sre, status, gate decisions
   - `LogEvent` — one append-only log line
   - Show `compute_security_verdict(findings, threshold="high")` — pure
     Python, no LLM call, deterministic.

3. **(10 min) State the rule out loud.** You may **add** optional fields to
   any model in `state.py`. Never rename or remove one — a rename breaks all
   five lanes simultaneously. Get a verbal yes from each person.

4. **(10 min) Confirm the poisoned flaw.** Hardcoded AWS key
   (`AKIAIOSFODNN7EXAMPLE`, AWS's public example placeholder) is the flaw
   Reem's poisoned ticket carries and Habiba's gitleaks wrapper catches.

5. **(10 min) Confirm directory ownership**, pointing each person at their
   `docs/plan/<name>/README.md`:
   - Sorour: `infra/`, `agentorg/common/`, `graph.py`, `gates.py`, `log.py`, `agentorg/agents/`
   - Mariam: `agentorg/github_ops.py`, `.github/workflows/`
   - Habiba: `agentorg/security/`
   - Reem: `target_repo/`, `tickets/`, `tests/test_functional_*`, `test_baseline.py`
   - Aya: `tests/test_block_*`, `test_chaos_*`, `test_dora_*`

6. **(15 min) Live clone-and-run, together.** Everyone runs, on their own
   machine, in this order:
   ```bash
   git clone https://github.com/mohamedsorour1998/TheAgentOrg.git
   cd TheAgentOrg
   pip install -e ".[dev]"
   python make_fixtures.py
   pytest -q
   python -m agentorg.graph
   python -m agentorg.graph --poisoned
   ```
   Fix issues live — usually a Python version mismatch or a missing
   `pip install -e ".[dev]"`.

## Done when

- [ ] All 5 people have a local clone.
- [ ] `pytest -q` shows **3 passed** on every machine.
- [ ] `python -m agentorg.graph` prints `status=promoted`.
- [ ] `python -m agentorg.graph --poisoned` prints `status=blocked`,
      `security verdict=block, blocking=2`.
- [ ] Everyone has verbally agreed to the add-only rule and knows their
      directory.
```

- [ ] **Step 2: Verify the commands in the doc actually work as written**

Run each command from the doc's step 6 in this repo right now:

```bash
pip install -e ".[dev]"
python make_fixtures.py
pytest -q
python -m agentorg.graph
python -m agentorg.graph --poisoned
```
Expected: `pytest -q` → `3 passed`; clean run → `status=promoted`; poisoned
run → `status=blocked`, `security verdict=block, blocking=2`. If any command
differs from what's documented, fix the doc, not the repo.

- [ ] **Step 3: Commit**

```bash
git add docs/plan/kickoff-agenda.md
git commit -m "docs: add Aug 8 kickoff agenda"
```

---

### Task 2: Bedrock smoke-test script

**Files:**
- Create: `scripts/bedrock_smoke_test.py`
- Modify: `docs/plan/sorour/week1.md:83-94` (replace the inline snippet with a pointer to the script, keep the same "done when")

**Interfaces:**
- Consumes: `agentorg.common.model.create_model()` — existing function, signature `create_model(**overrides) -> BedrockModel | OpenAIModel`.
- Produces: a runnable CLI script other tasks (and future teammates) can invoke without copy-pasting a snippet from a markdown file.

- [ ] **Step 1: Write the script**

```python
"""Bedrock connectivity smoke test.

Confirms create_model() reaches a real Bedrock endpoint and gets a real
completion back — not a stub, not an exception. Run manually:

    python scripts/bedrock_smoke_test.py

Requires: AWS credentials configured (aws configure / env vars) with
bedrock:InvokeModel on the model in agentorg.common.config.BEDROCK_MODEL,
in agentorg.common.config.AWS_REGION.
"""

import sys

from agentorg.common.model import create_model


def main() -> int:
    from strands import Agent

    agent = Agent(model=create_model(), system_prompt="You are terse.")
    reply = agent("say hi")
    print(f"Bedrock reply: {reply}")

    if not reply or not str(reply).strip():
        print("FAIL: empty reply from Bedrock", file=sys.stderr)
        return 1

    print("OK: Bedrock is reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Confirm the script fails cleanly before AWS is ready**

Run: `python scripts/bedrock_smoke_test.py`
Expected (before Task 4's `terraform apply` and before Bedrock model access
is granted): a `botocore`/`ClientError` traceback (e.g. `AccessDeniedException`
or `UnauthorizedOperation`) — not a Python `ImportError` or `AttributeError`.
This confirms the script is wired correctly and only AWS access is missing.
If you get an `ImportError` for `strands`, run `pip install -e ".[dev]"` first.

- [ ] **Step 3: Update the week1 doc to reference the script**

In `docs/plan/sorour/week1.md`, replace the inline Python snippet under
"Wed Aug 12 — prove Bedrock works" with:

```markdown
**Task: run the Bedrock smoke test.**
```bash
python scripts/bedrock_smoke_test.py
```
If `AccessDenied`: attach `AmazonBedrockFullAccess` to your IAM user for the
hackathon account (fine — not a shared/prod account).

**Done when:** the script prints `OK: Bedrock is reachable.`
```

- [ ] **Step 4: Commit**

```bash
git add scripts/bedrock_smoke_test.py docs/plan/sorour/week1.md
git commit -m "feat: add Bedrock smoke-test script for week 1 verification"
```

---

### Task 3: Request Bedrock model access + confirm AWS identity

**Files:** none (AWS console + CLI actions only, no repo changes).

**Interfaces:**
- Consumes: `agentorg.common.config.AWS_REGION` (`us-east-1`), `agentorg.common.config.BEDROCK_MODEL` (`us.amazon.nova-2-lite-v1:0`).
- Produces: a working AWS identity Task 4 and Task 6 depend on.

- [ ] **Step 1: Confirm the active AWS identity**

Run: `aws sts get-caller-identity`
Expected: a JSON object with `Account`, `UserId`, `Arn` for the hackathon AWS
account — **confirm this is the account you intend to use for the whole
hackathon**, not a personal/prod account, before continuing.

- [ ] **Step 2: Request Bedrock model access (manual, console)**

Console → Bedrock → Model access → request `amazon.nova-2-lite-v1:0` in
`us-east-1`. This is a manual console action with no CLI equivalent for
on-demand model access grants; approval can take minutes to hours.

⚠️ **Ask the user before proceeding to Task 4** if this hasn't been
requested yet — the smoke test in Task 2 will keep failing until it's
granted, but Task 4 (Terraform apply) does not depend on it and can proceed
in parallel.

- [ ] **Step 3: No commit** — this task has no file changes.

---

### Task 4: Stand up the S3 Terraform state backend

**Files:** none (AWS CLI actions only).

**Interfaces:**
- Consumes: `infra/Terraform/environments/shared/backend.tf` (already references bucket `theagentorg-shared-terraform-backend`, region `us-east-1` — do not modify).
- Produces: the S3 bucket Task 5's `terraform init` requires.

⚠️ **This task creates a real, billed AWS resource. Confirm with the user
before running these commands.**

- [ ] **Step 1: Create the bucket**

```bash
aws s3 mb s3://theagentorg-shared-terraform-backend --region us-east-1
```
Expected: `make_bucket: theagentorg-shared-terraform-backend`

- [ ] **Step 2: Enable versioning**

```bash
aws s3api put-bucket-versioning \
  --bucket theagentorg-shared-terraform-backend \
  --versioning-configuration Status=Enabled
```
Expected: no output on success (exit code 0).

- [ ] **Step 3: Verify**

Run: `aws s3api get-bucket-versioning --bucket theagentorg-shared-terraform-backend`
Expected: `{"Status": "Enabled"}`

- [ ] **Step 4: No commit** — this task has no file changes (the bucket
  already exists in `backend.tf` as a reference, committed previously).

---

### Task 5: Configure and init the shared Terraform environment

**Files:**
- Create (local, gitignored — do not commit): `infra/Terraform/environments/shared/terraform.tfvars`

**Interfaces:**
- Consumes: `infra/Terraform/environments/shared/terraform.tfvars.example` (already committed, defines `github_oidc_roles`).
- Produces: an initialized Terraform working directory Task 6 applies.

- [ ] **Step 1: Copy the example tfvars**

```bash
cd infra/Terraform/environments/shared
cp terraform.tfvars.example terraform.tfvars
```
Review the copied file — the default `subjects: ["repo:mohamedsorour1998/TheAgentOrg:*"]`
should already match this repo; edit only if your AWS account ID needs to
appear anywhere (it doesn't currently — `main.tf` reads it via
`data.aws_caller_identity.current`).

- [ ] **Step 2: Confirm it's gitignored**

Run: `git check-ignore infra/Terraform/environments/shared/terraform.tfvars`
Expected: prints the path (confirms it's ignored, so `git status` won't show
it as untracked).

- [ ] **Step 3: Init**

```bash
terraform init
```
Expected: `Terraform has been successfully initialized!` — this connects to
the S3 backend created in Task 4.

- [ ] **Step 4: No commit** — `terraform.tfvars` and `.terraform/` are both
  gitignored; nothing in this task is tracked.

---

### Task 6: Apply the AgentCore infra

**Files:** none (Terraform state changes only; no source files modified —
`infra/Terraform/environments/shared/*.tf` already exist from the earlier
infra restructure).

**Interfaces:**
- Consumes: `module.agentcore` (from `infra/Terraform/modules/agentcore/`), `module.iam_github_oidc_role` — both already defined in `main.tf`.
- Produces: `ecr_repository_urls`, `agentcore_runtime_role_arn`, `github_actions_role_arns` outputs — Mariam's week 3 plan (`docs/plan/mariam/week3.md`) consumes these ARNs directly by name.

⚠️ **This task creates real, billed AWS resources (5 ECR repos, 2 IAM
roles, an OIDC provider). Confirm with the user before running `apply`.**

- [ ] **Step 1: Plan**

```bash
cd infra/Terraform/environments/shared
terraform plan
```
Expected: plan shows resources to add — 5× `module.agentcore.module.ecr[*]`,
`module.agentcore.aws_iam_role.runtime`,
`module.agentcore.aws_iam_role_policy.runtime`,
`module.iam_github_oidc_provider`, `module.iam_github_oidc_role["github-actions-role"]`.
No resources to destroy (this is a fresh apply).

- [ ] **Step 2: Apply**

```bash
terraform apply
```
Type `yes` when prompted. Expected: `Apply complete! Resources: N added, 0 changed, 0 destroyed.`

- [ ] **Step 3: Capture outputs**

```bash
terraform output
```
Expected: three outputs —
- `ecr_repository_urls` — a map with 5 entries (`planner`, `developer`, `reviewer`, `security`, `sre`)
- `agentcore_runtime_role_arn` — a single ARN string
- `github_actions_role_arns` — a map with one entry, `"github-actions-role"`

Save these three values somewhere you can paste from — Task 7 verifies them,
and Mariam needs `agentcore_runtime_role_arn` + the `github-actions-role` ARN
for her week 3 work.

- [ ] **Step 4: No commit** — Terraform state lives in the S3 backend, not
  in git. Nothing new to stage.

---

### Task 7: Verify AWS outputs and hand off to Mariam

**Files:**
- Create: `docs/plan/week1-verification-log.md`

**Interfaces:**
- Consumes: `terraform output` values from Task 6, `python scripts/bedrock_smoke_test.py` from Task 2/3.
- Produces: a written record confirming week 1's "done when" criteria from `docs/plan/sorour/week1.md:120-125` are met, and the exact values Mariam needs.

- [ ] **Step 1: Re-run the Bedrock smoke test now that access + apply are done**

```bash
python scripts/bedrock_smoke_test.py
```
Expected: `OK: Bedrock is reachable.`

- [ ] **Step 2: Re-run the full stubbed-pipeline dry run**

```bash
python make_fixtures.py
python -m agentorg.graph
python -m agentorg.graph --poisoned
pytest -q
```
Expected: fixtures regenerate clean, `status=promoted` on the clean run,
`status=blocked` / `security verdict=block, blocking=2` on the poisoned run,
`3 passed` from pytest. This confirms the Terraform apply didn't disturb the
stubbed graph.

- [ ] **Step 3: Write the verification log**

```markdown
# Week 1 Verification Log — Sorour

**Date:** 2026-08-08 (or the actual date you ran this)

## AWS live

- [x] S3 backend bucket: `theagentorg-shared-terraform-backend` (versioned)
- [x] `terraform apply` succeeded from `infra/Terraform/environments/shared/`
- ECR repos (5): <paste `ecr_repository_urls` output here>
- AgentCore runtime role ARN: <paste `agentcore_runtime_role_arn` here>
- GitHub Actions OIDC role ARN: <paste `github_actions_role_arns["github-actions-role"]` here>

## Bedrock

- [x] `python scripts/bedrock_smoke_test.py` → `OK: Bedrock is reachable.`

## Pipeline still green

- [x] `pytest -q` → 3 passed
- [x] `python -m agentorg.graph` → status=promoted
- [x] `python -m agentorg.graph --poisoned` → status=blocked, blocking=2

## Handed off to Mariam

Sent her the AgentCore runtime role ARN and the `github-actions-role` ARN
(both above) — she needs them for `agentcore configure -er ...` and her CI
workflow's `role-to-assume` (see `docs/plan/mariam/week3.md`).
```
Fill in the actual output values from Task 6 Step 3 before committing.

- [ ] **Step 4: Commit**

```bash
git add docs/plan/week1-verification-log.md
git commit -m "docs: record week 1 AWS bring-up verification"
```

---

## Self-Review Notes

- **Spec coverage:** every bullet in `docs/plan/sorour/week1.md` (kickoff,
  Bedrock access request, S3 backend, tfvars, terraform apply, Bedrock
  smoke test, dry-run verification, handoff to Mariam) maps to a task above.
  The "skim everyone's early commits" bullet is a standing habit, not a
  one-time deliverable — it isn't a task here; do it ad hoc through the week.
- **Placeholder scan:** no TBD/TODO left; every step has literal commands or
  code. The verification log template has bracketed placeholders
  (`<paste ... here>`) by design — they get filled with Task 6's real output
  values before the Step 4 commit, not left as permanent placeholders.
- **Type/interface consistency:** `create_model()` signature matches
  `agentorg/common/model.py:10`; output names (`ecr_repository_urls`,
  `agentcore_runtime_role_arn`, `github_actions_role_arns`) match
  `infra/Terraform/environments/shared/outputs.tf` exactly.
