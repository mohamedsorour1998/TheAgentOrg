variable "aws_region" {
  description = "AWS region for all Agent Org resources"
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for all resources"
  type        = string
  default     = "theagentorg"
}

variable "image_retention_count" {
  description = "How many ECR images to keep per repo"
  type        = number
  default     = 10
}
