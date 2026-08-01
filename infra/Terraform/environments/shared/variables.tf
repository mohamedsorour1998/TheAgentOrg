variable "github_oidc_roles" {
  type        = list(any)
  description = "List of GitHub-OIDC role definitions to pass to the iam module"
}

variable "image_retention_count" {
  type        = number
  description = "How many ECR images to keep per agent repo"
  default     = 5
}
