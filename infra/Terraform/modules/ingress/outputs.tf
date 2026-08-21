output "webhook_url" {
  description = <<-EOT
    The Function URL to paste into the GitHub App's webhook field.

    PUBLIC AND UNAUTHENTICATED AT THE AWS LAYER (`authorization_type = "NONE"`).
    Anyone who has this URL can invoke the function; only a caller holding the
    webhook secret can get it to publish anything. Not marked sensitive on
    purpose -- treating it as a secret would imply secrecy is what protects it,
    and the human in step 6 has to read it out of the plan output anyway.
  EOT
  value       = aws_lambda_function_url.ingress.function_url
}

output "webhook_secret_arn" {
  description = "Secrets Manager ARN whose value the human must write in step 6. Terraform creates the container, never the value."
  value       = aws_secretsmanager_secret.webhook.arn
}

output "webhook_secret_name" {
  description = "Secret NAME, for the `aws secretsmanager put-secret-value --secret-id` call in step 6"
  value       = aws_secretsmanager_secret.webhook.name
}

output "event_bus_name" {
  description = "EventBridge bus the handler publishes to"
  value       = aws_cloudwatch_event_bus.github.name
}

output "event_bus_arn" {
  description = "EventBridge bus ARN -- the only resource this function may PutEvents to"
  value       = aws_cloudwatch_event_bus.github.arn
}

output "issue_opened_rule_arn" {
  description = <<-EOT
    The rule matching an opened issue.

    IT HAS NO TARGET YET, and a rule with no target fires into nothing while
    looking perfectly healthy in the console. Attaching the target means an
    EventBridge API destination plus a connection holding a GitHub token, aimed
    at run-pipeline.yml's `workflow_dispatch` -- and that workflow is Task 3's
    file. Stated here rather than left to be discovered when the demo does not
    start.
  EOT
  value       = aws_cloudwatch_event_rule.issue_opened.arn
}

output "function_name" {
  description = "Lambda function name, for `aws logs tail /aws/lambda/<name>` while debugging a delivery"
  value       = aws_lambda_function.ingress.function_name
}

output "log_group_name" {
  description = "CloudWatch log group carrying the handler's accept/reject lines"
  value       = aws_cloudwatch_log_group.ingress.name
}
