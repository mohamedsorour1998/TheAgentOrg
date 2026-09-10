variable "name" {
  description = "Resource name prefix (e.g. theagentorg-shared)"
  type        = string
}

variable "table_name" {
  description = <<-EOT
    The single tenancy table: organisations, memberships, repositories, runs,
    secrets, budgets and queue jobs, one partition per tenant.

    Defaulted rather than derived from `name`, following modules/state: the
    application will carry the same literal as a config default, and a derived
    name hides that coupling behind string interpolation. Deliberately NOT
    `theagentorg-runs` -- that table exists, holds the decision log, and is a
    different thing with a different key schema.
  EOT
  type        = string
  default     = "theagentorg-tenancy"
}

variable "tenant_assumer_arns" {
  description = <<-EOT
    Principals allowed to assume the tenant-scoped role WITH a session tag.

    In practice one entry: the Amplify SSR compute role, which holds the verified
    Cognito ID token and reads `custom:tenant` from it.

    A list of explicit ARNs rather than an account-root principal, because the
    tag is what authorises the session and anything that can set the tag can
    choose the tenant. `custom:tenant` being immutable in Cognito is only worth
    something if the set of things that may translate it into a session tag is
    also closed.

    Empty by default so the role is created with a trust policy nothing can
    satisfy -- see the plan-time precondition. An empty trust policy fails
    CLOSED, which is the correct direction for a role whose entire purpose is to
    scope database access.
  EOT
  type        = list(string)
  default     = []
}

variable "service_role_arns" {
  description = <<-EOT
    Roles granted CROSS-TENANT access: the worker, and the AgentCore runtime role.

    Every ARN here is a principal that `dynamodb:LeadingKeys` does not constrain,
    because the pipeline claims whichever job is next and only then learns whose
    tenant it belongs to. Keep this list as short as the pipeline genuinely
    needs, and read `docs/design/dynamodb-migration.md` §4 before adding to it:
    until step 7 lands, an ARN here is a principal that can read every tenant's
    rows and is trusted not to by application code alone.
  EOT
  type        = list(string)
  default     = []
}

variable "point_in_time_recovery" {
  description = <<-EOT
    Continuous backups.

    On by default, and the argument is stronger here than for the run table:
    this one holds `secret` rows and the membership graph that decides who may
    approve a gate. A schemaless table has no migration ledger to roll forward
    from, so PITR is the only thing standing between a bad backfill and a
    reconstruction by hand.
  EOT
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to every resource in this module"
  type        = map(string)
  default     = {}
}
