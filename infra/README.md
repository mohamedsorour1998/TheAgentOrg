# infra/ — AWS infrastructure (Terraform)

**Owner: Sorour.** Everything AWS lives here. No other teammate needs an AWS
account or credentials — they run the pipeline locally on stubs.

## Layout

```
infra/Terraform/
├── environments/
│   └── shared/                 # the root module you `terraform apply`
│       ├── backend.tf          # S3 remote state
│       ├── providers.tf        # aws ~> 6.28, region us-east-1
│       ├── variables.tf
│       ├── main.tf             # locals + GitHub OIDC + agentcore module
│       ├── outputs.tf
│       └── terraform.tfvars    # the github-actions-role definition
└── modules/
    └── agentcore/              # 5 ECR repos + the AgentCore runtime role
        ├── main.tf
        ├── variables.tf
        └── outputs.tf
```

One `shared` environment for the whole team — a `modules/` + `environments/`
split so infra grows the same way the rest of our stack does.

## What it creates

- **Five ECR repositories** — `theagentorg-shared-{planner,developer,reviewer,security,sre}-agent`,
  MUTABLE tags, keep-last-5 lifecycle. Where each agent's arm64 image is pushed.
- **AgentCore runtime role** — `theagentorg-shared-agentcore-runtime-role`, trusted
  by `bedrock-agentcore.amazonaws.com`: log write, Bedrock foundation-model invoke,
  agent-to-agent runtime invoke, ECR pull. Pass its ARN to `agentcore configure -er`.
- **GitHub OIDC provider + `github-actions-role`** — lets CI assume a role with no
  static keys, scoped to `repo:mohamedsorour1998/TheAgentOrg:*`. Mariam's deploy
  workflow uses it.

The AgentCore **runtimes** themselves are created by the AgentCore CLI at deploy
time (see `docs/plan/mariam/week3.md`) once the images exist — Terraform lays down the
registries and roles they stand on.

## First-time setup

```bash
# 1. Create the state bucket once (see backend.tf for the exact commands).
aws s3 mb s3://theagentorg-shared-terraform-backend --region us-east-1

# 2. Provide your var values (tfvars is gitignored — copy the example).
cd infra/Terraform/environments/shared
cp terraform.tfvars.example terraform.tfvars

# 3. Init + apply the shared environment.
terraform init
terraform plan
terraform apply
```

Outputs give you the ECR URLs (`ecr_repository_urls`), the runtime role ARN
(`agentcore_runtime_role_arn`, used at `agentcore configure` time), and the CI
role ARN (`github_actions_role_arns`).
