# The Agent Org — AgentCore infrastructure.
#
# OWNER: Sorour. Modeled on astrolabe/infra/_archived/agents/main.tf.
#
# Creates, per role agent:
#   - an ECR repository (arm64 images for AgentCore)
#   - one shared IAM role trusted by bedrock-agentcore, allowed to:
#       * write logs
#       * invoke Bedrock models (the LLM behind every agent)
#       * invoke other AgentCore runtimes (agent-to-agent calls in the graph)
#       * pull images from ECR
#
# The AgentCore runtime resources themselves are added once the images exist
# (week 3 deploy). Mariam co-owns that step — see docs/plan/mariam.md.

locals {
  agent_repos = toset([
    "${var.project}-planner-agent",
    "${var.project}-developer-agent",
    "${var.project}-reviewer-agent",
    "${var.project}-security-agent",
    "${var.project}-sre-agent",
  ])
}

# ── ECR repositories ──────────────────────────────────────────────────────────

resource "aws_ecr_repository" "agents" {
  for_each = local.agent_repos

  name                 = each.key
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "agents" {
  for_each   = local.agent_repos
  repository = aws_ecr_repository.agents[each.key].name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last ${var.image_retention_count} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.image_retention_count
      }
      action = { type = "expire" }
    }]
  })
}

# ── IAM role (bedrock-agentcore trust) ────────────────────────────────────────

resource "aws_iam_role" "agents" {
  name = "${var.project}-agentcore-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "agents" {
  name = "${var.project}-agentcore-policy"
  role = aws_iam_role.agents.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        # Agent-to-agent calls inside the graph.
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = "*"
      },
    ]
  })
}
