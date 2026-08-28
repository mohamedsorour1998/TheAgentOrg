data "aws_caller_identity" "current" {}

locals {
  name       = "theagentorg-shared"
  region     = "us-east-1"
  account_id = data.aws_caller_identity.current.account_id

  tags = {
    Terraform   = "true"
    Environment = "shared"
    Project     = "TheAgentOrg"
  }
}

################################################################################
# GitHub OIDC provider + CI role
#
# The account already has a shared `github-actions-role` + OIDC provider
# trusted by other repos' CI. Rather than let Terraform overwrite that trust
# policy (which would drop the other repos' subjects), this role is managed
# outside Terraform: the TheAgentOrg subject and the ECR/Bedrock policies
# were added to the existing role via the AWS CLI. This data source just
# looks up its ARN for the output below — Terraform never writes to it.
################################################################################
data "aws_iam_role" "github_actions" {
  name = "github-actions-role"
}

################################################################################
# AgentCore: five ECR repos + the runtime role the agents assume
################################################################################
module "agentcore" {
  source = "../../modules/agentcore"

  name                  = local.name
  account_id            = local.account_id
  image_retention_count = var.image_retention_count
  tags                  = local.tags
}

################################################################################
# Ingress: the GitHub App's webhook -> a Lambda Function URL -> EventBridge
#
# THIS MODULE CREATES THE ONLY INTERNET-FACING, UNAUTHENTICATED ENTRY POINT IN
# THIS ACCOUNT'S SHARE OF THE PROJECT. The Function URL is `auth-type NONE`
# because GitHub cannot sign a SigV4 request, so the HMAC-SHA256 check inside
# the handler is the whole of the access control. The module's own main.tf opens
# with what that costs and what limits it -- read that before changing anything
# here.
#
# Two things this deliberately does NOT do:
#   * It does not write the webhook secret's value. Terraform creates the
#     container; a human writes the value once (task brief step 6), so the
#     secret never lands in S3 state.
#   * The event rule has no target yet. That needs an API destination aimed at
#     run-pipeline.yml's workflow_dispatch, which is Task 3's file.
################################################################################
module "ingress" {
  source = "../../modules/ingress"

  name = local.name
  # The handler is one file under infra/ingress/, zipped at plan time. It is NOT
  # part of the agentorg package on purpose: it imports boto3, which the Lambda
  # runtime provides, and tests/test_agentcore_deploy_assets.py fails a
  # third-party import under agentorg/ that is absent from the agents'
  # requirements.txt.
  handler_source_dir = "${path.root}/../../../ingress"
  tags               = local.tags

  # Empty by default: the rule gets no target. See the block below.
  dispatch_token_secret_name = var.dispatch_token_secret_name
}

################################################################################
# The ingress rule's TARGET: dispatching run-pipeline.yml
#
# Set `dispatch_token_secret_name` to the Secrets Manager secret holding a GitHub
# token and the module creates the connection, API destination, target, its role
# and a dead-letter queue. Leave it unset and the rule has no target -- an opened
# issue reaches the bus and starts nothing.
#
# UNSET IS THE DEFAULT, AND DELIBERATELY SO. An API_KEY connection needs the
# token's VALUE at PLAN time, so an ungated read of a secret nobody has written
# yet fails the plan -- which would turn this workflow, currently green end to
# end, red until somebody minted a token. The variable's own description in the
# module carries the full reasoning, including that the token lands in S3 state
# and must therefore be scoped to `actions: write` on this one repository.
################################################################################
variable "dispatch_token_secret_name" {
  description = "Secrets Manager secret NAME holding the GitHub token EventBridge dispatches with. Empty leaves the rule without a target -- see the module's variables.tf."
  type        = string
  default     = ""
}

################################################################################
# Run state: the decision log and paused-run documents in DynamoDB.
#
# OFF BY DEFAULT IN THE APPLICATION, and that is the point of keeping the two
# separate. This module creates the table; nothing reads or writes it until
# STATE_BACKEND=dynamodb is set in the environment (agentorg/common/config.py
# defaults to "local"). So applying this is safe on its own: the local JSONL path
# stays the tested default and the demo's fallback.
#
# The two roles are named rather than wildcarded because this table holds the
# audit trail of every human gate decision -- see the module's IAM section.
################################################################################
module "state" {
  source = "../../modules/state"

  name              = local.name
  runtime_role_arns = [module.agentcore.runtime_role_arn, data.aws_iam_role.github_actions.arn]
  tags              = local.tags
}

################################################################################
# Platform: where the queue worker runs. LANE N.
#
# The registry, the log group and two IAM roles are always created and cost
# nothing. The ECS cluster, task definition and service are COUNT-GATED OFF --
# `runtime_enabled` defaults false -- because they bill by the hour and because
# the Postgres queue dialect they would run has a defect measured 2026-08-28
# against a real PostgreSQL 16.15:
#
#   psycopg.errors.DatatypeMismatch: column "poisoned" is of type integer but
#   expression is of type boolean          -- agentorg/queue/_sql.py:369
#
# on the FIRST enqueue. So a worker service today would reach RUNNING, report
# healthy, poll, and fail every job. The module's main.tf carries the full
# reasoning, including why it creates no database and why the API and the web app
# are deliberately absent.
#
# `image_retention_count` comes from the same root variable the agentcore module
# reads, so the two registries cannot drift to different retentions.
################################################################################
module "platform" {
  source = "../../modules/platform"

  name                  = local.name
  account_id            = local.account_id
  image_retention_count = var.image_retention_count
  tags                  = local.tags

  # Off by default. Set through TF_VAR_platform_runtime_enabled in
  # .github/workflows/terraform.yml -- NEVER in terraform.tfvars, which
  # `.gitignore:14` ignores, so a value set there exists only on the laptop that
  # wrote it while CI applies from a fresh checkout. That failure is measured: the
  # ingress rule sat at zero targets while looking configured locally.
  runtime_enabled      = var.platform_runtime_enabled
  worker_image         = var.platform_worker_image
  queue_dsn_secret_arn = var.platform_queue_dsn_secret_arn
  subnet_ids           = var.platform_subnet_ids
  vpc_id               = var.platform_vpc_id
}
