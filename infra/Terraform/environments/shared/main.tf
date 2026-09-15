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
# Tenancy: the single DynamoDB table that will replace Postgres. STEP 1 ONLY.
#
# `docs/design/dynamodb-migration.md` is the plan. This creates the table and two
# IAM policies and NOTHING READS IT YET -- the Postgres path stays authoritative
# until step 10 of that document, and the table costs nothing empty under
# PAY_PER_REQUEST.
#
# BOTH ROLE LISTS ARE DELIBERATELY EMPTY AND THAT IS THE SAFE STATE, not an
# oversight. `tenant_assumer_arns` empty means the tenant-scoped role exists with
# a trust policy nothing can satisfy, so no AssumeRole succeeds; `service_role_arns`
# empty means the pipeline has no access. Both fail CLOSED, and the outputs report
# which state you are in -- `modules/ingress`'s `dispatch_target_enabled` lesson,
# where a rule with no target fires into nothing while looking healthy.
#
# Populate them only as the corresponding step of the plan lands, and read §4
# before adding to `service_role_arns`: every ARN there is a principal that
# `dynamodb:LeadingKeys` does not constrain.
################################################################################

# THE AMPLIFY SSR COMPUTE ROLE -- the missing half of steps 6 and 8.
#
# MEASURED 2026-09-15, and it is why the deployed read path could never have
# worked. The Amplify app carries ONE role, `AmplifySSRLoggingRole`, and its whole
# policy is three actions:
#
#   logs:CreateLogStream   logs:PutLogEvents   logs:CreateLogGroup/DescribeLogGroups
#
# No DynamoDB, no STS. So `web/lib/reader/*.py` was repointed at DynamoDB in
# `da7c1dd` (step 8, correctly) onto a runtime with no credential able to read it.
# That is this repository's signature shape one more time: code that is right and
# cannot run, with every gate green -- `next build` compiles the readers, and no
# test in either suite can see an IAM policy.
#
# THIS ROLE HOLDS NO DATA ACCESS OF ITS OWN, deliberately. Its only permission is
# to assume the tenant-scoped role WITH a session tag, so the chain the design
# argues for stays intact end to end:
#
#   Cognito custom:tenant (immutable) -> session tag -> LeadingKeys -> DynamoDB
#
# Granting it `dynamodb:GetItem` directly would be one line shorter and would
# delete the entire guarantee: the SSR runtime would read every tenant's rows and
# be trusted not to by application code, which is what §4 of the plan refuses.
resource "aws_iam_role" "amplify_compute" {
  name                 = "${local.name}-amplify-compute"
  max_session_duration = 3600
  tags                 = local.tags

  assume_role_policy = data.aws_iam_policy_document.assume_amplify_compute.json
}

data "aws_iam_policy_document" "assume_amplify_compute" {
  statement {
    sid     = "AmplifyHostingMayRunAsThisRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["amplify.amazonaws.com"]
    }

    # THE CONFUSED-DEPUTY GUARD. `amplify.amazonaws.com` is every Amplify app in
    # every account, so without this any of them could ask STS for this role.
    # Scoped to this app id, which is the value `infra/amplify/provision.py`
    # already treats as the app's identity.
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:amplify:${local.region}:${local.account_id}:apps/*"]
    }
  }
}

data "aws_iam_policy_document" "amplify_compute_may_assume_tenant" {
  statement {
    sid    = "AssumeTheTenantScopedRoleWithATag"
    effect = "Allow"

    # `TagSession` ALONGSIDE `AssumeRole`, for the reason the trust policy in
    # modules/tenancy/iam.tf spells out: without it STS answers AccessDenied, and
    # the tempting fix is to drop the tag -- which mints a session whose
    # `aws:PrincipalTag/tenant` is empty, so LeadingKeys compares against
    # `TENANT#`, matches nothing, and reads as "the database is broken".
    actions = ["sts:AssumeRole", "sts:TagSession"]

    # CONSTRUCTED, NOT REFERENCED, AND THAT IS NOT LAZINESS. Referencing
    # `module.tenancy` here would be a cycle: the tenant role's trust policy names
    # this role, so this role's policy cannot name that one. The name is
    # deterministic (`${var.name}-tenancy-scoped` in the module), so the string is
    # exact rather than a wildcard -- and if the module ever renames it, the
    # AssumeRole fails closed at run time rather than widening access.
    resources = ["arn:aws:iam::${local.account_id}:role/${local.name}-tenancy-scoped"]
  }
}

resource "aws_iam_role_policy" "amplify_compute_may_assume_tenant" {
  name   = "assume-tenancy-scoped"
  role   = aws_iam_role.amplify_compute.id
  policy = data.aws_iam_policy_document.amplify_compute_may_assume_tenant.json
}

module "tenancy" {
  source = "../../modules/tenancy"

  name = local.name
  tags = local.tags

  # WIRED 2026-09-15. Both lists were empty, so `aws_iam_role.tenant_scoped` was
  # counted off and had never been created -- `aws iam get-role` answered
  # NoSuchEntity. The module was complete and reached by nothing, which is the
  # same pattern `dispatch_target_enabled` exists to report for a rule with no
  # target.
  tenant_assumer_arns = [aws_iam_role.amplify_compute.arn]

  # CROSS-TENANT, AND THE COMMENT ON THE VARIABLE IS THE WARNING TO READ. The
  # pipeline claims whichever job is next and only then learns whose tenant it
  # belongs to, so LeadingKeys cannot constrain it. Until step 7 lands, every ARN
  # here is a principal that CAN read every tenant's rows and is trusted not to by
  # application code alone. Kept to the two that genuinely span partitions.
  service_role_arns = [
    module.platform.worker_task_role_arn,
    module.agentcore.runtime_role_arn,
  ]
}

################################################################################
# Platform: where the queue worker runs. LANE N.
#
# The registry, the log group and two IAM roles are always created and cost
# nothing. The ECS cluster, task definition and service are COUNT-GATED OFF --
# `runtime_enabled` defaults false -- for two reasons, both measured:
#
#   * They are the project's FIRST HOURLY CHARGES. Everything else here is
#     per-invocation (Lambda at reserved concurrency 2, DynamoDB PAY_PER_REQUEST,
#     five AgentCore runtimes that cost nothing idle).
#   * THE DSN'S DATABASE ROLE DECIDES WHETHER TENANT ISOLATION BINDS, and nothing
#     in Terraform can inspect it. Measured 2026-08-28 on PostgreSQL 16.15, one
#     table, one RLS policy, two roles:
#
#       as the TABLE OWNER, no tenant bound      2 of 2 rows visible
#       as a plain application role, unbound     0 rows
#
#     Postgres skips RLS for a superuser, for BYPASSRLS, and for the table owner.
#     A DSN naming the owner makes every policy decoration while `pg_policies`
#     still lists each one.
#
# The module's main.tf carries the full reasoning, including why it creates no
# database and why the API and the web app are deliberately absent.
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
