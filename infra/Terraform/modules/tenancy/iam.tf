################################################################################
# TWO POLICIES, AND THE DIFFERENCE BETWEEN THEM IS THE WHOLE ISOLATION STORY.
#
# `docs/design/dynamodb-migration.md` §4 is the argument. Summarised: there are
# two kinds of caller and only one of them can be constrained by `LeadingKeys`.
#
#   the WEB app   acts AS a tenant  -> constrained by LeadingKeys  (STRONGER
#                                      than the Postgres path it replaces)
#   the PIPELINE  spans tenants     -> cannot be, by definition    (WEAKER)
#
# The second is a real regression and it is not papered over here. Today the
# worker connects as `agentorg_app`, a non-owning role, and Postgres RLS enforces
# isolation for it too -- measured, owner sees 2 tenants' rows and `agentorg_app`
# sees 1. `tenant_scoped` below restores that for the web path. Step 7 of the
# plan (per-tenant AssumeRole in the worker) is what restores it for the pipeline,
# and until that lands the `service` policy is genuinely broader than what it
# replaces.
################################################################################

################################################################################
# The tenant-scoped policy. Assumed with a session tag, never attached to a user.
#
# THE TAG COMES FROM COGNITO AND CANNOT BE CHOSEN BY THE CALLER, which is what
# makes this stronger than `SET agentorg.tenant_id`. `custom:tenant` is declared
# `Mutable: False` and is absent from the app client's `WriteAttributes`, so a
# signed-in user cannot rewrite it and a sign-up cannot set it -- and it was
# MEASURED on 2026-09-09 to be unsettable even for a user who never had one
# (`InvalidParameterException: user.custom:tenant: Attribute cannot be updated`).
# So the value that authorises the session is one only an administrator ever
# wrote. No application code sits between that claim and this condition.
################################################################################

data "aws_iam_policy_document" "tenant_scoped" {
  statement {
    sid    = "OwnTenantPartitionOnly"
    effect = "Allow"

    # No Scan, no BatchWriteItem, for `modules/state`'s reason: nothing in the
    # accessor layer issues either, so nothing here grants either. DeleteItem IS
    # granted, unlike the run-state table -- `accessors` has real delete paths
    # (a repository unlinked, a secret rotated) and that table is an audit trail
    # while this one is not.
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:ConditionCheckItem",
      "dynamodb:TransactWriteItems",
      "dynamodb:TransactGetItems",
    ]

    # THE BASE TABLE ONLY -- the index ARNs are deliberately absent. See the
    # explicit Deny below, which is what actually enforces it.
    resources = [aws_dynamodb_table.tenancy.arn]

    condition {
      # `ForAllValues:` because a Query or a transaction can name several keys and
      # the condition must hold for EVERY one of them. With plain
      # `StringEquals` a request mixing one permitted partition with one
      # forbidden partition would be ALLOWED on the strength of the permitted
      # one -- which is the fail-open direction, and the exact shape this
      # repository keeps finding.
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["TENANT#$${aws:PrincipalTag/tenant}"]
    }
  }

  ##############################################################################
  # AN EXPLICIT DENY ON THE INDEXES, because `LeadingKeys` DOES NOT DEFEND THEM.
  #
  # That condition compares against the BASE TABLE's partition key. A Query
  # against `gsi1` is keyed on `gsi1pk` -- a run id, with no tenant in it -- so
  # the condition has nothing to match and the request is not constrained. An
  # Allow that merely omits the index ARNs would be enough today, and would stop
  # being enough the moment somebody widens the resource list to
  # `${arn}` + `${arn}/index/*` while "fixing" a permissions error.
  #
  # A Deny cannot be overridden by any later Allow, so this survives that edit.
  # The web path never needs an index: GSI1 is KEYS_ONLY and its follow-up
  # GetItem lands on the base table, where LeadingKeys applies.
  ##############################################################################
  statement {
    sid       = "NoIndexAccessForTenantScopedCallers"
    effect    = "Deny"
    actions   = ["dynamodb:Query", "dynamodb:Scan"]
    resources = ["${aws_dynamodb_table.tenancy.arn}/index/*"]
  }
}

# The role the web app assumes PER REQUEST, tagged with the tenant from the
# verified Cognito claim. `max_session_duration` is the AWS floor of one hour:
# these credentials are minted per request and cached for the request, so a long
# ceiling buys nothing and widens the window on a leaked credential.
#
# **COUNT-GATED, BECAUSE A TRUST POLICY WITH NO PRINCIPALS CANNOT BE CREATED.**
# The first draft left `tenant_assumer_arns` empty and reasoned that a role
# nothing can assume is the correct fail-closed default. MEASURED, and it is not
# a default at all -- IAM refuses the resource outright:
#
#   Error: creating IAM Role (theagentorg-shared-tenancy-scoped):
#   MalformedPolicyDocument: The passed in policy has a statement with no
#   principals!
#
# So "empty means nobody can assume it" was never a state this module could
# reach; the apply simply failed, and it failed AFTER the DynamoDB table had
# already been attempted. The gate is `modules/ingress`'s pattern instead -- what
# costs nothing is always created, what needs configuration is counted off -- and
# `tenant_scoped_is_assumable` reports which state an apply left behind, exactly
# as `dispatch_target_enabled` does for a rule with no target.
resource "aws_iam_role" "tenant_scoped" {
  count = length(var.tenant_assumer_arns) > 0 ? 1 : 0

  name                 = "${var.name}-tenancy-scoped"
  max_session_duration = 3600
  tags                 = var.tags

  assume_role_policy = data.aws_iam_policy_document.assume_tenant_scoped.json
}

data "aws_iam_policy_document" "assume_tenant_scoped" {
  statement {
    sid    = "CallersThatMaySetATenantTag"
    effect = "Allow"

    dynamic "principals" {
      # NO PRINCIPALS BLOCK AT ALL when the list is empty, rather than an empty
      # `identifiers`. IAM rejects the second (`a statement with no
      # principals!`), and the role above is counted off in that case anyway --
      # but a data source that renders invalid JSON is a trap waiting for the
      # next person who references it.
      for_each = length(var.tenant_assumer_arns) > 0 ? [1] : []
      content {
        type        = "AWS"
        identifiers = var.tenant_assumer_arns
      }
    }

    # `TagSession` is REQUIRED and is not implied by AssumeRole. Without it the
    # call fails at STS with `AccessDenied`, and the tempting fix is to drop the
    # tag -- which produces a session whose `aws:PrincipalTag/tenant` is empty,
    # so `LeadingKeys` compares against `TENANT#` and matches NOTHING. That fails
    # closed, which is correct, and reads as "the database is broken" rather than
    # as a missing permission.
    actions = ["sts:AssumeRole", "sts:TagSession"]
  }
}

################################################################################
# INLINE, NOT A MANAGED POLICY PLUS AN ATTACHMENT, AND THAT IS A MEASUREMENT
# RATHER THAN A STYLE CHOICE.
#
# Both halves of this module were `aws_iam_policy` + `aws_iam_role_policy_attachment`
# until 2026-09-15, and the apply for `e2e4423` FAILED on all three attachments:
#
#   AccessDenied ... iam:AttachRolePolicy on resource:
#     role/theagentorg-shared-tenancy-scoped
#     role/theagentorg-shared-agentcore-runtime-role
#     role/theagentorg-shared-worker-task-role
#
# The CI role's policy DOES name `iam:AttachRolePolicy` -- and grants it on
# `Resource: arn:aws:iam::339712964409:policy/theagentorg-shared-*`. That action
# is authorised against the ROLE being written to, not against the policy being
# attached (the policy travels as the `iam:PolicyARN` condition key), so the
# statement can never match and the grant is dead text. Measured with
# `simulate-principal-policy`, which is this repository's instrument for exactly
# this question:
#
#   iam:AttachRolePolicy  role/theagentorg-shared-tenancy-scoped     implicitDeny
#   iam:AttachRolePolicy  policy/theagentorg-shared-tenancy-scoped   implicitDeny
#   iam:PutRolePolicy     role/theagentorg-shared-tenancy-scoped     allowed
#   iam:PutRolePolicy     role/theagentorg-shared-worker-task-role   allowed
#   iam:PutRolePolicy     role/theagentorg-shared-agentcore-runtime-role  allowed
#
# So inline is what CI can already write. The alternative -- widening
# `github-actions-role` to attach any managed policy to any `theagentorg-shared-*`
# role -- is refused on the precedent this project already set for
# `iam:CreateServiceLinkedRole`: that one was performed by hand once rather than
# granting CI standing power to mint roles for any AWS service.
#
# **IT IS ALSO THE STRONGER FORM HERE, AND THAT IS NOT A CONSOLATION.** The failed
# apply left the account in a state worth remembering: the role existed, the
# managed policy existed, and the two were not connected -- so the role granted
# NOTHING while `aws iam get-policy` answered perfectly. `preflight_tenancy.py`
# read the document through `get-policy` and reported check 8 PASSED, all four
# rows correct, against a principal subject to none of it. An inline policy cannot
# reach that state: the document IS the role's, or `get-role-policy` raises.
#
# THE COST, STATED: an inline policy takes no tags, so `var.tags` does not reach
# these two documents. They are named `${var.name}-*` and live on roles that carry
# the tags, which is how they stay findable.
################################################################################
resource "aws_iam_role_policy" "tenant_scoped" {
  count = length(aws_iam_role.tenant_scoped)

  name   = "${var.name}-tenancy-scoped"
  role   = aws_iam_role.tenant_scoped[0].id
  policy = data.aws_iam_policy_document.tenant_scoped.json
}

################################################################################
# The service policy: the pipeline, which legitimately spans tenants.
#
# NO `LeadingKeys` CONDITION, because there is no single tenant to compare
# against -- the worker claims whichever job is next and only then learns whose
# it is. Index access IS granted here: `gsi2` is how `queue.claim` finds a READY
# job at all.
#
# **THIS IS THE WEAKER HALF AND IT IS NOT PRETENDING OTHERWISE.** Until step 7,
# what keeps a stage inside its tenant is the accessor building `TENANT#<t>`
# correctly -- application code, where today Postgres RLS enforces it regardless.
# The plan marks that step MANDATORY and says the migration should not proceed
# without it.
################################################################################

data "aws_iam_policy_document" "service" {
  statement {
    sid    = "CrossTenantForThePipelineOnly"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:ConditionCheckItem",
      "dynamodb:TransactWriteItems",
      "dynamodb:TransactGetItems",
    ]

    resources = [
      aws_dynamodb_table.tenancy.arn,
      "${aws_dynamodb_table.tenancy.arn}/index/*",
    ]
  }

  # The one action that is refused to EVERY caller, in both policies. A Scan
  # reads the whole table -- every tenant's rows in one call, billed and
  # returned -- so it is the single request that most resembles the breach this
  # subsystem is built to prevent. Nothing in `accessors.py` issues one, and a
  # Deny means nothing can start.
  statement {
    sid     = "NeverScan"
    effect  = "Deny"
    actions = ["dynamodb:Scan"]
    resources = [
      aws_dynamodb_table.tenancy.arn,
      "${aws_dynamodb_table.tenancy.arn}/index/*",
    ]
  }
}

# INLINE for the reason spelled out above the tenant-scoped policy: CI holds
# `iam:PutRolePolicy` on `role/theagentorg-shared-*` and does not hold
# `iam:AttachRolePolicy` on anything at all.
#
# ONE DOCUMENT, WRITTEN ONCE PER ROLE. With a managed policy the same ARN was
# attached to every principal here; inline means one copy per role, all rendered
# from `data.aws_iam_policy_document.service` so there is still exactly one
# declaration of what the pipeline may do. The `element(split(...))` is unchanged:
# `aws_iam_role_policy.role` takes a name, and `service_role_arns` carries ARNs
# because a module output is the only ARN this root can hand over without
# assembling one from account id and name.
resource "aws_iam_role_policy" "service" {
  for_each = toset(var.service_role_arns)

  name   = "${var.name}-tenancy-service"
  role   = element(split("/", each.value), length(split("/", each.value)) - 1)
  policy = data.aws_iam_policy_document.service.json
}
