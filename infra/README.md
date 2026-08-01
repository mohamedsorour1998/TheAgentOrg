# infra/ — AWS infrastructure (Terraform)

**Owner: Sorour.** Everything AWS lives here. No other teammate needs an AWS
account or credentials — they run the pipeline locally on stubs.

## agentcore/

Terraform for the AgentCore stack: one ECR repo per role agent and a shared IAM
role trusted by `bedrock-agentcore.amazonaws.com` (log write, Bedrock invoke,
agent-to-agent invoke, ECR pull). State is stored in S3.

### First-time setup

```bash
# 1. Create the state bucket once (see backend.tf for the exact commands).
aws s3 mb s3://theagentorg-terraform-state --region us-east-1

# 2. Init + apply.
cd infra/agentcore
terraform init
terraform plan
terraform apply
```

Outputs give you the ECR URLs (where agent images get pushed) and the AgentCore
role ARN (used when the runtimes are created in the week-3 deploy step).
