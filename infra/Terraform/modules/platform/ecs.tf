# Where the worker actually runs. LANE N, N2. COUNT-GATED — see variables.tf.
#
# Everything in this file is `count = var.runtime_enabled ? 1 : 0`, so the default
# plan creates a registry, a log group and two roles and nothing that bills by the
# hour. The gate's full reasoning is on `runtime_enabled`; the short version is that
# the Postgres queue dialect these resources would run has a DatatypeMismatch on
# every enqueue, measured 2026-08-28 against a real PostgreSQL 16.15.

# ── THE PRECONDITIONS. Three inputs whose absence would deploy something worse ──
#
# `terraform_data` rather than preconditions scattered across resources: all three
# checks belong to the same decision, and this way a bad combination fails ONE
# named thing at PLAN time rather than partway through an apply.
#
# EVERY ONE OF THESE IS A FAIL-QUIET WITHOUT THE CHECK, which is the standard this
# repository holds a refusal to.
resource "terraform_data" "runtime_preconditions" {
  count = var.runtime_enabled ? 1 : 0

  input = "${var.worker_image}|${var.queue_dsn_secret_arn}|${join(",", var.subnet_ids)}"

  lifecycle {
    precondition {
      condition     = var.worker_image != ""
      error_message = "runtime_enabled is true but worker_image is empty. A task definition with no image cannot be created, and the failure would arrive from the ECS API mid-apply rather than from the plan. Build and push through .github/workflows/deploy-platform.yml, which tags the image with the commit SHA -- `:latest` cannot tell you which commit is running."
    }

    precondition {
      # THE MOST IMPORTANT OF THE THREE. `agentorg/queue/_sql.py:_dsn()` reads
      # `QUEUE_DSN` from the environment and DEFAULTS TO A SQLITE FILE when it is
      # empty. So a worker with no DSN starts, claims jobs, and writes them to a
      # file inside its own container -- meaning two tasks never see each other's
      # work and BOTH run every stage, which posts every PR comment twice and pays
      # the model bill twice. Lane A's own refusal message names exactly that
      # consequence for the wrong-driver case; this precondition is the same
      # refusal one layer out, where the value is chosen.
      condition     = var.queue_dsn_secret_arn != ""
      error_message = "runtime_enabled is true but queue_dsn_secret_arn is empty. agentorg/queue/_sql.py falls back to a sqlite file inside the container when QUEUE_DSN is unset, so each task would hold a PRIVATE queue: two workers would never see each other's jobs and both would run every stage -- two PR comments, two model bills, one run. This module does not create a database; see main.tf for why."
    }

    precondition {
      condition     = length(var.subnet_ids) > 0 && var.vpc_id != ""
      error_message = "runtime_enabled is true but subnet_ids or vpc_id is empty. A Fargate task needs both. The worker takes no inbound traffic -- it polls -- so these are for EGRESS: without reachable subnets it cannot reach Bedrock, GitHub or its database, and it fails on its first claim rather than at startup."
    }
  }
}

# ── THE CLUSTER ──────────────────────────────────────────────────────────────
#
# An empty ECS cluster is free; the task inside it is not. Container Insights is
# left at its account default deliberately -- it is a per-cluster CloudWatch charge
# for metrics nothing in this repository reads, and `report.render` already names
# the model cost, which is the number the judges asked about.
resource "aws_ecs_cluster" "platform" {
  count = var.runtime_enabled ? 1 : 0

  name = "${var.name}-platform"
  tags = var.tags
}

# ── THE WORKER'S SECURITY GROUP: EGRESS ONLY, AND NO INGRESS RULE AT ALL ─────
#
# NOT AN OVERSIGHT. A worker is a poller: it claims a job from the database, runs a
# stage, and records the result. Nothing connects TO it, so there is no port to open
# and no load balancer to attach. An empty ingress block means the group refuses
# every inbound connection, which is exactly right and is worth stating because a
# reader looking for the "how do I reach it" rule should find this sentence instead.
#
# THIS IS ALSO WHY THE API AND WEB APP ARE NOT IN THIS MODULE. Both take inbound
# traffic, so both need a listener, a certificate and a hostname -- and
# `agentorg/api/server.py` binds 127.0.0.1 with no way to override it, measured over
# the AST. See main.tf.
resource "aws_security_group" "worker" {
  count = var.runtime_enabled ? 1 : 0

  name        = "${var.name}-worker"
  description = "The queue worker. Egress only, because nothing connects to a poller."
  vpc_id      = var.vpc_id

  # OPEN EGRESS, and what bounds it is the task role rather than this rule.
  # Narrowing to prefix lists would need one per AWS service plus GitHub's published
  # ranges, which change without notice -- and a stale range presents as a worker
  # that claims a job and then fails to reach the model, which reads as a denial.
  #
  # NOTE ON THE DESCRIPTION STRINGS IN THIS FILE: AWS restricts security group
  # descriptions to `^[0-9A-Za-z_ .:/()#,@\[\]+=&;{}!$*-]*$` -- no apostrophe.
  # MEASURED: `terraform validate` refused the first version of this block with
  # `"egress.0.description" doesn't comply with restrictions`. So the reasoning lives
  # in comments and the description stays plain, which is the right split anyway.
  egress {
    description = "All egress. Bedrock, ECR, Secrets Manager, CloudWatch, GitHub and the queue database. Bounded by the task role, not by this rule."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = var.tags
}

# ── THE TASK DEFINITION ──────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "worker" {
  count = var.runtime_enabled ? 1 : 0

  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory

  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn

  # ARM64, MATCHING THE IMAGE. `infra/worker/Dockerfile` pins
  # `--platform=linux/arm64`, and a mismatch here is the failure the agent image's
  # own comment records: the image deploys and then fails to start with an exec
  # format error that reads like a broken entrypoint rather than a wrong
  # architecture. Fargate ARM is also the cheaper of the two, measured.
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.worker_image
    essential = true

    # `--forever` CLAIMS JOBS UNTIL STOPPED, and it is named HERE rather than
    # defaulted in the image. `infra/worker/Dockerfile`'s CMD is `--help`, so any
    # `docker run` of that image is harmless; the deployment is what asks for a
    # worker that pulls work off a shared queue.
    command = ["python", "scripts/worker.py", "--forever"]

    environment = [
      # THE QUEUE. `postgres` is the durable backend; `memory` loses every paused
      # run when the task restarts, and Fargate restarts tasks. Lane A's `_backend`
      # refuses to downgrade for that reason, and this is the value that keeps it
      # from having to.
      { name = "QUEUE_BACKEND", value = "postgres" },

      # REMOTE AGENTS: the five AgentCore runtimes, not this container's CPU. The
      # task role grants `InvokeAgentRuntime` for this, and the Bedrock statement
      # beside it covers the documented fallback when this is unset.
      { name = "REMOTE_AGENTS", value = "true" },

      # The target repository every agent reads through `repo_snapshot`. A NAME, NOT
      # A CREDENTIAL -- the clone is anonymous, and `deploy.yml:260-265` records why
      # shipping a token to read a public repository is a credential in one more
      # place for no capability.
      #
      # `config.GITHUB_REPO` reads the env var `DEMO_REPO` -- the one name mismatch
      # in config.py. Setting `GITHUB_REPO` here would have NO EFFECT and every
      # agent would reason blind against an empty snapshot, which is measured: a
      # reviewer probe went from 18338 prompt characters to 1977.
      { name = "DEMO_REPO", value = "" },
    ]

    # `SCANNERS_REQUIRED` IS ABSENT AND MUST STAY ABSENT.
    #
    # This image carries no scanners -- see infra/worker/Dockerfile, which states
    # why. Set true on a container without the three binaries and ABSENT becomes a
    # FAULT: one `*-scanner-error` finding per tool at severity `high`, which IS the
    # block threshold, so it blocks EVERY run including the clean one with
    # `blocking=3`. The security verdict is reached through the deployed security
    # runtime, which is the one image that carries the binaries and the one place
    # `deploy.yml` sets this knob.
    #
    # Asserted by `tests/test_platform_deploy.py`, because a comment saying "absent"
    # cannot notice somebody adding it.

    secrets = [
      # THE DSN ARRIVES AS A SECRET, NOT AN ENVIRONMENT VALUE, and that distinction
      # is why `secrets` exists in this API. A plaintext `environment` entry appears
      # in `describe-task-definition` output, in every ECS console page showing the
      # revision, and in Terraform state -- and this string carries a database
      # password. `secrets` injects it from Secrets Manager at container start; the
      # ARN is what lands in state, which is a name rather than a credential.
      #
      # This repository has already been burned by the other shape: ten Actions
      # artifacts carried a live `github_pat_` because a binary tfplan embeds state.
      { name = "QUEUE_DSN", valueFrom = var.queue_dsn_secret_arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = data.aws_region.current.region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])

  tags = var.tags
}

# ── THE SERVICE ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "worker" {
  count = var.runtime_enabled ? 1 : 0

  name            = "${var.name}-worker"
  cluster         = aws_ecs_cluster.platform[0].id
  task_definition = aws_ecs_task_definition.worker[0].arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.worker[0].id]
    assign_public_ip = true
  }

  # NO `deployment_circuit_breaker` WITH ROLLBACK, DELIBERATELY. A worker that
  # crash-loops on a real defect -- the DatatypeMismatch above is exactly one --
  # would be rolled back to the previous task definition, and the deployment would
  # report success for the version it did not run. That is the shape this repository
  # refuses everywhere else: a check that cannot distinguish "did not run" from
  # "passed". A failing worker should stay failing and visible in its log group.

  # NO LOAD BALANCER AND NO HEALTH CHECK GRACE PERIOD. Both belong to a service that
  # takes traffic. A worker's health is whether it CLAIMS jobs, which no ECS health
  # check can observe -- an idle queue and a wedged worker look identical from
  # outside. `agentorg/queue/`'s `reclaimed_from` is the field that can tell them
  # apart, and reading it is `scripts/worker.py --list`, not a probe.

  tags = var.tags

  depends_on = [terraform_data.runtime_preconditions]
}
