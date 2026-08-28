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
# here would be the obvious answer and is refused today for three reasons -- and the
# first one is not the one this file originally gave.
#
#   1. THE DSN'S DATABASE ROLE IS THE WHOLE TENANT-ISOLATION GUARANTEE, and creating
#      the instance is the easy half of that. MEASURED 2026-08-28 on a real
#      PostgreSQL 16.15, two roles against one table carrying one RLS policy:
#
#        as the TABLE OWNER, no tenant bound      2 of 2 rows visible
#        as a plain application role, unbound     0 rows
#        as a plain application role, tenant=t1   1 row
#
#      Postgres skips row-level security for a superuser, for any role with
#      BYPASSRLS, and for the TABLE OWNER; `FORCE ROW LEVEL SECURITY` covers only
#      the third. So the single choice of which role the DSN names decides whether
#      six policies enforce anything or are decoration -- and `pg_policies` lists
#      every one either way.
#
#      An `aws_db_instance` here would create a master user that OWNS everything it
#      migrates, which is exactly the wrong role for the worker to connect as. Doing
#      this properly means: instance, master user, a migration run as the owner, a
#      separate non-owning application role, GRANTs, and a DSN naming the second --
#      a provisioning sequence, not a resource. Half-building it would ship the
#      failing configuration with the reassuring shape.
#
#   2. IT IS THE FIRST STANDING CHARGE IN THE PROJECT. Read from the AWS Pricing API
#      on 2026-08-28, not recalled:
#
#        db.t4g.micro PostgreSQL Single-AZ   $0.0160/hour   = ~$11.68/month
#        gp3 storage                         $0.115/GB-month
#
#      Every other resource here is per-invocation: Lambda at reserved concurrency 2,
#      DynamoDB PAY_PER_REQUEST, five AgentCore runtimes that cost nothing idle.
#
#   3. WHO OWNS THE SCHEMA IS AN OPEN QUESTION IN THIS PHASE. Three consumers want
#      one Postgres -- the queue (`_sql.py`'s `queue_jobs`), Lane B's tenancy schema,
#      and Auth.js's session tables (`web/lib/auth.ts` refuses to start without
#      `DATABASE_URL`). `infra/selfhost/docker-compose.yml` points all three at one
#      database on purpose. And the web app's tenant lookup is currently CIRCULAR
#      under RLS: `membershipsFor` reads the RLS-scoped `membership` table to
#      discover the tenant RLS needs bound, so `/api/session` answers
#      `signed_in: true` with `tenant_id: null` and every authenticated route 401s.
#      `web/lib/tenant.ts:126` records the fix -- a narrow unscoped path for the
#      identity lookup only -- and names it as a change to THIS module's role model.
#      Choosing an instance size before that role model exists is choosing early.
#
# NOTE ON WHAT IS NO LONGER TRUE. This comment previously gave a fourth reason: that
# the queue's Postgres dialect refused its own INSERT with a `DatatypeMismatch` on
# `poisoned`. That was real, it was fixed on `main` (471fc31 / 69ab1d3), and it has
# been re-measured independently here -- enqueue, claim, a refused second claim,
# pause, resume and complete all pass with `poisoned` surviving as a real `bool`.
# Recorded rather than deleted, because a stale justification for a gate is how a
# gate outlives its reason and nobody can tell which reasons still hold.
#
# So the DSN arrives as a secret ARN. A team with an operational Postgres and a
# non-owning application role points this at it. The refusal when it is empty is the
# important half: see the precondition in ecs.tf.
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
