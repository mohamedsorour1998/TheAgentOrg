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

# ── run state (DynamoDB) ──────────────────────────────────────────────────────

output "run_state_table_name" {
  description = "Set STATE_TABLE to this, and STATE_BACKEND=dynamodb, to move run state off local disk"
  value       = module.state.table_name
}

output "run_state_table_arn" {
  description = "The run-state table's ARN"
  value       = module.state.table_arn
}

################################################################################
# The platform module. LANE N.
################################################################################

output "worker_repository_url" {
  description = "The ECR repository deploy-platform.yml pushes the worker image to."
  value       = module.platform.worker_repository_url
}

output "worker_task_role_arn" {
  description = "The role the worker's own code assumes. scripts/preflight.py check 5 simulates Bedrock against this principal -- a green apply proves the policy was written, not that it permits the call."
  value       = module.platform.worker_task_role_arn
}

output "worker_runtime_enabled" {
  description = "Whether a worker service exists. A green apply with this FALSE created a registry and two roles and nothing that runs -- and the apply's exit code cannot tell you which happened. Same hazard as dispatch_target_enabled: a rule with no target fires into nothing while looking healthy."
  value       = module.platform.worker_runtime_enabled
}

output "worker_hourly_usd_estimate" {
  description = "Fargate ARM cost per hour for the running worker(s), EXCLUDING the database this module does not create."
  value       = module.platform.worker_hourly_usd_estimate
}
