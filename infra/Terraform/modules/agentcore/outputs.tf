output "ecr_repository_urls" {
  description = "ECR repo URL per agent (push images here)"
  value       = { for k, m in module.ecr : k => m.repository_url }
}

output "runtime_role_arn" {
  description = "IAM role ARN the AgentCore runtimes assume"
  value       = aws_iam_role.runtime.arn
}
