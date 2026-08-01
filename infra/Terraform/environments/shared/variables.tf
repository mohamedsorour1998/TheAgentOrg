variable "image_retention_count" {
  type        = number
  description = "How many ECR images to keep per agent repo"
  default     = 5
}
