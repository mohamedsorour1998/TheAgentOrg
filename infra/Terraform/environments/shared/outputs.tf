output "ecr_repository_urls" {
  description = "ECR repo URL per agent (push images here)"
  value       = module.agentcore.ecr_repository_urls
}

output "agentcore_runtime_role_arn" {
  description = "IAM role ARN the AgentCore runtimes assume (pass to `agentcore configure -er`)"
  value       = module.agentcore.runtime_role_arn
}

output "github_actions_role_arns" {
  description = "GitHub OIDC role ARN per role name (used by CI)"
  value       = { "github-actions-role" = data.aws_iam_role.github_actions.arn }
}
