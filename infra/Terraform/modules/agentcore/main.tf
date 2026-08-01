# The Agent Org — AgentCore module.
#
# OWNER: Sorour.
#
# Creates, for the five role agents:
#   - one ECR repository each (arm64 images for AgentCore), keep-last-N lifecycle
#   - one shared IAM runtime role trusted by bedrock-agentcore, allowed to:
#       * write logs
#       * invoke Bedrock foundation models (the LLM behind every agent)
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
        Sid      = "BedrockInvoke"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = ["arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/*"]
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
