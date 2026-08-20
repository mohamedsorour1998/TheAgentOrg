# Container definition for the five AgentCore agent runtimes.
#
# ONE image, five runtimes. All five agents (planner, developer, reviewer,
# security, sre) live in the same installed package and differ only by which
# module the runtime invokes, so five near-identical images would be five times
# the pull cost for no isolation benefit. The five per-agent ECR repos recorded
# in docs/plan/week1-verification-log.md each receive this same build.
#
# HOW THIS IS ACTUALLY USED -- read before editing
# ------------------------------------------------
# `agentcore launch` GENERATES its own Dockerfile and performs the ARM64 build
# and ECR push itself (docs/plan/mariam/week3.md:55-57). This file is therefore
# NOT on the `agentcore launch` path. It exists for two things that path does
# not give you:
#   1. A reviewable, checked-in statement of what the runtime image must
#      contain: pinned base, non-editable install, all four subpackages, the
#      fixtures, the scanners, and the fail-closed scanner flag.
#   2. A build a teammate can run on a laptop to reproduce or debug the runtime
#      environment without touching AWS.
# If the two ever disagree, this file is the specification and the generated one
# is the artifact. Reconcile them deliberately.
#
# NOT VALIDATED BY A CONTAINER BUILD. No container runtime exists on the
# authoring machine -- `command -v docker`, `podman` and `finch` each returned
# rc=1. Everything asserted below was checked by running the equivalent
# operation with pip against a `--target` directory instead; see
# infra/agentcore/README.md, "What was validated and what was not". The scanner
# stage's download URLs are the one part adapted rather than measured, and it
# carries its own error checking so it cannot fail quietly -- see the comment
# there before changing it.

# Pinned to an explicit minor, never `latest` and never a bare `3` -- a floating
# tag moves the interpreter under a deployed runtime with no diff to review.
#
# pyproject.toml:18 declares `requires-python = ">=3.12"`, and the reason is
# agentorg/state.py:12 `from datetime import UTC, datetime`, which needs 3.11+.
# 3.12 is the declared floor, so the image runs the OLDEST interpreter the
# project claims to support. That direction is chosen on purpose: an image on a
# newer interpreter than the declared floor can pass every test and then fail on
# something the floor does not have.
#
# Digest deliberately omitted rather than invented. Pinning by digest is
# stronger than pinning by tag, but this machine has no container runtime with
# which to resolve `python:3.12-slim-bookworm` to a real digest, and a fabricated
# digest is worse than an honest tag. Resolve and pin it on the first machine
# that runs a build.
FROM python:3.12-slim-bookworm

# ---------------------------------------------------------------------------
# The three scanners the security agent shells out to.
#
# THIS STAGE IS LOAD-BEARING, AND ITS URLS ARE UNVERIFIED FROM THIS MACHINE.
#
# Why it is load-bearing: agentorg/common/config.py:73-77 says SCANNERS_REQUIRED
# must be true "on the demo machine and in any production image", because with it
# false a missing binary makes each wrapper raise, agents/security.py catch it,
# and the gate fall back to a FIXTURE verdict -- the gate reports clean because
# it never ran, which is failing OPEN. This image sets that flag true further
# down. The flag and these binaries are two halves of one decision: the flag
# without the binaries turns every scan into a hard fault, and the binaries
# without the flag leaves the fail-open path live.
#
# What is unverified: the URLs are adapted from .github/workflows/ci.yml:143-169,
# which is real, working, checked-in truth -- but for linux_x64. `agentcore
# launch` builds ARM64 (docs/plan/mariam/week3.md:55), so the architecture
# segment of each URL had to change, and NO fetch was performed from this machine
# to confirm the ARM64 asset names exist under these release tags. Treat them as
# the first thing to check if a build fails here.
#
# WHY THAT IS SAFE TO SHIP UNVERIFIED, rather than stubbed out: this stage cannot
# fail quietly. `curl -sSfL` uses -f, so a wrong asset name is an HTTP error that
# fails the curl, fails the && chain, and fails the build. And the chain ENDS with
# `gitleaks version && trivy --version && semgrep --version`, so the build only
# proceeds if all three are present AND executable. An earlier draft of this file
# also appended `&& false` to force a failure here; that was removed because it
# guaranteed the build could never succeed even with correct URLs, while adding no
# protection the -f and the version checks do not already give. The version-check
# tail is the real guarantee -- do not remove it.
#
# Versions are CI's pins (ci.yml:124-126), not the authoring machine's
# (gitleaks 8.30.1 / semgrep 1.173.0 / trivy 0.74.0 on /opt/homebrew/bin), so the
# image matches what the suite is gated against. trivy agrees at 0.74.0; gitleaks
# and semgrep deliberately do not. Record resolved checksums here once a real
# build has run.
ARG GITLEAKS_VERSION=8.21.2
ARG TRIVY_VERSION=0.74.0
ARG SEMGREP_VERSION=1.172.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && curl -sSfL -o /tmp/gitleaks.tar.gz \
      "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_arm64.tar.gz" \
 && tar -xzf /tmp/gitleaks.tar.gz -C /tmp gitleaks \
 && mv /tmp/gitleaks /usr/local/bin/gitleaks \
 && curl -sSfL -o /tmp/trivy.tar.gz \
      "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-ARM64.tar.gz" \
 && tar -xzf /tmp/trivy.tar.gz -C /tmp trivy \
 && mv /tmp/trivy /usr/local/bin/trivy \
 && python -m venv /tmp/semgrep-venv \
 && /tmp/semgrep-venv/bin/pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}" \
 && ln -s /tmp/semgrep-venv/bin/semgrep /usr/local/bin/semgrep \
 && rm -f /tmp/gitleaks.tar.gz /tmp/trivy.tar.gz \
 && gitleaks version && trivy --version && semgrep --version

WORKDIR /app

# Dependencies before source, so a source-only change does not re-resolve them.
# This is the SAME file AgentCore installs from (`agentcore configure -rf
# requirements.txt`), so the laptop build and the AgentCore build agree on the
# dependency set by construction rather than by discipline.
COPY agentorg/agents/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Source, then a NON-EDITABLE install.
#
# `pip install .`, never `-e .`: an editable install resolves imports through a
# .pth finder pointing at the source tree, which is exactly what hid the
# packaging defect tests/test_packaging.py now pins. The container must exercise
# the same install path that test does.
#
# Only the three paths the package needs are copied, rather than the whole
# context. That is belt-and-braces with .dockerignore: build/ holds a STALE copy
# of several modules missing the subpackages, and if .dockerignore were ever
# lost, a `COPY . .` here is the line that would ship it.
COPY pyproject.toml README.md ./
COPY agentorg ./agentorg
RUN pip install --no-cache-dir --no-deps .

# fixtures/ is NOT part of the installed package, and all five agents need it.
#
# MEASURED DEFECT, not a precaution. agentorg/fixtures_loader.py:21 computes
#     _FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
# which resolves relative to the INSTALL location. In a non-editable install
# that is the directory holding the package -- site-packages -- not the repo
# root. Measured on the authoring machine with
# `pip install --no-deps --no-cache-dir --target=/tmp/nei .`:
#     _FIXTURES resolves to: /private/tmp/nei/fixtures
#     exists? False
#     plan() RAISED: FileNotFoundError .../nei/fixtures/plan_result.json
# Every one of the five agents calls fixtures_loader on its fallback path, and
# that path is the NORMAL one whenever a model call returns None -- which is what
# happens with no credentials or an unreachable Bedrock. So without this copy the
# runtime does not degrade to fixtures, it raises FileNotFoundError. After
# copying fixtures/ beside the installed package all five loaders returned their
# models: plan -> PlanResult, dev -> DevResult, review -> ReviewResult,
# security -> SecurityResult, sre -> SREResult.
#
# The destination is COMPUTED from sysconfig, never hardcoded to a
# python3.12 path, so bumping the base image cannot silently orphan it.
COPY fixtures /tmp/fixtures
RUN cp -R /tmp/fixtures "$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/fixtures" \
 && rm -rf /tmp/fixtures

# Absent scanners are a FAULT in a deployed image, not a development
# affordance. agentorg/common/config.py:73-77: true "promotes absent to fault,
# and the fault blocks loudly instead of quietly borrowing a fixture verdict."
# Paired with the scanner stage above -- neither half is correct alone.
ENV SCANNERS_REQUIRED=true

# Region is the one recorded in docs/plan/week1-verification-log.md. The model id
# is the one the deploy command passes (docs/plan/sorour/week3.md:293,
# `--env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0`), set here too so a laptop
# build behaves like the runtime. No credentials, keys or tokens anywhere in this
# file: the runtime gets them from the AgentCore runtime role.
ENV AWS_REGION=us-east-1
ENV BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0

# Unbuffered, so a crash loop's traceback reaches CloudWatch instead of dying in
# a full stdout buffer.
ENV PYTHONUNBUFFERED=1

# Fail the build if the packaging defect ever returns.
#
# These are the same three modules tests/test_packaging.py imports, and `-S`
# mirrors that test's isolation so a stray .pth cannot be what satisfies the
# import. The second line proves the fixtures copy above actually landed where
# fixtures_loader looks -- the check that would have caught the measured defect.
# A build reaching this point has proved the image can import what it will run.
RUN python -S -c "import agentorg.graph, agentorg.agents.security, agentorg.common.llm" \
 && python -S -c "import agentorg.fixtures_loader as f; assert f._FIXTURES.exists(), f._FIXTURES; f.plan(); f.dev(); f.review(); f.security(); f.sre()"

# No CMD or ENTRYPOINT. AgentCore supplies the entrypoint via
# `agentcore configure -e <agent>.py`, and that is what selects which of the five
# agents a runtime serves; a CMD here would be either ignored or misleading.
#
# NOTE for whoever wires the entrypoint: the five agent modules are LIBRARY
# functions, not servers. Each exposes `run(state: RunState) -> ...Result` and
# none has a `__main__` guard or an AgentCore app object -- measured, and
# `python agentorg/agents/planner.py` fails with
# `ImportError: attempted relative import with no known parent package` because
# of the `from .. import fixtures_loader` at planner.py:13. `agentcore configure
# -e planner.py` expects an entrypoint it can serve, so an adapter exposing
# these functions over the AgentCore contract is still required. That work is
# NOT in this image and NOT in this task. See infra/agentcore/README.md,
# "The gap this task did not close".
#
# For a laptop smoke check of the image:
#   docker run --rm <image> python -c "import agentorg.agents.planner as p; print(p.run)"
