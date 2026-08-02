# Sorour — Week 1 (Aug 8–14): skeleton + AWS live

Goal: AWS is live under your account, Bedrock answers a real prompt, and
everyone else has cloned a green repo. Nothing here waits on a teammate.

---

## Sat Aug 8 — kickoff + contract freeze

**Task: run the 90-minute kickoff, everyone present.**
- Agenda: walk `agentorg/state.py` field by field (`PlanResult`, `DevResult`,
  `ReviewResult`, `Finding`, `SecurityResult`, `SLOCheck`, `SREResult`,
  `HumanDecision`, `RunState`, `LogEvent`), show `compute_security_verdict()`,
  pick the poisoned flaw (hardcoded AWS key — already the demo), confirm
  directory ownership.
- Deliverable: the "add-only, never rename" rule stated out loud and
  acknowledged by all 5.
- **Done when:** everyone has cloned the repo and run
  `pip install -e ".[dev]" && pytest -q` → **3 passed** on their own machine.
- **Blocks:** nobody. Just needs to happen before independent work starts.

**Task: request Bedrock model access.**
- Console → Bedrock → Model access → `amazon.nova-2-lite-v1:0`, `us-east-1`.
  Can take hours to approve — request it today, not Wednesday.
- **Done when:** the model shows "Access granted."

---

## Sun–Mon Aug 9–10 — AWS state backend

**Task: create the S3 backend bucket.**
```bash
aws s3 mb s3://theagentorg-shared-terraform-backend --region us-east-1
aws s3api put-bucket-versioning \
  --bucket theagentorg-shared-terraform-backend \
  --versioning-configuration Status=Enabled
```
File `infra/Terraform/environments/shared/backend.tf` already points at this
bucket — no edit needed, just create the bucket first.
**Done when:** `aws s3 ls | grep theagentorg-shared-terraform-backend` returns it.

**Task: provide your tfvars.**
```bash
cd infra/Terraform/environments/shared
cp terraform.tfvars.example terraform.tfvars
terraform init
```
`terraform.tfvars` is gitignored — yours only, never pushed.
**Done when:** `terraform init` succeeds.

---

## Tue Aug 11 — apply the AgentCore infra

**Task: `terraform plan` then `apply`.**
```bash
cd infra/Terraform/environments/shared
terraform plan
terraform apply
```
Creates: 5 ECR repos (`theagentorg-shared-{planner,developer,reviewer,
security,sre}-agent`, keep-last-5) and the `theagentorg-shared-agentcore-
runtime-role` (trusts `bedrock-agentcore.amazonaws.com`).

The `github-actions-role` and its GitHub OIDC provider ALREADY EXIST in the
account (shared with other repos' CI) — Terraform looks them up through a `data`
source and never manages them. The TheAgentOrg subject
(`repo:mohamedsorour1998/TheAgentOrg:*`) and the ECR/Bedrock policies were added
to that existing role once, via the AWS CLI, outside Terraform. So `apply`
creates only the repos + runtime role; it must not try to (re)create the OIDC
provider or the CI role.

**Done when:** `terraform output` shows:
- `ecr_repository_urls` — 5 entries
- `agentcore_runtime_role_arn` — one ARN
- `github_actions_role_arns` — the existing `github-actions-role` ARN, surfaced
  for Mariam (not created here)

**You're unblocked because:** depends on nobody — start the moment the
backend bucket exists.

**Hands off to Mariam:** send her `agentcore_runtime_role_arn` and
`github_actions_role_arns` — she needs both for `agentcore configure` and her
CI workflow's `role-to-assume`.

---

## Wed Aug 12 — prove Bedrock works

**Task: one throwaway script (or under `scripts/` as a smoke test).**
```python
from agentorg.common.model import create_model
from strands import Agent

agent = Agent(model=create_model(), system_prompt="You are terse.")
print(agent("say hi"))
```
If `AccessDenied`: attach `AmazonBedrockFullAccess` to your IAM user for the
hackathon account (fine — not a shared/prod account).

**Done when:** a real text completion comes back, not an exception.

**Task: check in on the poisoned-ticket handoff** (Reem → Habiba, due today).
Not your task — a 2-minute ping if it hasn't landed in `tickets/poisoned.md`
by end of day.

---

## Thu–Fri Aug 13–14 — buffer + verification

**Task: dry-run the whole stack against real AWS.**
```bash
python -m agentorg.graph            # clean    -> promoted
python -m agentorg.graph --poisoned # poisoned -> blocked (2 findings)
pytest -q                           # 3 passed
```
**Done when:** all three behave exactly as before your Terraform apply —
confirms AWS work didn't touch the stubbed graph.

**Task: skim everyone's early commits** (5 minutes). You don't own their
directories, but a quick look catches an accidental `state.py` edit before it
compounds.
**Done when:** you've looked at each of the 4 branches/PRs at least once.

---

## End of week 1 — done when

- AWS is live: ECR repos + IAM roles exist.
- Bedrock answers a real prompt.
- The stubbed pipeline is still green (clean → promoted, poisoned → blocked).
- Mariam has the ARNs she needs to start week 2.

## Risks + fallback

| Risk | Fallback |
|---|---|
| `terraform apply` fails on IAM permissions | Use an admin-scoped role for the hackathon account only — acceptable, not a shared/prod account. |
| Bedrock model access not enabled | Request access **Sat Aug 8**, not Wed — approval can take hours. |
| A teammate can't `pytest -q` locally | Usually a Python version or missing `pip install -e ".[dev]"` — fix in the kickoff before people scatter. |

Nothing this week depends on Mariam, Habiba, Reem, or Aya finishing anything —
the one dependency (poisoned ticket → scanners) is *their* handoff, not yours.
