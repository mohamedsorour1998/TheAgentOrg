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
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          # CONVERSE IS WHAT STRANDS ACTUALLY CALLS, and it is a SEPARATE IAM
          # action from InvokeModel -- not an alias, not covered by it.
          #
          # MEASURED 2026-08-22, after the ARN-shape fix above had already made
          # InvokeModel `allowed`. The runtimes still served fixtures, and the
          # container log named the operation:
          #
          #   botocore.errorfactory.AccessDeniedException: An error occurred
          #   (AccessDeniedException) when calling the ConverseStream operation:
          #   User: .../theagentorg-shared-agentcore-runtime-role/BedrockA...
          #   └ Model id: us.amazon.nova-2-lite-v1:0
          #
          # Simulated on the same profile ARN, all four at once:
          #
          #   bedrock:InvokeModel                      allowed
          #   bedrock:InvokeModelWithResponseStream    allowed
          #   bedrock:Converse                         implicitDeny
          #   bedrock:ConverseStream                    implicitDeny
          #
          # `strands.Agent` streams through the Converse API, so granting only the
          # Invoke pair is granting the actions nothing in this codebase uses. TWO
          # independent things were wrong -- the ARN shape and the action name --
          # and fixing the first is what made the second visible, because until
          # then everything failed at the earlier check.
          #
          # Both Converse forms are granted: `Converse` for a non-streaming call
          # and `ConverseStream` for the streaming one. Which of the two the SDK
          # picks is its choice, not ours, and a grant that covers only the form it
          # happens to use today would break on an SDK upgrade with the same silent
          # fixture fallback this whole sequence exists to end.
          "bedrock:Converse",
          "bedrock:ConverseStream",
        ]
        Resource = [
          # THE FOUNDATION-MODEL GRANT IS CROSS-REGION, AND THAT IS THE WHOLE
          # POINT OF A CROSS-REGION INFERENCE PROFILE.
          #
          # A `us.` profile ROUTES to foundation models in several regions, and
          # Bedrock requires the caller to hold permission on whichever one it
          # picks. Measured with `get-inference-profile`, the profile in
          # `config.BEDROCK_MODEL` fans out to three:
          #
          #   arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-lite-v1:0
          #   arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-2-lite-v1:0
          #   arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-2-lite-v1:0
          #
          # Scoped to one region, two of the three were denied -- simulated
          # 2026-08-22 with the Converse grant already in place:
          #
          #   foundation-model in us-east-1   allowed
          #   foundation-model in us-east-2   implicitDeny
          #   foundation-model in us-west-2   implicitDeny
          #
          # So the call succeeded or failed depending on which region the profile
          # happened to choose, and a failure was indistinguishable from every
          # other denial in this sequence: the agent served its fixture and the job
          # went green.
          #
          # THE WILDCARD IS DELIBERATE AND IS NOT LAZINESS. Enumerating the three
          # regions would break silently the day AWS adds a fourth to the profile
          # -- the same failure, rediscovered, with the same fixture fallback
          # hiding it. What bounds this grant is the ACTION list above (four
          # read-only inference calls, no management actions) and the model
          # wildcard being foundation models only. The account is still scoped on
          # the inference-profile line below, which is the resource that must be
          # ours.
          #
          # THIS WAS THE THIRD OF THREE INDEPENDENT DEFECTS on one statement: the
          # ARN shape, the action name, then the region. Each fix made the next one
          # visible, because until then the call failed at the earlier check. Read
          # the container log, not the simulation, to know which one you are on.
          "arn:aws:bedrock:*::foundation-model/*",
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
