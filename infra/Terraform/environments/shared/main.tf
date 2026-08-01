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
# Lets GitHub Actions assume a role with no static keys (see .github/workflows/).
# Mariam's CI deploy step uses this role — subjects scope it to our repo only.
################################################################################
module "iam_github_oidc_provider" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-github-oidc-provider"
  version = "5.55.0"
}

module "iam_github_oidc_role" {
  for_each = { for r in var.github_oidc_roles : r.name => r }

  source  = "terraform-aws-modules/iam/aws//modules/iam-github-oidc-role"
  version = "5.55.0"

  name     = each.value.name
  subjects = each.value.subjects
  policies = each.value.policies

  tags = merge(local.tags, lookup(each.value, "tags", {}))
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
