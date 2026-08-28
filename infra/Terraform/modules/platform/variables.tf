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
# This gate exists because the resources behind it SPEND CONTINUOUSLY and because
# one input to them is a correctness decision nobody has made yet.
#
# THE ORIGINAL REASON IS GONE, AND RECORDING THAT MATTERS MORE THAN THE GATE.
# This variable was first written because the Postgres queue dialect had never been
# executed and, on its first execution, refused its own INSERT:
#
#   psycopg.errors.DatatypeMismatch: column "poisoned" is of type integer but
#   expression is of type boolean          -- agentorg/queue/_sql.py:369
#
# FIXED on `main` at 471fc31 / 69ab1d3 (`_SCHEMA` is now a per-dialect template).
# RE-MEASURED INDEPENDENTLY 2026-08-28 against PostgreSQL 16.15, after rebasing --
# enqueue, claim, a refused second claim, pause at a gate, resume, complete, with
# `poisoned` surviving as a real `bool`:
#
#   dialect = postgres
#   enqueue      -> plan ready poisoned = True
#   claim        -> plan claimed worker-a poisoned = True
#   second claim -> None (two workers cannot hold one)
#   pause        -> paused gate1
#   resume       -> plan ready decided_by = a-real-person
#   complete     -> done
#
# So THE QUEUE PATH THESE RESOURCES WOULD RUN NOW WORKS. Two reasons to keep the
# gate off by default remain, and neither is that.
#
# REASON 1: THE DSN'S DATABASE ROLE IS THE ENTIRE TENANT-ISOLATION GUARANTEE, and
# nothing in this module can check it. MEASURED 2026-08-28 on a real Postgres,
# two roles against one table with one RLS policy:
#
#   as the TABLE OWNER, no tenant bound       2 of 2 rows visible
#   as a plain application role, unbound      0 rows
#   as a plain application role, tenant=t1    1 row
#
# Postgres skips row-level security for a superuser, for any role holding
# BYPASSRLS, and for the TABLE OWNER. `FORCE ROW LEVEL SECURITY` fixes only the
# third. So a DSN naming the owning role turns every policy into decoration while
# `pg_policies` still lists each one -- a cross-tenant read returning rows, with
# the schema looking correct. That is a WORSE outcome than no deployment, and the
# fix is an operator action (provision a non-owning role) that a Terraform variable
# cannot verify. `scripts/preflight.py` check 6 is what checks it, from the DSN.
#
# REASON 2: THE RESOURCES BILL BY THE HOUR. Every other resource in this repository
# is per-invocation -- Lambda at reserved concurrency 2, DynamoDB PAY_PER_REQUEST,
# five AgentCore runtimes that cost nothing idle. A Fargate task and the database it
# needs are the project's first standing charges, and `worker_hourly_usd_estimate`
# states the figure in the plan output rather than leaving it to a bill.
#
# WHY A COUNT GATE AND NOT SIMPLY OMITTING THE RESOURCES. Infrastructure that does
# not exist in code cannot be reviewed, planned, or costed, and the next person
# writes it from scratch under time pressure. `terraform plan` with this true is the
# artifact that says exactly what an apply would create -- which is what the task
# asks this lane to be able to state.
variable "runtime_enabled" {
  description = "Create the ECS cluster, the worker task definition and its service. FALSE by default for two measured reasons: these resources are the project's first hourly charges, and the DSN's database role decides whether RLS binds at all -- as the table OWNER a policy admits 2 of 2 rows with no tenant bound, as a plain role 0. The queue's Postgres dialect itself now works (verified 2026-08-28 against PostgreSQL 16.15); that is no longer a reason to leave this off."
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
