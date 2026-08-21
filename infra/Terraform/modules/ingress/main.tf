# The Agent Org — GitHub webhook ingress module.
#
# OWNER: Task 5 (cloud-native platform lane).
#
# The path a GitHub issue takes into this system:
#
#   GitHub App (Issues subscription)
#     -> POST to a Lambda Function URL          [this module]
#     -> handler verifies HMAC-SHA256 over the raw body   (infra/ingress/handler.py)
#     -> PutEvents on a dedicated EventBridge bus [this module]
#     -> rule matches detail-type "issues" + action "opened"  [this module]
#     -> dispatches run-pipeline.yml in THIS repo             (Task 3)
#
# Creates: the function and its zip, the public Function URL, the log group, the
# webhook secret's CONTAINER (not its value), the event bus, and the rule.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY THE FUNCTION URL IS `authorization_type = "NONE"`, AND WHAT THAT COSTS
# ─────────────────────────────────────────────────────────────────────────────
# GitHub cannot sign a SigV4 request, so `AWS_IAM` would reject every delivery.
# NONE is the only option that works, and the consequence must be stated rather
# than buried:
#
#   THE FUNCTION URL IS INTERNET-REACHABLE AND UNAUTHENTICATED AT THE AWS LAYER.
#
# Anyone who learns the URL can invoke this function. The provider documents
# that creating a URL with NONE automatically adds a `lambda:InvokeFunctionUrl`
# permission "allowing a public endpoint" to the function's resource policy --
# which is why no `aws_lambda_permission` appears below; adding one would be
# redundant, not protective. The same note warns those policies are NOT removed
# on destroy.
#
# What actually defends this endpoint, in order:
#
#   1. The HMAC in the handler. It is the ONLY access control. It runs before
#      the function does any work at all -- before Secrets Manager, before
#      EventBridge -- because a handler that publishes and THEN returns 401 has
#      already started the pipeline. tests/test_ingress_handler.py asserts ZERO
#      PutEvents on every reject path, and proves the assertion is not vacuous
#      by replaying a valid delivery through the same stub.
#   2. `reserved_concurrent_executions` below. Auth cannot stop an anonymous
#      flood from being INVOKED, only from being believed, so the spend is
#      capped structurally.
#   3. IAM narrowed to two actions on two specific ARNs. If the function is ever
#      compromised, that is the whole reachable surface.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY A LAMBDA AND NOT A NATIVE EVENTBRIDGE WEBHOOK
# ─────────────────────────────────────────────────────────────────────────────
# Checked, not assumed. EventBridge has no inbound-webhook API: API destinations
# and connections are OUTBOUND, and a partner event source needs an onboarded
# SaaS partner -- GitHub is not on that list. So something has to terminate the
# HTTPS POST and verify the signature, and that something is this function.

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  function_name = "${var.name}-github-ingress"
  bus_name      = "${var.name}-github-ingress"
  secret_name   = "${var.name}-github-webhook-secret"
}

# ── the deployment package ────────────────────────────────────────────────────
#
# One file, zipped at plan time. No pip install and no layer: the handler's only
# non-stdlib import is boto3, which the Lambda Python runtime already provides.
# That is also why boto3 must NOT be added to agentorg/agents/requirements.txt --
# it would ship a redundant dependency into the five agent images.
#
# output_base64sha256 (not the file's mtime) is what makes a code change land: a
# zip rebuilt with identical contents produces an identical hash and Terraform
# correctly reports no change.

data "archive_file" "handler" {
  type        = "zip"
  source_file = "${var.handler_source_dir}/handler.py"
  output_path = "${path.module}/.build/handler.zip"
}

# ── the webhook secret ────────────────────────────────────────────────────────
#
# Terraform creates the CONTAINER and never the VALUE. A secret written from
# here would be committed in state -- readable by everyone with state access --
# and would drift the moment it were rotated. The value is written once, by a
# human, in step 6 of the task brief, with:
#
#   aws secretsmanager put-secret-value \
#     --secret-id theagentorg-shared-github-webhook-secret \
#     --secret-string '<the secret shown by GitHub when creating the App>'
#
# Until that happens the function returns 500, not 401 -- deliberately. "We
# cannot read our own secret" must never be reported as "your signature is
# wrong", or the first person to debug it goes hunting a signature bug that does
# not exist.
#
# recovery_window_in_days = 0 so a destroy/apply cycle can reuse the name; the
# default 30-day window would otherwise make the name unavailable and the next
# apply fail with InvalidRequestException on a secret scheduled for deletion.

resource "aws_secretsmanager_secret" "webhook" {
  name        = local.secret_name
  description = "GitHub App webhook secret. VALUE IS WRITTEN BY HAND, never by Terraform -- see this module's main.tf."

  recovery_window_in_days = 0

  tags = var.tags
}

# ── the event bus and its rule ────────────────────────────────────────────────
#
# A dedicated bus, not `default`. The default bus carries every AWS service
# event in the account, which is shared with the rosettacloud_* projects, so a
# rule there would sit in someone else's traffic and a mistake in its pattern
# would match their events. A named bus makes the IAM grant below meaningful
# too: PutEvents is granted on THIS bus and no other.

resource "aws_cloudwatch_event_bus" "github" {
  name = local.bus_name

  tags = var.tags
}

# The pattern's `detail-type` is GitHub's own event name, forwarded verbatim by
# the handler from the `x-github-event` header. That coupling is the fragile
# part: invent a detail-type in the handler and this rule matches NOTHING, the
# bus still accepts the event, no rule fires, and nothing anywhere turns red.
# tests/test_ingress_handler.py::test_the_detail_type_is_githubs_event_name_verbatim
# pins the handler side of it and says why in its docstring.
#
# `action: ["opened"]` filters at the bus rather than in the handler on purpose:
# every Issues delivery (edited, labeled, closed, reopened, ...) reaches
# EventBridge and is recorded, but only an opened issue starts a pipeline run.
# Filtering in the handler instead would make "we never saw it" and "we saw it
# and ignored it" indistinguishable.
resource "aws_cloudwatch_event_rule" "issue_opened" {
  name           = "${var.name}-github-issue-opened"
  description    = "A GitHub issue was opened on the target repo -- start a pipeline run"
  event_bus_name = aws_cloudwatch_event_bus.github.name

  event_pattern = jsonencode({
    source      = [var.event_source]
    detail-type = ["issues"]
    detail = {
      action = ["opened"]
    }
  })

  tags = var.tags
}

# NO TARGET IS ATTACHED HERE, and that is deliberate rather than unfinished.
# The target is a GitHub Actions `workflow_dispatch` of run-pipeline.yml, which
# EventBridge can only reach through an API destination plus a connection
# holding a GitHub token -- and run-pipeline.yml is Task 3's file, which does not
# exist yet. Attaching a target now would either point at a workflow that is not
# there (a rule that fires into a 404 on every issue) or require inventing its
# input shape ahead of the file. The rule is what this task owns; the wiring is
# recorded in outputs.tf as the follow-on.

# ── the function's role ───────────────────────────────────────────────────────

resource "aws_iam_role" "ingress" {
  name = "${local.function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

# Exactly three statements, each scoped to one ARN. No wildcard resource
# anywhere, including the logs statement -- the AWS-managed
# AWSLambdaBasicExecutionRole grants logs on `*`, which is why it is not
# attached: this function's log group is created below and is the only one it
# can write to.
#
# The reason to keep this tight is the first paragraph of this file. A function
# on a public unauthenticated URL should be able to reach precisely two things:
# the secret it verifies with, and the bus it publishes to.
resource "aws_iam_role_policy" "ingress" {
  name = "${local.function_name}-policy"
  role = aws_iam_role.ingress.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "OwnLogGroupOnly"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        # `.arn` has the API's `:*` suffix stripped by the provider, and
        # PutLogEvents needs the stream-level wildcard, so it is re-added.
        # CreateLogGroup is absent: the group is Terraform-managed below, so the
        # function has no business creating one.
        Resource = ["${aws_cloudwatch_log_group.ingress.arn}:*"]
      },
      {
        Sid      = "ReadTheWebhookSecretAndNothingElse"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.webhook.arn]
      },
      {
        Sid      = "PublishToTheIngressBusAndNothingElse"
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.github.arn]
      },
    ]
  })
}

# ── the log group ─────────────────────────────────────────────────────────────
#
# Declared rather than left to Lambda's implicit creation, for two reasons: the
# retention above applies (an implicitly created group never expires, and this
# function's log volume is driven by public traffic), and the IAM statement
# above can name it instead of granting logs on `*`.

resource "aws_cloudwatch_log_group" "ingress" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days

  tags = var.tags
}

# ── the function ──────────────────────────────────────────────────────────────

resource "aws_lambda_function" "ingress" {
  function_name = local.function_name
  role          = aws_iam_role.ingress.arn
  handler       = "handler.handler"
  runtime       = var.python_runtime
  architectures = ["arm64"]

  filename         = data.archive_file.handler.output_path
  source_code_hash = data.archive_file.handler.output_base64sha256

  # 10s: the handler does one HMAC, at most one GetSecretValue on a cold start,
  # and one PutEvents. Anything slower is a failure, and a long timeout on a
  # public endpoint just means paying longer for each abusive request.
  timeout     = 10
  memory_size = 256

  # THE SPEND CAP. See the variable's own description -- this is required, not
  # tuning, because the URL is public and unauthenticated.
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      # The ARN, not the secret. The handler reads the value at runtime through
      # the IAM grant above; nothing secret is ever in this config or in state.
      WEBHOOK_SECRET_ARN = aws_secretsmanager_secret.webhook.arn
      EVENT_BUS_NAME     = aws_cloudwatch_event_bus.github.name
      EVENT_SOURCE       = var.event_source
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.ingress.name
  }

  # Without this the first invocation races the group's creation and Lambda
  # implicitly creates one with no retention, which the IAM statement above then
  # does not cover. The policy dependency is explicit for the same class of
  # reason: a function that starts before its role policy exists fails its first
  # deliveries with AccessDenied.
  depends_on = [
    aws_cloudwatch_log_group.ingress,
    aws_iam_role_policy.ingress,
  ]

  tags = var.tags
}

# ── the public endpoint ───────────────────────────────────────────────────────
#
# `authorization_type = "NONE"`. Read the header of this file before changing
# it: this is the internet-facing, unauthenticated entry point, and the HMAC in
# the handler is the only thing standing behind it.

resource "aws_lambda_function_url" "ingress" {
  function_name      = aws_lambda_function.ingress.function_name
  authorization_type = "NONE"
}
