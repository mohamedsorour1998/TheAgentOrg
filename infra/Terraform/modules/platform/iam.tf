# The worker's two IAM roles, and what each one may reach. LANE N, N2.
#
# TWO ROLES, NOT ONE, AND THE SPLIT IS THE POINT. ECS distinguishes them and
# collapsing them is the common shortcut:
#
#   execution role  what the ECS AGENT uses, before the container starts: pull the
#                   image, create the log stream, read the secrets it injects.
#   task role       what the CONTAINER's own code uses at runtime: invoke Bedrock
#                   runtimes, and nothing else.
#
# Giving the container the execution role's permissions would let the worker's own
# code read every secret named in its task definition and re-tag its own image.
# `agentorg/` runs a MODEL that writes a diff and shells out to `git`; the smallest
# possible set of things that code can reach is the whole argument.
#
# NO WILDCARD RESOURCE ANYWHERE, matching modules/ingress's three statements.

# ── THE EXECUTION ROLE ───────────────────────────────────────────────────────

resource "aws_iam_role" "execution" {
  name = "${var.name}-worker-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

# `AmazonECSTaskExecutionRolePolicy` IS DELIBERATELY NOT ATTACHED, for exactly the
# reason modules/ingress refuses `AWSLambdaBasicExecutionRole`: that managed policy
# grants `logs:CreateLogGroup` plus logs on `*`, and ECR pull on `*`. Both are
# broader than this role needs, and the log group is Terraform-managed.
resource "aws_iam_role_policy" "execution" {
  name = "${var.name}-worker-execution-policy"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          # `GetAuthorizationToken` takes no resource -- it is an account-level
          # call, and `"*"` is the only value AWS accepts for it. The two calls
          # that actually read bytes are scoped to our one repository below, which
          # is what bounds this pair.
          Sid      = "EcrLogin"
          Effect   = "Allow"
          Action   = ["ecr:GetAuthorizationToken"]
          Resource = "*"
        },
        {
          Sid      = "PullTheWorkerImageAndNothingElse"
          Effect   = "Allow"
          Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
          Resource = [aws_ecr_repository.worker.arn]
        },
        {
          # `:*` is the log group's STREAMS, not a wildcard over log groups.
          # `CreateLogGroup` is absent: the group is declared in main.tf.
          Sid      = "OwnLogGroupOnly"
          Effect   = "Allow"
          Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = ["${aws_cloudwatch_log_group.worker.arn}:*"]
        },
      ],
      # THE DSN SECRET, ONE ARN, AND ONLY WHEN ONE WAS NAMED.
      #
      # Gated rather than granted with a prefix wildcard, because an empty
      # `queue_dsn_secret_arn` would otherwise produce
      # `Resource: [""]` -- which Terraform accepts and IAM rejects at apply time,
      # failing the apply rather than the plan. The `runtime_enabled` precondition
      # in ecs.tf is what refuses the combination early.
      var.queue_dsn_secret_arn == "" ? [] : [
        {
          Sid      = "ReadTheQueueDsnAndNothingElse"
          Effect   = "Allow"
          Action   = ["secretsmanager:GetSecretValue"]
          Resource = [var.queue_dsn_secret_arn]
        },
      ],
    )
  })
}

# ── THE TASK ROLE: what the worker's own code may do ─────────────────────────

resource "aws_iam_role" "task" {
  name = "${var.name}-worker-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "task" {
  name = "${var.name}-worker-task-policy"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "OwnLogGroupOnly"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.worker.arn}:*"]
      },
      {
        # INVOKE THE FIVE AGENT RUNTIMES. This is what the worker is FOR: each
        # stage reaches its agent through `call_agent`, whose remote branch calls
        # `invoke_agent_runtime` -- and that call REQUIRES `qualifier="DEFAULT"`,
        # measured, or it answers ResourceNotFoundException against a READY runtime
        # with a READY endpoint.
        #
        # Scoped to `runtime/*` in this account and region, matching the agentcore
        # module's `AgentCoreInvoke` statement exactly. Not narrowed to the five
        # names: the runtime ARNs carry a generated suffix
        # (`theagentorg_security-Wa42fz7FCC`) that changes when a runtime is
        # recreated, so an enumerated grant would break on a recreate with a denial
        # `agent_client` classifies as DENIED -- correct, and pointing at the wrong
        # cause.
        Sid      = "InvokeTheAgentRuntimes"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = ["arn:aws:bedrock-agentcore:${data.aws_region.current.region}:${var.account_id}:runtime/*"]
      },
      {
        # BEDROCK DIRECTLY, BECAUSE THE WORKER IS ALSO THE FALLBACK PATH.
        #
        # `REMOTE_AGENTS` defaults FALSE and CLAUDE.md names that default as the
        # demo's fallback: "if the runtimes misbehave, unsetting one variable puts
        # the pipeline back on the path that has been green all week". On that path
        # the model call happens in THIS container, so without this statement the
        # fallback fails the way every model denial in this project has failed --
        # `llm.text()` catches it by design, every agent serves its fixture, and
        # every job reports green.
        #
        # BOTH ARN SHAPES AND ALL FOUR ACTIONS, and every one of those is a
        # separately measured fact from modules/agentcore/main.tf:89-203. Three
        # independent defects on one statement there, each hiding the next:
        #
        #   1. `config.BEDROCK_MODEL` is `us.amazon.nova-2-lite-v1:0` -- the `us.`
        #      prefix makes it a CROSS-REGION INFERENCE PROFILE, not a foundation
        #      model, and the two live at different ARN shapes. Granting only the
        #      foundation-model form: inference-profile implicitDeny.
        #   2. `strands.Agent` calls ConverseStream, which is a SEPARATE IAM action
        #      from InvokeModel -- not an alias, not covered by it. Simulated:
        #      InvokeModel allowed, ConverseStream implicitDeny.
        #   3. The profile fans out to us-east-1, us-east-2 AND us-west-2. Scoped
        #      to one region, two of three were denied and the call succeeded or
        #      failed depending on which region the profile chose.
        #
        # The foundation-model wildcard is deliberate: enumerating three regions
        # breaks silently the day AWS adds a fourth. Note the profile ARN carries an
        # account and the foundation-model ARN does not -- that asymmetry is AWS's,
        # because inference profiles are account-scoped and foundation models are
        # not.
        #
        # `scripts/preflight.py` check 1 is what proves this permits the call; a
        # green apply proves only that it was written.
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream",
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:${data.aws_region.current.region}:${var.account_id}:inference-profile/*",
        ]
      },
    ]
  })
}

# NOTHING GRANTS THE TASK ROLE DynamoDB, AND THAT IS CORRECT TODAY.
#
# `STATE_BACKEND` defaults to `local`, and CLAUDE.md records `dynamodb` as known
# debt: `scripts/run_stage.py:_load` calls `gates._state_path`, which refuses on
# that backend by design, so every cloud stage after `plan` raises. A grant here
# would read as support for a path that does not work.
#
# When that is fixed, `modules/state` already emits the grant -- its
# `runtime_role_arns` input takes a list, so the worker's task role is added there
# rather than duplicated here. One writer for that table's IAM, which is the same
# rule modules/state states about being an audit trail.
