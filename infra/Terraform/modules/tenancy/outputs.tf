output "table_name" {
  description = "The tenancy table's name, for the application's config default"
  value       = aws_dynamodb_table.tenancy.name
}

output "table_arn" {
  description = "The tenancy table's ARN"
  value       = aws_dynamodb_table.tenancy.arn
}

output "tenant_scoped_role_arn" {
  description = <<-EOT
    The role the web app assumes per request, tagged with the verified tenant.

    This is the ARN `web/lib/` needs. Read it from here rather than assembling it
    from account id and name: an assembled ARN is a second declaration that keeps
    agreeing while the real one moves.
  EOT
  value       = aws_iam_role.tenant_scoped.arn
}

output "tenant_scoped_is_assumable" {
  description = <<-EOT
    FALSE when `tenant_assumer_arns` is empty, which is the default.

    The role exists in that state with a trust policy nothing can satisfy, so
    every AssumeRole fails and the web path has no database access at all. That
    is the correct fail-closed default and it is also indistinguishable, from
    inside the application, from a misconfigured tag -- both surface as
    AccessDenied. So it is reported as data here, the way `modules/ingress`
    reports `dispatch_target_enabled`: a rule with no target fires into nothing
    while looking perfectly healthy in the console.
  EOT
  value       = length(var.tenant_assumer_arns) > 0
}

output "service_roles_attached" {
  description = <<-EOT
    How many principals hold CROSS-TENANT access.

    Surfaced as a number because it is the one figure in this module worth
    reviewing on every apply: each one is a principal `dynamodb:LeadingKeys` does
    not constrain. Zero is a working configuration -- the pipeline simply has no
    access yet -- and is the right value until the application is ported.
  EOT
  value       = length(var.service_role_arns)
}
