# One image, five runtimes. OWNER: Sorour.
#
# Every agent runs the same image with a different AGENT_ROLE, so there is one
# Dockerfile to keep correct rather than five that drift. The tradeoff is that
# each image carries all five agent modules -- a few kilobytes of Python -- in
# exchange for one build to verify and one place to fix a base-image CVE.
#
# ARM64 IS NOT OPTIONAL. AgentCore runs arm64, and the module comment in
# infra/Terraform/modules/agentcore/main.tf says so: "arm64 images for
# AgentCore". An amd64 image pushes and deploys and then fails to start with an
# exec format error, which reads like a broken entrypoint rather than a wrong
# architecture. The deploy workflow builds with --platform linux/arm64.
#
# WHY `pip install .` AND NOT ONLY requirements.txt. The package itself has to
# be importable, and Task 1 of this plan fixed exactly the defect that hides
# here: pyproject's [tool.setuptools] once declared `packages = ["agentorg"]`,
# which silently dropped agentorg/agents, agentorg/common and agentorg/security
# from a non-editable install. The image built green and died on import. The
# smoke test at the end of this file is what keeps that from recurring.

# ECR Public, not Docker Hub. CodeBuild pulls anonymously and Docker Hub
# rate-limits that: the first real deploy died with
#
#   ERROR: failed to solve: python:3.12-slim: failed to resolve source metadata
#   for docker.io/library/python:3.12-slim: unexpected status from HEAD request
#   to https://registry-1.docker.io/v2/library/python/manifests/3.12-slim:
#   429 Too Many Requests
#
# after CodeBuild had already provisioned, downloaded source and started the
# build -- so it fails late, and it fails for a reason that has nothing to do
# with this repository. public.ecr.aws mirrors the same official images with no
# anonymous pull limit, and AWS-to-AWS pulls do not leave the region.
#
# Verified before switching, via the registry API rather than assumed: the
# mirror's 3.12-slim manifest is an OCI image index carrying arm64 among
# ['386','amd64','arm','arm64','ppc64le','riscv64','s390x'].
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

# THE THREE SCANNERS, IN THE IMAGE. Without them this container takes the ABSENT
# path: every wrapper raises FileNotFoundError, agents/security.py catches it, and
# the verdict comes from fixtures/security_result_block.json rather than from
# compute_security_verdict. The gate would then block because a JSON file was
# read, which is exactly the claim the demo must not make. Measured before this
# layer existed: the deployed security runtime returned a fixture verdict.
#
# Versions are CI's pins (.github/workflows/ci.yml:124-126), not the authoring
# machine's, so the image matches what the suite is gated against.
#
# THE VERSION-CHECK TAIL IS THE GUARANTEE AND MUST NOT BE REMOVED. A binary that
# downloads but cannot execute -- wrong architecture, truncated tarball -- would
# otherwise ship, and a BROKEN scanner is a different fault from an absent one:
# it blocks every run, including the clean one. Failing the build here is cheap;
# discovering it on a projector is not.
#
# git is installed alongside them deliberately: github_ops.open_pr shells out to
# real `git` on the offline path (github_ops.py:64-67), and a container without it
# dies there instead, which reads as an unrelated failure.
#
# semgrep lives in /opt, not /tmp, because /tmp is not guaranteed to survive
# between build and run; a symlink into a wiped directory presents as a BROKEN
# scanner and would block every run.
ARG GITLEAKS_VERSION=8.21.2
ARG TRIVY_VERSION=0.74.0
ARG SEMGREP_VERSION=1.172.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates git \
 && rm -rf /var/lib/apt/lists/* \
 && curl -sSfL -o /tmp/gitleaks.tar.gz \
      "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_arm64.tar.gz" \
 && tar -xzf /tmp/gitleaks.tar.gz -C /tmp gitleaks \
 && mv /tmp/gitleaks /usr/local/bin/gitleaks \
 && curl -sSfL -o /tmp/trivy.tar.gz \
      "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-ARM64.tar.gz" \
 && tar -xzf /tmp/trivy.tar.gz -C /tmp trivy \
 && mv /tmp/trivy /usr/local/bin/trivy \
 && python -m venv /opt/semgrep-venv \
 && /opt/semgrep-venv/bin/pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}" \
 && ln -s /opt/semgrep-venv/bin/semgrep /usr/local/bin/semgrep \
 && rm -f /tmp/gitleaks.tar.gz /tmp/trivy.tar.gz \
 && gitleaks version && trivy --version && semgrep --version

# Fail fast on a missing directory, and keep every later path relative to here.
WORKDIR /app

# PYTHONDONTWRITEBYTECODE: the image is read-only at runtime, so .pyc files are
# dead weight in a layer.
# PYTHONUNBUFFERED: without it CloudWatch shows nothing until the buffer flushes
# or the container dies, which is precisely when the logs matter most.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Requirements before the source, so a source-only change reuses the dependency
# layer. This is the difference between a 20-second rebuild and a 3-minute one,
# five times over.
COPY agentorg/agents/requirements.txt ./agents-requirements.txt
RUN pip install --no-cache-dir -r agents-requirements.txt

# pyproject and README first: `pip install .` reads both, and copying them
# separately keeps the metadata layer stable across source edits.
COPY pyproject.toml README.md ./
COPY agentorg ./agentorg

# fixtures/ IS REQUIRED AT RUNTIME AND LIVES OUTSIDE THE PACKAGE.
# agentorg/fixtures_loader.py:21 resolves it as
# `pathlib.Path(__file__).resolve().parent.parent / "fixtures"` -- the repo root,
# not inside agentorg -- so `pip install .` never ships it, and the packaging fix
# that covers the subpackages and agentorg/security's data files does not reach
# it either.
#
# Measured on the first runtime that actually served traffic: /ping answered 200
# and /invocations failed with
#
#   FileNotFoundError: [Errno 2] No such file or directory:
#   '/app/fixtures/plan_result.json'
#
# Every agent falls back to a fixture when no model answers -- see the note in
# planner.py -- so without this directory a runtime reports healthy and cannot
# complete a single invocation. WORKDIR is /app and the loader walks up from
# /app/agentorg/, so /app/fixtures is exactly where it looks.
COPY fixtures ./fixtures

# --no-deps because requirements.txt above is the pinned source of truth. Without
# it, pip would re-resolve pyproject's floating dependencies and could quietly
# upgrade past a pin -- the image would then differ from the one that was tested.
RUN pip install --no-cache-dir --no-deps .

# THE SMOKE TEST THAT WOULD HAVE CAUGHT TASK 1's DEFECT AT BUILD TIME. Every
# subpackage, imported from a directory that is not the source tree, so a
# missing package cannot be masked by the working directory being importable.
# `cd /` matters: from /app, `import agentorg` finds ./agentorg whether or not
# the install shipped it.
RUN cd / && python -c "\
import agentorg, agentorg.agents, agentorg.common, agentorg.security; \
from agentorg.agents import server, planner, developer, reviewer, security, sre; \
from agentorg.state import RunState; \
from agentorg.security import gitleaks_tool, semgrep_tool, trivy_tool; \
import importlib.resources as r; \
assert r.files('agentorg.security').joinpath('gitleaks.toml').is_file(), 'gitleaks.toml missing'; \
assert r.files('agentorg.security').joinpath('semgrep_rules.yml').is_file(), 'semgrep_rules.yml missing'; \
print('import smoke test OK:', len(server.AGENTS), 'agents')"

# Not root. AgentCore does not require it, but a container that only needs to
# read its own code has no reason to be able to write to it.
RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent

# The AgentCore default. The server reads PORT and would honour an override.
EXPOSE 8080

# No CMD default for AGENT_ROLE on purpose: server.agent_role() raises when it is
# unset or unknown, so a misconfigured runtime fails at startup instead of
# serving the wrong agent's results with every response looking successful.
CMD ["python", "-m", "agentorg.agents.server"]
