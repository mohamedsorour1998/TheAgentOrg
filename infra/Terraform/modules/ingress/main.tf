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
# `retention_in_days` below applies (an implicitly created group never expires,
# and this function's log volume is driven by public traffic), and the IAM
# statement above can name it instead of granting logs on `*`.

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

# ─────────────────────────────────────────────────────────────────────────────
# THE RULE'S TARGET: dispatching run-pipeline.yml
# ─────────────────────────────────────────────────────────────────────────────
#
# Added after run-pipeline.yml existed. The note above -- "NO TARGET IS ATTACHED
# HERE" -- described the state before this block and is left in place because it
# still explains WHY the target could not be written earlier.
#
# The path an opened issue now takes to a pipeline run:
#
#   rule (issue opened)
#     -> input_transformer  reshapes GitHub's issue payload into the REST
#                           dispatch body
#     -> api_destination    POST .../actions/workflows/run-pipeline.yml/dispatches
#     -> connection         supplies `Authorization: Bearer <token>`
#
# ── EVERYTHING HERE IS COUNT-GATED, AND THAT IS THE LOAD-BEARING DECISION ────
#
# `aws_cloudwatch_event_connection` with API_KEY auth needs the token's VALUE as
# a configuration value, so Terraform has to READ it at plan time. The secret
# exists but its value is written by a HUMAN, and reading a secret with no
# version fails the PLAN, not the apply. Ungated, that would turn terraform.yml
# -- currently green end to end -- red on every run until somebody minted a
# token, and the failure would be blamed on Terraform rather than on the missing
# value.
#
# So the whole target is created only when `dispatch_token_secret_name` is set.
# Empty is the default: this module applies exactly as it did before, and the
# rule has no target. `terraform validate` passes in both states -- measured, not
# assumed.
#
# ── WHY API_KEY AND NOT OAUTH ────────────────────────────────────────────────
#
# The connection's auth types are API_KEY, BASIC and OAUTH_CLIENT_CREDENTIALS.
# GitHub's REST API takes a bearer token in a header, which is API_KEY with the
# key literally named `Authorization` and the value `Bearer <token>` -- EventBridge
# sends `<key>: <value>` verbatim, it does not prepend anything. OAuth here would
# mean a GitHub App's client-credentials flow, which GitHub does not offer for
# this endpoint.
#
# THE TOKEN ENDS UP IN TERRAFORM STATE. Unavoidable with API_KEY: the provider
# takes the value through config, and state lives in S3. The mitigation is scope,
# not secrecy -- a fine-grained token with `actions: write` on this ONE repository
# and nothing else, rotated after the demo. Recorded here because a reader who
# assumes otherwise would grant it more than it needs.

data "aws_secretsmanager_secret_version" "dispatch_token" {
  count     = var.dispatch_token_secret_name == "" ? 0 : 1
  secret_id = var.dispatch_token_secret_name
}

locals {
  # Whether the target wiring exists at all. One expression, five readers.
  dispatch_enabled = var.dispatch_token_secret_name == "" ? 0 : 1

  # A bare-string secret, or one key out of a JSON secret. Both shapes exist for
  # the same reason handler.py accepts both for the webhook secret: which one you
  # get depends on how the human wrote it.
  dispatch_token = local.dispatch_enabled == 0 ? "" : (
    var.dispatch_token_secret_json_key == ""
    ? data.aws_secretsmanager_secret_version.dispatch_token[0].secret_string
    : jsondecode(data.aws_secretsmanager_secret_version.dispatch_token[0].secret_string)[var.dispatch_token_secret_json_key]
  )

  # The REST endpoint that starts a run. The workflow FILE NAME is accepted in
  # place of a numeric workflow id, which is what keeps this readable and what
  # makes the coupling to run-pipeline.yml visible in the URL.
  dispatch_endpoint = "https://api.github.com/repos/${var.dispatch_repo}/actions/workflows/${var.dispatch_workflow_file}/dispatches"
}

resource "aws_cloudwatch_event_connection" "github_dispatch" {
  count = local.dispatch_enabled

  name               = "${var.name}-github-dispatch"
  description        = "Bearer token for dispatching run-pipeline.yml. Value read from Secrets Manager, never written by Terraform."
  authorization_type = "API_KEY"

  auth_parameters {
    api_key {
      # EventBridge sends this header verbatim, so the scheme belongs in the
      # VALUE. `key = "Bearer"` would send `Bearer: <token>`, which GitHub
      # ignores -- and an ignored auth header on this endpoint answers 404, not
      # 401, because an unauthenticated caller cannot see the workflow at all.
      # That 404 reads as "the workflow does not exist" and sends the next person
      # to look for a missing file.
      key   = "Authorization"
      value = "Bearer ${local.dispatch_token}"
    }
  }
}

resource "aws_cloudwatch_event_api_destination" "github_dispatch" {
  count = local.dispatch_enabled

  name                = "${var.name}-run-pipeline-dispatch"
  description         = "POST workflow_dispatch to run-pipeline.yml in ${var.dispatch_repo}"
  invocation_endpoint = local.dispatch_endpoint
  http_method         = "POST"
  connection_arn      = aws_cloudwatch_event_connection.github_dispatch[0].arn

  # One dispatch per second is far above real traffic -- a human opens issues one
  # at a time -- and it is the second spend cap in this module, for the same
  # reason as the Lambda's reserved concurrency: the front door is public. A
  # signed flood cannot become a thousand pipeline runs.
  invocation_rate_limit_per_second = 1
}

resource "aws_iam_role" "dispatch" {
  count = local.dispatch_enabled

  name = "${var.name}-github-dispatch-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

# One action on one ARN. An API-destination target is the one EventBridge target
# type that needs a role at all, and this is the whole of what it may do.
resource "aws_iam_role_policy" "dispatch" {
  count = local.dispatch_enabled

  name = "${var.name}-github-dispatch-policy"
  role = aws_iam_role.dispatch[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeTheRunPipelineDestinationAndNothingElse"
      Effect   = "Allow"
      Action   = ["events:InvokeApiDestination"]
      Resource = [aws_cloudwatch_event_api_destination.github_dispatch[0].arn]
    }]
  })
}

# ── THE INPUT TRANSFORMER, WHICH IS WHERE THE TYPES BITE ─────────────────────
#
# `input_paths` are JSONPath-ish selectors over the EventBridge envelope, so the
# issue body sits under `$.detail` -- the handler puts GitHub's raw payload
# there verbatim.
#
# EVERY VALUE IN `inputs` IS QUOTED, INCLUDING THE BOOLEANS. The REST dispatch
# API rejects real JSON booleans inside `inputs`; each one must be a string. So
# the template writes "false", not false -- and `scripts/run_stage.py:flag`
# parses exactly those strings, refusing anything it does not recognise rather
# than defaulting to False. tests/test_run_pipeline_workflow.py pins both halves.
#
# `poisoned` is hardcoded "false" and NOT read off the payload. A label is
# attached AFTER an issue is opened, so `$.detail.issue.labels` is reliably empty
# on the event this rule matches -- reading it would produce a clean run while
# looking as though it honoured the label. The poisoned demo run is dispatched by
# hand with `gh workflow run -f poisoned=true`. Stated rather than left as an
# apparent oversight.
#
# `auto_approve` is "false" so an issue-triggered run pauses at all three
# Environments. An issue is opened by anyone with access to the repository; a run
# it starts must not approve itself.
#
# `trigger` is "issue", and it is the ONLY thing that can say so. EventBridge
# dispatches this workflow through the same REST API `gh workflow run` uses, so
# `github.event_name` reads `workflow_dispatch` for both -- MEASURED on run
# 32542152671, started by opening issue #15 and still reporting
# `event: workflow_dispatch`. No Actions context field distinguishes them, so the
# provenance has to be SENT, and this is the only sender.
#
# It MUST differ from run-pipeline.yml's default of "manual". Identical values
# would make the field prove nothing -- a run recording `manual` would be
# indistinguishable from a run whose trigger was never set. Quoted, like every
# other value here.
#
# The issue NUMBER becomes ticket_id and the TITLE becomes ticket_text. The body
# is deliberately not used: it is unbounded, may hold anything, and goes straight
# into an agent prompt.
resource "aws_cloudwatch_event_target" "run_pipeline" {
  count = local.dispatch_enabled

  rule           = aws_cloudwatch_event_rule.issue_opened.name
  event_bus_name = aws_cloudwatch_event_bus.github.name
  target_id      = "run-pipeline-dispatch"
  arn            = aws_cloudwatch_event_api_destination.github_dispatch[0].arn
  role_arn       = aws_iam_role.dispatch[0].arn

  input_transformer {
    input_paths = {
      issue_number = "$.detail.issue.number"
      issue_title  = "$.detail.issue.title"
    }

    # <issue_number> and <issue_title> are EventBridge's substitution syntax, not
    # Terraform's. The JSON is written by hand rather than through jsonencode()
    # because those placeholders must survive unquoted-into-a-string-position,
    # which jsonencode would escape.
    input_template = <<-EOT
      {
        "ref": "${var.dispatch_ref}",
        "inputs": {
          "ticket_id": "<issue_number>",
          "ticket_text": "<issue_title>",
          "poisoned": "false",
          "auto_approve": "false",
          "trigger": "issue"
        }
      }
    EOT
  }

  # A dispatch that fails is a demo that does not start, so failures are retried
  # and then KEPT. Without a dead-letter queue a 401 from a rotated token, or a
  # 404 from a workflow not yet on `main`, disappears silently: the rule reports
  # healthy, the run never appears, and there is nothing to read afterwards.
  retry_policy {
    maximum_retry_attempts       = 3
    maximum_event_age_in_seconds = 600
  }

  dead_letter_config {
    arn = aws_sqs_queue.dispatch_dlq[0].arn
  }
}

resource "aws_sqs_queue" "dispatch_dlq" {
  count = local.dispatch_enabled

  name                      = "${var.name}-github-dispatch-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum

  tags = var.tags
}

# EventBridge needs an explicit resource-policy grant to write to the queue; the
# target's role_arn does not cover the DLQ. Scoped by SourceArn to this one rule,
# so no other rule in the account can use this queue as its dead-letter.
resource "aws_sqs_queue_policy" "dispatch_dlq" {
  count = local.dispatch_enabled

  queue_url = aws_sqs_queue.dispatch_dlq[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowThisRuleToDeadLetterHere"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.dispatch_dlq[0].arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.issue_opened.arn }
      }
    }]
  })
}
