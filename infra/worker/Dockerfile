# The queue worker's image. LANE N, task N1.
#
# WHY THIS IS A SECOND IMAGE AND NOT A SECOND TAG OF THE AGENT ONE. Two measured
# reasons, and neither is a preference:
#
#   1. `scripts/` IS EXCLUDED FROM THE AGENT IMAGE'S BUILD CONTEXT. The repository
#      root `.dockerignore` line 51 is `scripts/`, so `scripts/worker.py` and
#      `scripts/run_stage.py` are stripped from the context before it reaches the
#      builder. MEASURED 2026-08-28 by evaluating the ignore patterns the way the
#      docker client does:
#
#          scripts/worker.py                excluded_by=['scripts/']
#          scripts/run_stage.py             excluded_by=['scripts/']
#          agentorg/api/server.py           excluded_by=[]
#          agentorg/queue/_sql.py           excluded_by=[]
#
#      So `infra/selfhost/docker-compose.yml:170` -- `command: ["python",
#      "scripts/worker.py", "--forever"]` against `agentorg/agents/Dockerfile` --
#      starts a container whose entrypoint file is not in the image. That is this
#      repository's signature failure shape in a new place: the compose file
#      parses, `docker compose config` passes, and the worker dies on a missing
#      file. It is the SAME defect as `COPY fixtures ./fixtures`, which was
#      measured on the first runtime that served traffic (`/ping` answered 200 and
#      every `/invocations` died with FileNotFoundError).
#
#   2. psycopg MUST NOT SHIP TO THE FIVE ARM64 AGENT IMAGES.
#      `agentorg/queue/_sql.py:225-245` records the ruling and it is Lane A's:
#      `importlib.import_module("psycopg")` at CALL time, deliberately not a static
#      import, because `test_requirements_covers_every_third_party_import_in_the_package`
#      AST-walks `agentorg/` and would make any import there a pinned dependency of
#      all five agent containers -- a database driver no agent opens, and one more
#      thing that can fail at runtime on arm64. That note ends: "When a worker image
#      exists, psycopg belongs in ITS requirements." This is that image.
#
# THE PAIRED IGNORE FILE, AND WHY THE BUILD ALSO ASSERTS THE OUTCOME.
# `Dockerfile.dockerignore` beside this file overrides the context-root
# `.dockerignore` -- docker's documented rule is "Place your ignore-file in the
# same directory as the Dockerfile, and prefix the ignore-file with the name of the
# Dockerfile", and "A Dockerfile-specific ignore-file takes precedence over the
# .dockerignore file at the root of the build context".
#
# THAT BEHAVIOUR IS UNVERIFIED ON THE MACHINE THIS WAS WRITTEN ON: there is no
# Docker daemon here (`docker ps` -> "dial unix /var/run/docker.sock: connect: no
# such file or directory"), so no build has run. A documented rule nobody executed
# is exactly the kind of claim this repository does not accept, so the outcome is
# ALSO asserted at build time by the `test -f` step below. If the pairing does not
# take effect, the build FAILS naming the file rather than pushing an image whose
# whole purpose is absent -- turning a runtime failure into a build failure, which
# is what the agent image's own import smoke test does for the same reason.
#
# ARM64, matching the agent image. Not because AgentCore requires it here -- this
# image does not run on AgentCore -- but because Fargate ARM is the cheaper
# runtime (measured from the AWS Pricing API: USE1-Fargate-ARM-vCPU-Hours
# $0.03238/hour, USE1-Fargate-ARM-GB-Hours $0.00356/hour) and because one
# `docker/setup-qemu-action` platform serves both images in one workflow.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

# ECR Public, not Docker Hub, for the reason `agentorg/agents/Dockerfile:21-32`
# measured across three runs: CodeBuild pulls anonymously and Docker Hub answers
# `429 Too Many Requests` late in the build, for a reason unrelated to this repo.

# NO SCANNERS IN THIS IMAGE, and that is deliberate rather than an omission.
#
# The worker does not scan. It claims a job and runs `python scripts/run_stage.py
# <stage>` as a SUBPROCESS (`agentorg/queue/runner.py:49`), and that stage reaches
# the security verdict through `call_agent("security", state)` -- which under
# `REMOTE_AGENTS=true` is the deployed security runtime, the one image that carries
# gitleaks, trivy and semgrep.
#
# SO `SCANNERS_REQUIRED` MUST NOT BE SET ON THIS IMAGE. Set true on a container
# without the three binaries and the ABSENT path becomes a FAULT: one
# `*-scanner-error` finding per tool at severity `high`, which is the block
# threshold, so it blocks EVERY run including the clean one with `blocking=3`.
# `deploy.yml:267-269` guards that knob to the security agent for exactly this
# reason, and the same guard is asserted for this image in
# `tests/test_platform_deploy.py`.
#
# git IS installed: `github_ops.open_pr` shells out to real `git` on the offline
# path, and `scripts/run_stage.py` runs in this container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Requirements before the source, so a source-only change reuses this layer.
COPY infra/worker/requirements.txt ./worker-requirements.txt
RUN pip install --no-cache-dir -r worker-requirements.txt

COPY pyproject.toml README.md ./
COPY agentorg ./agentorg

# fixtures/ IS REQUIRED AT RUNTIME AND LIVES OUTSIDE THE PACKAGE, for the reason
# `agentorg/agents/Dockerfile:105-122` measured: `fixtures_loader` resolves it from
# the REPO ROOT, so `pip install .` never ships it. Every agent falls back to a
# fixture when no model answers, and `scripts/run_stage.py` runs agents in-process
# whenever `REMOTE_AGENTS` is false -- which is the demo's documented fallback, so
# this container must be able to take that path.
COPY fixtures ./fixtures

# THE TWO SCRIPTS THIS IMAGE EXISTS FOR. Excluded from the agent image's context by
# the root `.dockerignore`; re-included by `Dockerfile.dockerignore` beside this
# file. `run_stage.py` is not optional -- `queue/runner.py` invokes it as a
# subprocess per stage, deliberately, so that `llm.last_source()` and the scanner
# fan-out memo cannot be inherited between stages. Both would otherwise produce a
# FALSE MEASUREMENT rather than a crash.
COPY scripts ./scripts

RUN pip install --no-cache-dir --no-deps .

# THE BUILD-TIME ASSERTION THAT MAKES THE PAIRED IGNORE FILE CHECKABLE. Without
# this, a pairing that did not take effect produces an image that pushes, deploys
# and then exits 2 on `python: can't open file '/app/scripts/worker.py'` -- read at
# a distance as a broken command rather than as a stripped build context.
#
# `python -c "import ..."` on the worker is deliberately NOT the check here:
# `scripts/` is not a package and `worker.py` is imported by nothing, so a bare
# import proves less than the file's presence does. `--help` exercises argparse,
# which is the interface `run-pipeline.yml` and the queue both drive.
RUN test -f scripts/worker.py \
      || (echo "scripts/worker.py is NOT in this image. The paired ignore file \
infra/worker/Dockerfile.dockerignore did not override the root .dockerignore's \
'scripts/' line. Build with -f infra/worker/Dockerfile and context '.'" && exit 1) \
 && test -f scripts/run_stage.py \
      || (echo "scripts/run_stage.py is NOT in this image. queue/runner.py invokes \
it as a subprocess per stage; without it every claimed job fails" && exit 1) \
 && python scripts/worker.py --help > /dev/null \
 && python scripts/run_stage.py --help > /dev/null \
 && echo "worker image OK: both scripts present and both parse their arguments"

# THE POSTGRES DRIVER MUST BE IMPORTABLE HERE, and this asserts it rather than
# trusting the pip line. `agentorg/queue/_sql.py:_psycopg()` raises a named refusal
# when it is absent -- good behaviour, and it fires at the FIRST `claim()`, which is
# after the worker has reported itself healthy and started polling. A driver that
# failed to build a wheel for arm64 would otherwise present as a queue that accepts
# `QUEUE_BACKEND=postgres` and never claims a job.
RUN python -c "import psycopg; print('psycopg', psycopg.__version__)"

RUN useradd --create-home --uid 10001 worker && chown -R worker:worker /app
USER worker

# NO DEFAULT COMMAND THAT CLAIMS JOBS. `--forever` in a CMD means any `docker run`
# of this image starts pulling work off a shared queue, including one a person
# started to look around in. `--help` is the harmless default and the deployment
# names the real command, the way `agentorg/agents/Dockerfile` refuses to default
# `AGENT_ROLE`.
CMD ["python", "scripts/worker.py", "--help"]
