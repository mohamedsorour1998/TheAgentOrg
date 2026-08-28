# What the platform module tells its caller. LANE N, N2.

output "worker_repository_url" {
  description = "The ECR repository the worker image is pushed to. deploy-platform.yml reads this rather than assembling the URL, for the reason agent_client states about ARNs: a value read from the API cannot disagree with the resource, and a value assembled from parts can."
  value       = aws_ecr_repository.worker.repository_url
}

output "worker_repository_name" {
  description = "The repository NAME, which is what `aws ecr describe-images --repository-name` takes."
  value       = aws_ecr_repository.worker.name
}

output "worker_task_role_arn" {
  description = "The role the worker's own code assumes. This is the principal scripts/preflight.py check 5 simulates: a green apply proves the Bedrock policy was WRITTEN, and only simulate-principal-policy proves it PERMITS the call. Those were different facts here for a week."
  value       = aws_iam_role.task.arn
}

output "worker_execution_role_arn" {
  description = "The role the ECS agent uses to pull the image, write the log stream and read the DSN secret. Distinct from the task role on purpose -- see iam.tf."
  value       = aws_iam_role.execution.arn
}

output "worker_log_group" {
  description = "The worker's CloudWatch group. Worth knowing because it is the ONLY place a wedged worker is visible: an idle queue and a stuck worker look identical from outside, and no ECS health check can tell them apart."
  value       = aws_cloudwatch_log_group.worker.name
}

# ── THE RUNTIME GATE, REPORTED THE WAY modules/ingress REPORTS ITS TARGET ─────
#
# `dispatch_target_enabled` exists in that module because "a rule with no target
# fires into nothing while looking perfectly healthy in the console" -- measured.
# This is the same hazard with a different subject: a platform module that created a
# registry and no service is INDISTINGUISHABLE, in a green apply's output, from one
# that deployed a running worker. The apply succeeds either way.
#
# So the state of the gate is an output rather than something a reader infers from
# the absence of an ARN. `scripts/preflight.py` check 6 reads the live account and
# says the same thing from the other direction.
output "worker_runtime_enabled" {
  description = "Whether the ECS cluster, task definition and service were created. FALSE by default: those resources are the project's first hourly charges, and the DSN's database role decides whether RLS binds (as the table OWNER a policy admits every row with no tenant bound). A green apply with this false has created a registry and two roles, nothing more -- and cannot be told from a real deployment by the apply's exit code."
  value       = var.runtime_enabled
}

output "worker_service_name" {
  description = "The ECS service, or \"\" when runtime_enabled is false. EMPTY IS A FACT, not a missing value: it means no worker is running, which is why the string is empty rather than the output being conditional -- a caller reading `\"\"` learns something, and a caller getting an error about an absent output learns to stop asking."
  value       = var.runtime_enabled ? aws_ecs_service.worker[0].name : ""
}

output "worker_cluster_name" {
  description = "The ECS cluster, or \"\" when runtime_enabled is false. Same reasoning as worker_service_name."
  value       = var.runtime_enabled ? aws_ecs_cluster.platform[0].name : ""
}

# ── THE COST, IN THE OUTPUT, BECAUSE NOBODY OPENS A BILL BEFORE AN APPLY ─────
#
# Every figure is from the AWS Pricing API on 2026-08-28, and the commands are in
# variables.tf beside the values they set. Stated as a computed output rather than a
# comment so it appears in `terraform output` and in the apply job's log, where the
# person who turned the gate on will actually read it.
#
# THE DATABASE IS NOT IN THIS NUMBER and that is the largest omission: this module
# creates none (see main.tf), and a db.t4g.micro Single-AZ PostgreSQL is $0.0160/hour
# = ~$11.68/month, which is more than the worker. Stating a total that excluded it
# without saying so would be the flattering half of a measurement, which
# `cost/report.py` refuses to do for model spend.
output "worker_hourly_usd_estimate" {
  description = "Fargate ARM cost per hour for the running worker(s), EXCLUDING the database this module does not create. Prices read from the AWS Pricing API 2026-08-28: USE1-Fargate-ARM-vCPU-Hours $0.03238, USE1-Fargate-ARM-GB-Hours $0.00356."
  value = var.runtime_enabled ? format(
    "%.5f USD/hour for %d task(s) at %d CPU units + %d MiB, EXCLUDING the database (a db.t4g.micro Single-AZ PostgreSQL is 0.01600 USD/hour on top)",
    var.worker_desired_count * ((var.worker_cpu / 1024) * 0.03238 + (var.worker_memory / 1024) * 0.00356),
    var.worker_desired_count,
    var.worker_cpu,
    var.worker_memory,
  ) : "0.00000 USD/hour -- runtime_enabled is false, so no cluster, task definition or service exists. An empty ECR repository and a CloudWatch log group with no streams cost nothing."
}
