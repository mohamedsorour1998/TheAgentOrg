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
