# The Agent Org — the PLATFORM module: where the queue worker runs. LANE N, N2.
#
# Follows modules/agentcore and modules/ingress: one registry + one role always,
# the resources that SPEND count-gated behind a variable that defaults off.
#
# ── WHAT THIS MODULE DELIBERATELY DOES NOT CREATE ────────────────────────────
#
# A DATABASE. `runtime_enabled` takes a `queue_dsn_secret_arn` instead, and that
# omission is a decision rather than an unfinished edge.
#
# The worker needs a durable Postgres for `QUEUE_BACKEND=postgres`. An RDS instance
# here would be the obvious answer and would be wrong today, for three measured
# reasons:
#
#   1. THE CODE THAT WOULD USE IT DOES NOT WORK YET. Measured 2026-08-28 against a
#      real PostgreSQL 16.15 -- the first execution of this repository's Postgres
#      dialect, ever:
#
#        psycopg.errors.DatatypeMismatch: column "poisoned" is of type integer but
#        expression is of type boolean
#
#      from `agentorg/queue/_sql.py:369`, on the FIRST `enqueue`. Creating a database
#      for code that cannot write to it is a bill with no capability attached.
#
#   2. IT IS THE MOST EXPENSIVE THING IN THE DESIGN AND IT NEVER STOPS. Read from
#      the AWS Pricing API on 2026-08-28, not recalled:
#
#        db.t4g.micro PostgreSQL Single-AZ   $0.0160/hour   = ~$11.68/month
#        gp3 storage                         $0.115/GB-month
#
#      Every other resource in this repository is per-invocation: Lambda at
#      reserved concurrency 2, DynamoDB PAY_PER_REQUEST, five AgentCore runtimes
#      that cost nothing idle. An RDS instance is the first standing charge, and it
#      accrues whether or not a run ever happens.
#
#   3. WHO OWNS THE SCHEMA IS AN OPEN QUESTION IN THIS PHASE. Three consumers want
#      one Postgres -- the queue (`_sql.py`'s `queue_jobs`), Lane B's tenancy schema,
#      and Auth.js's session tables (`web/lib/auth.ts` refuses to start without
#      `DATABASE_URL`). `infra/selfhost/docker-compose.yml` points all three at one
#      database on purpose, and Lane B's own note says NOTHING IN THE SUITE CONNECTS
#      TO POSTGRES. Choosing an instance size, a parameter group and a migration
#      owner before any of that is exercised is choosing on no evidence.
#
# So the DSN arrives as a secret ARN. A team that has an operational Postgres --
# RDS, Aurora Serverless, or a container -- points this at it without this module
# holding an opinion it cannot justify. The refusal when it is empty is the
# important half: see the precondition below.
#
# ── AND IT DOES NOT RUN THE API OR THE WEB APP ───────────────────────────────
#
# Both are named in N1 and both are REFUSED here, with the measurement:
#
#   `agentorg/api/server.py` BINDS LOOPBACK AND CANNOT BE TOLD OTHERWISE. Measured
#   over the AST: `serve(host="127.0.0.1", port=8100)`, `main()` calls `serve()`
#   with NO arguments, and the module reads no environment variable at all (`grep`
#   for `environ`/`getenv` in that file returns nothing). A container binding
#   127.0.0.1 inside its own network namespace is reachable by nothing -- so a task
#   definition for it would deploy, report RUNNING, pass a "the service is up"
#   check, and answer no request. That is this repository's signature failure shape,
#   and the fix is one keyword argument in a file this lane does not own
#   (`agentorg/api/**` is Lane K's).
#
#   Its key store, idempotency store and config store are also IN-PROCESS -- Lane
#   K's own note names all three -- so a second task would share no keys with the
#   first and `worker_desired_count`'s reasoning does not transfer.
#
#   THE WEB APP NEEDS A POSTGRES THAT HAS NEVER EXISTED. `next build` refuses
#   without `DATABASE_URL`, `AUTH_SECRET`, `AUTH_GITHUB_ID` and
#   `AUTH_GITHUB_SECRET` -- Lane I's fail-closed design firing at BUILD time -- and
#   Lane I's own note says "NO POSTGRES HAS EVER BEEN CONNECTED [...] A sign-in flow
#   that has never completed against a real Postgres is not a working sign-in flow."
#   Deploying it would also need a GitHub OAuth app whose callback URL names a
#   hostname that does not exist yet, and an HTTPS listener, and a certificate.
#
# A workflow that pushed either image would report a successful deploy of something
# that cannot serve traffic. CLAUDE.md's central lesson is that "deployed" and
# "verified" were separate facts here for most of a week; this module refuses to
# repeat it. `deploy-platform.yml` builds and pushes the WORKER image only, and says
# so in its own header.

data "aws_region" "current" {}

# ── THE REGISTRY. Always created; an empty repository costs nothing. ──────────
#
# Not `for_each` over a list like the agentcore module: there is one worker image,
# and a single resource makes the diff readable.
resource "aws_ecr_repository" "worker" {
  name                 = "${var.name}-worker"
  image_tag_mutability = "MUTABLE"

  # Scan on push, matching the agentcore module's five. A base-image CVE in the
  # container that holds a GitHub token and a database DSN is worth being told about.
  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire images, keep last ${var.image_retention_count}"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.image_retention_count
      }
      action = { type = "expire" }
    }]
  })
}

# ── THE LOG GROUP. Terraform-managed, so the task role needs no CreateLogGroup. ──
#
# Exactly modules/ingress's reasoning: the group is declared here, so the execution
# role below grants `CreateLogStream` and `PutLogEvents` and NOT `CreateLogGroup`,
# and no policy in this module carries a wildcard log resource.
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/ecs/${var.name}-worker"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
