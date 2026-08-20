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

FROM --platform=linux/arm64 python:3.12-slim

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
