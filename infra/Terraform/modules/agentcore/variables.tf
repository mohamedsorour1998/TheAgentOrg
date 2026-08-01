variable "name" {
  description = "Resource name prefix (e.g. theagentorg-shared)"
  type        = string
}

variable "agents" {
  description = "The role agents that each get an ECR repo (planner, developer, ...)"
  type        = list(string)
  default     = ["planner", "developer", "reviewer", "security", "sre"]
}

variable "account_id" {
  description = "AWS account ID (pushers get read/write on the ECR repos)"
  type        = string
}

variable "image_retention_count" {
  description = "How many ECR images to keep per repo"
  type        = number
  default     = 5
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}
