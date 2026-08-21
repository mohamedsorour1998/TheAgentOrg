output "table_name" {
  description = "Set STATE_TABLE to this, alongside STATE_BACKEND=dynamodb"
  value       = aws_dynamodb_table.runs.name
}

output "table_arn" {
  description = "The run-state table's ARN"
  value       = aws_dynamodb_table.runs.arn
}

output "access_policy_arn" {
  description = "The four-action policy attached to the runtime and CI roles"
  value       = aws_iam_policy.table_access.arn
}
