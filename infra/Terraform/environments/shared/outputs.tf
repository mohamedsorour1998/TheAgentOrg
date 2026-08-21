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

# ── ingress (GitHub App webhook -> Lambda -> EventBridge) ─────────────────────

output "github_webhook_url" {
  description = "Paste into the GitHub App's webhook URL field. PUBLIC and unauthenticated at the AWS layer -- the HMAC in the handler is the only access control."
  value       = module.ingress.webhook_url
}

output "github_webhook_secret_name" {
  description = "Write the App's webhook secret here by hand: `aws secretsmanager put-secret-value --secret-id <this> --secret-string <secret>`. Terraform never writes the value."
  value       = module.ingress.webhook_secret_name
}

output "github_ingress_bus_name" {
  description = "EventBridge bus carrying verified GitHub deliveries"
  value       = module.ingress.event_bus_name
}

output "github_ingress_log_group" {
  description = "`aws logs tail <this> --follow` while debugging a delivery"
  value       = module.ingress.log_group_name
}
