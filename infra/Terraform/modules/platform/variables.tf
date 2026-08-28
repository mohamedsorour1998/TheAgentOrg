# The platform module's inputs. LANE N, task N2.

variable "name" {
  description = "Resource name prefix, e.g. theagentorg-shared"
  type        = string
}

variable "account_id" {
  description = "This account's id. Used to scope the ECR pull grant to our own registry."
  type        = string
}

variable "image_retention_count" {
  description = "How many images to keep per repository. Same default as the agentcore module's, and set from the same root variable so the two cannot drift."
  type        = number
  default     = 5
}

# ── THE RUNTIME GATE ─────────────────────────────────────────────────────────
#
# COUNT-GATED OFF BY DEFAULT, AND THAT IS THE SAME PATTERN modules/ingress USES
# FOR ITS DISPATCH TARGET -- for a related but distinct reason, so both are stated.
#
# The ingress gate exists because an API_KEY connection needs a secret's VALUE at
# PLAN time, and an ungated read of a secret nobody has written fails the plan.
# This gate exists because the resources behind it SPEND CONTINUOUSLY and the code
# they would run has a MEASURED defect.
#
# THE DEFECT, measured 2026-08-28 against a real PostgreSQL 16.15 -- the first time
# this repository's Postgres dialect has ever been executed:
#
#   psycopg.errors.DatatypeMismatch: column "poisoned" is of type integer but
#   expression is of type boolean
#   HINT:  You will need to rewrite or cast the expression.
#
# raised from `agentorg/queue/_sql.py:369`, on the FIRST `enqueue`. So a worker
# service started against a Postgres queue today reaches READY, reports healthy,
# polls, and fails on every job. `agentorg/queue/` is Lane A's file and this lane
# does not edit it; the gate is what keeps that fact from becoming a running bill.
#
# WHY A COUNT GATE AND NOT SIMPLY OMITTING THE RESOURCES. Infrastructure that does
# not exist in code cannot be reviewed, planned, or costed, and the next person
# writes it from scratch under time pressure. `terraform plan` with this true is the
# artifact that says exactly what an apply would create -- which is what the task
# asks this lane to be able to state.
variable "runtime_enabled" {
  description = "Create the ECS cluster, the worker task definition and its service. FALSE by default: these resources spend continuously, and the Postgres queue dialect they would run has a measured DatatypeMismatch on every enqueue (agentorg/queue/_sql.py:369). Turn on only after that is fixed and a worker has been observed claiming a job."
  type        = bool
  default     = false
}

variable "worker_image" {
  description = "The full ECR image URI the worker service runs, tagged with a commit SHA. Empty is refused when runtime_enabled is true: `:latest` cannot tell you which commit is running, which is why deploy.yml tags both."
  type        = string
  default     = ""
}

variable "queue_dsn_secret_arn" {
  description = "Secrets Manager ARN holding the worker's QUEUE_DSN. THIS MODULE DOES NOT CREATE A DATABASE -- see main.tf's note. Empty is refused when runtime_enabled is true: a worker with no DSN falls back to a sqlite file inside its own container, so two tasks would never see each other's jobs and both would run every stage."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subnets the worker task runs in. Required when runtime_enabled is true. A worker is a POLLER, not a listener -- it needs egress to Bedrock, GitHub and its database, and nothing needs to reach it, so no load balancer and no ingress rule appear in this module."
  type        = list(string)
  default     = []
}

variable "vpc_id" {
  description = "The VPC holding subnet_ids. Required when runtime_enabled is true; used only for the worker's egress-only security group."
  type        = string
  default     = ""
}

variable "worker_desired_count" {
  description = "How many worker tasks run. ONE by default, and that is a spend cap rather than a correctness limit: `agentorg/queue/_sql.py`'s claim is one transaction plus a UNIQUE index, so two workers cannot hold one job. The claim is at-least-once, not exactly-once, so more workers means more chances to exercise `reclaimed_from` -- which is the trace of a stage that may have run twice."
  type        = number
  default     = 1
}

variable "worker_cpu" {
  description = "Fargate CPU units. 512 = 0.5 vCPU, the smallest that pairs with 1024 MiB. Measured from the AWS Pricing API 2026-08-28: USE1-Fargate-ARM-vCPU-Hours $0.03238/hour, so 0.5 vCPU is $0.01619/hour."
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Fargate memory in MiB. 1024 is the minimum Fargate accepts with 512 CPU units. Measured: USE1-Fargate-ARM-GB-Hours $0.00356/hour, so 1 GiB is $0.00356/hour."
  type        = number
  default     = 1024
}

variable "log_retention_days" {
  description = "CloudWatch retention for the worker's log group. 14 days, matching the ingress module's Lambda group -- one number for the project, not a per-module opinion."
  type        = number
  default     = 14
}

variable "tags" {
  description = "Tags applied to every resource this module creates."
  type        = map(string)
  default     = {}
}
