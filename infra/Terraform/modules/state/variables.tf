variable "name" {
  description = "Resource name prefix (e.g. theagentorg-shared)"
  type        = string
}

variable "table_name" {
  description = <<-EOT
    The DynamoDB table holding every run's events and its current state document.

    Defaulted rather than derived from `name` because agentorg/common/config.py
    carries the same literal as STATE_TABLE's default, and the application reads
    that env var at import time. Two places, one value: if this changes, the
    workflow's STATE_TABLE must change with it, and a derived name would hide
    that coupling behind string interpolation.
  EOT
  type        = string
  default     = "theagentorg-runs"
}

variable "runtime_role_arns" {
  description = <<-EOT
    Roles allowed the four table actions. The AgentCore runtime role and the CI
    role, and nothing else.

    A list rather than a wildcard principal because this table holds the audit
    trail of every human gate decision. A run's approvals are the one thing in
    this system a reader must be able to trust was not written by something else.
  EOT
  type        = list(string)
}

variable "point_in_time_recovery" {
  description = <<-EOT
    Continuous backups for the run table.

    On by default: the table is the decision log, and PITR is the difference
    between "a bad deploy truncated the audit trail" and "the audit trail is
    gone". It is billed on stored bytes, and this table stores kilobytes per run.
  EOT
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to every resource in this module"
  type        = map(string)
  default     = {}
}
