################################################################################
# Run state in DynamoDB: the decision log and the paused-run documents.
#
# ONE TABLE, ONE PARTITION PER RUN. PK `run_id`, SK `ts_event_id`. An event row's
# sort key is `<ts>#<event_id>` and the run's state document sits at the reserved
# sort key `state#current` in the same partition -- see agentorg/log.py, which
# owns both constants.
#
# WHY THE SORT KEY CARRIES THE EVENT ID AND NOT JUST THE TIMESTAMP. DynamoDB
# PutItem REPLACES the item at a (pk, sk) pair; it does not append. Two events
# written in the same clock tick share a timestamp -- ordinary for a stage that
# logs twice in a row -- so a timestamp-only sort key would silently overwrite
# one of them and the log would come back a row short with nothing raised. The
# local backend is a JSONL append and cannot lose a line this way, which is
# exactly why this is the one place the two backends genuinely differ.
# tests/test_state_backend.py pins it.
#
# NO GLOBAL SECONDARY INDEX, and no `dynamodb:Scan` grant. Enumerating runs for
# the approve screen is a Query against one reserved partition (`__runs__`) that
# save() upserts into, not a table scan. A Scan would cost read units
# proportional to the whole table to answer "which runs are paused", and granting
# it would let any holder of the role read every run's audit trail in one call.
################################################################################

resource "aws_dynamodb_table" "runs" {
  name = var.table_name

  # PAY_PER_REQUEST, not provisioned. The traffic is a handful of writes per run
  # and nothing between demos, so a provisioned floor would bill continuously for
  # capacity nobody uses -- and a provisioned ceiling would throttle a burst,
  # which on this table means dropping an audit row.
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "run_id"
  range_key = "ts_event_id"

  # Only the KEY attributes are declared. DynamoDB is schemaless for everything
  # else, and `payload` is deliberately not declared: it carries the whole
  # LogEvent as JSON precisely so that a new optional field on that pydantic
  # model is not a second declaration here, free to drift out of step.
  attribute {
    name = "run_id"
    type = "S"
  }

  attribute {
    name = "ts_event_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  # Server-side encryption with the AWS-managed key. Explicit rather than left to
  # the default, because this table holds the ticket text and diffs of every run.
  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}

################################################################################
# IAM: the four actions agentorg/log.py can issue, on this one table.
#
# The list is derived from the code, not from a convenience set. log._table()
# returns the boto3 RESOURCE interface and this module uses exactly four of its
# methods: put_item, query, get_item, update_item. Nothing here can reach a
# fifth, so nothing here grants a fifth -- and in particular NOT `Scan`, NOT
# `DeleteItem`, and NOT `BatchWriteItem`.
#
# DeleteItem is worth naming as a deliberate omission. This table is an audit
# trail; the module has no delete path and must not gain one by having the
# permission available. A run is superseded by a new state document, never
# removed.
################################################################################

data "aws_iam_policy_document" "table_access" {
  statement {
    sid    = "RunStateTableAccess"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
    ]

    # THIS ONE TABLE. Not a prefix, not a wildcard. A widened resource here would
    # hand every role in var.runtime_role_arns read and write access to every
    # DynamoDB table in an account shared with three other projects.
    resources = [aws_dynamodb_table.runs.arn]
  }
}

resource "aws_iam_policy" "table_access" {
  name        = "${var.name}-run-state-access"
  description = "PutItem/Query/GetItem/UpdateItem on ${var.table_name} only"
  policy      = data.aws_iam_policy_document.table_access.json
  tags        = var.tags
}

# Attached by ARN to the roles the caller names, rather than this module creating
# a role of its own: the AgentCore runtime role and the CI role both already
# exist and are shared, so a module that created its own would leave the real
# callers unable to reach the table.
resource "aws_iam_role_policy_attachment" "table_access" {
  for_each = toset(var.runtime_role_arns)

  role       = element(split("/", each.value), length(split("/", each.value)) - 1)
  policy_arn = aws_iam_policy.table_access.arn
}
