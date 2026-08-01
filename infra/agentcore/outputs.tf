output "ecr_repository_urls" {
  description = "ECR repo URL per agent (push images here)"
  value       = { for k, r in aws_ecr_repository.agents : k => r.repository_url }
}

output "agentcore_role_arn" {
  description = "IAM role ARN AgentCore runtimes assume"
  value       = aws_iam_role.agents.arn
}
