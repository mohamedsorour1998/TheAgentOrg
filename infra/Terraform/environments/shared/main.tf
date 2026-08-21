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
}
