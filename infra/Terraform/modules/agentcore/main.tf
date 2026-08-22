# The Agent Org — AgentCore module.
#
# OWNER: Sorour.
#
# Creates, for the five role agents:
#   - one ECR repository each (arm64 images for AgentCore), keep-last-N lifecycle
#   - one shared IAM runtime role trusted by bedrock-agentcore, allowed to:
#       * write logs
#       * invoke Bedrock models -- BOTH the cross-region inference profile the
#         code names and the foundation models it routes to; see the
#         BedrockInvoke statement, where granting only one was a silent denial
#       * invoke other AgentCore runtimes (agent-to-agent calls in the graph)
#       * pull images from ECR
#
# The AgentCore runtime resources themselves are created by the AgentCore CLI at
# deploy time (see docs/plan/mariam.md) once the images exist — this module lays
# down the registries and the role they assume.

data "aws_region" "current" {}

locals {
  # planner -> theagentorg-shared-planner-agent, etc.
  repos = { for a in var.agents : a => "${var.name}-${a}-agent" }
}

# ── ECR repositories (one per role agent) ─────────────────────────────────────

module "ecr" {
  for_each = local.repos

  source  = "terraform-aws-modules/ecr/aws"
  version = "2.4.0"

  repository_name                 = each.value
  repository_image_tag_mutability = "MUTABLE"
  repository_read_write_access_arns = [
    "arn:aws:iam::${var.account_id}:root"
  ]

  repository_lifecycle_policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire images, keep last ${var.image_retention_count}"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.image_retention_count
        }
        action = { type = "expire" }
      }
    ]
  })

  tags = var.tags
}

# ── AgentCore runtime role (bedrock-agentcore trust) ──────────────────────────

resource "aws_iam_role" "runtime" {
  name = "${var.name}-agentcore-runtime-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "runtime" {
  name = "${var.name}-agentcore-runtime-policy"
  role = aws_iam_role.runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      },
      {
        # BOTH ARN SHAPES ARE REQUIRED, AND THAT IS NOT BELT-AND-BRACES.
        #
        # config.BEDROCK_MODEL defaults to `us.amazon.nova-2-lite-v1:0`. The `us.`
        # prefix makes it a CROSS-REGION INFERENCE PROFILE, not a foundation
        # model, and the two live at different ARN shapes:
        #
        #   arn:aws:bedrock:us-east-1:339712964409:inference-profile/us.amazon.nova-2-lite-v1:0
        #   arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-lite-v1:0
        #
        # Invoking the profile needs InvokeModel on the PROFILE (the thing called)
        # and on the FOUNDATION MODELS it routes to (the things that answer).
        # Grant only one and the call is denied.
        #
        # MEASURED 2026-08-22 against the live account, with only the
        # foundation-model ARN present:
        #
        #   simulate-principal-policy … inference-profile/us.amazon.nova-2-lite-v1:0
        #   implicitDeny
        #   simulate-principal-policy … foundation-model/amazon.nova-2-lite-v1:0
        #   allowed
        #
        # The consequence was the worst available shape. `llm.text()` catches the
        # denial by design, `structured()` returns None, and every model-calling
        # agent falls back to its fixture -- so the deployed pipeline produced
        # FIXTURE output while every job reported green, and the plan comment on
        # the target repo matched fixtures/plan_result.json byte for byte.
        # Nothing anywhere said the model had not answered. A whole week of
        # "verified" cloud runs were fixture runs.
        #
        # Note the profile ARN carries an ACCOUNT and the foundation-model ARN
        # does not. That asymmetry is AWS's, not a typo: inference profiles are
        # account-scoped resources, foundation models are not.
        #
        # Pinned by tests/test_agentcore_iam.py, and re-checked against the live
        # account by scripts/preflight.py check 1 -- a green apply proves only
        # that the policy was written, not that it permits the call.
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/*",
          "arn:aws:bedrock:${data.aws_region.current.region}:${var.account_id}:inference-profile/*",
        ]
      },
      {
        # Agent-to-agent calls inside the graph.
        Sid      = "AgentCoreInvoke"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = ["arn:aws:bedrock-agentcore:${data.aws_region.current.region}:${var.account_id}:runtime/*"]
      },
      {
        Sid      = "EcrPull"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = "*"
      },
    ]
  })
}
