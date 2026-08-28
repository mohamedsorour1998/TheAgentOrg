variable "image_retention_count" {
  type        = number
  description = "How many ECR images to keep per agent repo"
  default     = 5
}

################################################################################
# The platform module's inputs. LANE N.
#
# All five default to off/empty. The module's own variables.tf carries the
# reasoning for each; these exist so the root can be driven from TF_VAR_ in the
# workflow rather than from a gitignored tfvars file.
################################################################################

variable "platform_runtime_enabled" {
  description = "Create the ECS cluster, worker task definition and service. False by default: they spend continuously, and agentorg/queue/_sql.py's Postgres dialect has a measured DatatypeMismatch on every enqueue."
  type        = bool
  default     = false
}

variable "platform_worker_image" {
  description = "The worker's ECR image URI, tagged with a commit SHA. Refused when platform_runtime_enabled is true and this is empty."
  type        = string
  default     = ""
}

variable "platform_queue_dsn_secret_arn" {
  description = "Secrets Manager ARN holding QUEUE_DSN. Refused when platform_runtime_enabled is true and this is empty -- an unset DSN makes each worker write to a private sqlite file inside its own container, so two tasks both run every stage."
  type        = string
  default     = ""
}

variable "platform_subnet_ids" {
  description = "Subnets the worker task runs in. Egress only; the worker takes no inbound traffic."
  type        = list(string)
  default     = []
}

variable "platform_vpc_id" {
  description = "The VPC holding platform_subnet_ids. Used only for the worker's egress-only security group."
  type        = string
  default     = ""
}
