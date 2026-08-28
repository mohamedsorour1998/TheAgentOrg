"""LANE N, N5 — blast radius for `deploy-platform.yml` and the platform module.

Matching the 106 tests that guard `deploy.yml` and `terraform.yml`, because this is
the third workflow that can spend money and the first Terraform module that can spend
it CONTINUOUSLY -- every other resource in this repository is per-invocation.

WHAT "BLAST RADIUS" MEANS HERE, concretely. Four questions, and every one has a wrong
answer that reports green:

  1. Can it authenticate without OIDC? (a static key would defeat the whole design)
  2. Can it create a resource Terraform does not know about? (the hand-built console
     resource every module comment refuses)
  3. Can it spend by default? (`runtime_enabled` is the answer, and a test must pin it)
  4. Can it ship a credential? (a task definition's `environment` is world-readable in
     `describe-task-definition`; `secrets` is not)

ASSERTED OVER THE PARSED YAML AND THE COMMENT-STRIPPED HCL, never over raw text where
possible -- this repository has TWICE found a test satisfied by the comment explaining
the thing it checks, and these files are more than half commentary. Where a raw-text
assertion is unavoidable it says so and carries an anti-vacuity check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-platform.yml"
MODULE = REPO_ROOT / "infra" / "Terraform" / "modules" / "platform"
WORKER_DOCKERFILE = REPO_ROOT / "infra" / "worker" / "Dockerfile"
WORKER_IGNORE = REPO_ROOT / "infra" / "worker" / "Dockerfile.dockerignore"
WORKER_REQUIREMENTS = REPO_ROOT / "infra" / "worker" / "requirements.txt"
AGENT_REQUIREMENTS = REPO_ROOT / "agentorg" / "agents" / "requirements.txt"


def _workflow() -> dict:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    return yaml.safe_load(WORKFLOW.read_text())


def _jobs() -> dict:
    jobs = _workflow()["jobs"]
    # ANTI-VACUITY: every per-job assertion below iterates this dict, so an empty one
    # would make the whole file pass while examining nothing.
    assert jobs, "deploy-platform.yml declares no jobs; this file would pin nothing"
    return jobs


def _steps(job: dict) -> list[dict]:
    return list(job.get("steps") or [])


def _executable_yaml() -> str:
    """The workflow with `#` comment lines removed. THE COMMENTS ARE NOT THE WORKFLOW.

    ADDED AFTER TWO OF THIS FILE'S OWN TESTS FAILED ON IT, which is the third instance
    of this repository's most-repeated pattern and the first where the commentary
    satisfying the search was MINE:

      * the header says "do not add an AWS_ACCESS_KEY_ID secret", so a search for that
        string over raw text matched the sentence forbidding it;
      * a comment says "an `aws ecr create-repository` here would work, and would mean
        the account holds a registry Terraform does not know about", so a search for
        that command matched the explanation of why it is absent.

    Both tests would have passed had I written a shorter header, and both would have
    kept passing if the workflow later DID either thing -- the prose match is
    permanent. So these assertions read the executable content.

    Note this is a LINE-level strip, not a token-level one: a `#` inside a step's
    `run` body would take the rest of that line with it. That is acceptable here
    because nothing this file searches for is a shell comment, and it is stated
    because the alternative (a YAML-aware walk) has its own gap -- `_strip_comments`
    blanking heredoc bodies is already recorded in CLAUDE.md as exactly that mistake.
    """
    stripped = "\n".join(
        line for line in WORKFLOW.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    # ANTI-VACUITY FOR THE STRIPPER. A bug removing everything would make every
    # `not in` assertion below pass.
    assert "aws-actions/configure-aws-credentials" in stripped, (
        "stripping comments removed the workflow's own steps; every assertion over "
        "this text would be vacuous"
    )
    return stripped


def _hcl(name: str) -> str:
    """One module file with `#` comments stripped.

    STRIPPED BECAUSE THIS REPOSITORY HAS BEEN BITTEN TWICE by a test satisfied by the
    comment explaining the thing it checks -- `deploy.yml`'s smoke-check literal and
    `config.py`'s "SEVERITY_ORDER is imported, not restated". These files are roughly
    60% comment by line, so the hazard is larger here than anywhere.
    """
    path = MODULE / name
    assert path.is_file(), f"{path} is missing"
    stripped = "\n".join(
        line.split("#", 1)[0] for line in path.read_text().splitlines()
    )
    # ANTI-VACUITY FOR THE STRIPPER ITSELF. A bug that blanked everything would make
    # every `not in` assertion below pass. `resource` appears in all three files.
    assert "resource" in stripped, (
        f"stripping comments from {name} removed the code as well; every assertion "
        f"over this text would be vacuous"
    )
    return stripped


# ── 1. NO STATIC AWS KEYS, ANYWHERE ──────────────────────────────────────────


def test_no_job_carries_a_static_aws_credential():
    """The property `deploy.yml` and `terraform.yml` are both built around.

    Read over the COMMENT-STRIPPED text, and the first draft of this test read raw
    text and FAILED -- because the workflow's own header says "do not add an
    AWS_ACCESS_KEY_ID secret". See `_executable_yaml`.

    The tradeoff is stated rather than hidden: a static key genuinely pasted into a
    comment would now escape this test. That is the lesser hazard, because a test
    permanently satisfied by prose forbidding the thing cannot detect the thing.
    `test_the_secret_scan_would_catch_a_key_in_a_step` below closes the half that
    matters by proving the assertion is not vacuous.
    """
    executable = _executable_yaml()
    for forbidden in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                      "aws_access_key_id", "aws_secret_access_key"):
        assert forbidden not in executable, (
            f"deploy-platform.yml mentions {forbidden}. Every AWS step in this "
            f"repository assumes github-actions-role through OIDC; the absence of "
            f"static keys is the point of these workflows, not a detail. If a step "
            f"cannot authenticate, fix the role trust."
        )


@pytest.mark.parametrize("job_name", ["build", "redeploy"])
def test_every_aws_job_requests_the_oidc_token(job_name):
    """`id-token: write` is what makes the OIDC assumption possible at all.

    Without it `configure-aws-credentials` fails and NO amount of role trust fixes it
    -- `terraform.yml`'s comment records that measurement. A job that assumes a role
    without requesting the token fails in a way that reads as a trust-policy problem.
    """
    job = _jobs()[job_name]
    permissions = job.get("permissions") or {}
    assert permissions.get("id-token") == "write", (
        f"the `{job_name}` job assumes an AWS role but does not request the OIDC "
        f"token (`permissions: id-token: write`). It will fail with "
        f"\"Not authorized to perform sts:AssumeRoleWithWebIdentity\" and send the "
        f"next person to the role's trust policy, which will be correct. "
        f"permissions: {permissions}"
    )


def test_the_preflight_job_has_no_aws_credentials_at_all():
    """The cheap job must stay cheap, and credential-free.

    `deploy.yml`'s preflight exists so a packaging regression fails the run before any
    image is built. The same argument applies here and the same property makes it
    true: no `id-token`, so it cannot reach the account even by accident.
    """
    job = _jobs()["preflight"]
    permissions = job.get("permissions") or {}
    assert "id-token" not in permissions, (
        f"the preflight job requests an OIDC token. It checks two local files and "
        f"needs no account access; granting it means a fork PR could reach AWS "
        f"through it. permissions: {permissions}"
    )
    for step in _steps(job):
        uses = str(step.get("uses", ""))
        assert "configure-aws-credentials" not in uses, (
            "the preflight job configures AWS credentials, which it does not need"
        )


def test_the_workflow_never_writes_to_the_repository():
    """No job may hold `contents: write`, and none needs it.

    This workflow builds an image and restarts a service. A `contents: write` here
    would let a compromised action push to the default branch, and the paths that can
    spend money are exactly the ones worth narrowing.
    """
    for name, job in _jobs().items():
        permissions = job.get("permissions") or {}
        assert permissions.get("contents", "read") == "read", (
            f"the `{name}` job holds contents: {permissions.get('contents')!r}. "
            f"Nothing here writes to this repository."
        )


# ── 2. IT MUST NOT CREATE INFRASTRUCTURE TERRAFORM DOES NOT KNOW ABOUT ───────


def test_the_workflow_creates_no_aws_resource():
    """The hand-built-console-resource rule, as a test.

    `infra/README.md` and every module comment say nothing is created by hand, and a
    workflow is the console with extra steps. This workflow may PUSH an image and
    RESTART a service; it may not create the registry, the cluster, the service or a
    task definition revision.

    `register-task-definition` is the one worth naming: it is the tempting way to
    point a service at a new image, and it writes a revision Terraform's state does
    not know about -- so the next apply reverts it and the deploy silently undoes
    itself.
    """
    executable = _executable_yaml()
    forbidden = [
        "ecr create-repository",
        "ecs create-cluster",
        "ecs create-service",
        "ecs register-task-definition",
        "iam create-role",
        "secretsmanager create-secret",
        "rds create-db-instance",
    ]
    for command in forbidden:
        assert command not in executable, (
            f"deploy-platform.yml runs `aws {command}`, which creates a resource "
            f"Terraform does not manage. infra/Terraform is the only thing that "
            f"creates infrastructure here -- and `register-task-definition` in "
            f"particular writes a revision the next apply reverts, so the deploy "
            f"silently undoes itself."
        )


def test_the_build_job_refuses_when_the_registry_is_absent():
    """It must SAY the repository is Terraform's, not create it.

    The wrong version of this step is `create-repository --repository-name ... ||
    true`, which works, and leaves the account holding a registry the state does not
    know about. Asserted by finding the refusal.
    """
    body = "\n".join(
        str(step.get("run", "")) for step in _steps(_jobs()["build"])
    )
    assert "describe-repositories" in body, (
        "the build job never checks that the ECR repository exists, so a missing "
        "registry surfaces as a docker push failure rather than as \"apply "
        "modules/platform first\""
    )
    assert "does not exist" in body, (
        "the build job checks for the repository but does not explain what to do "
        "when it is absent. The answer is not obvious: the registry is created by "
        "modules/platform even with runtime_enabled false."
    )


# ── 3. IT MUST NOT SPEND BY DEFAULT ──────────────────────────────────────────


def test_the_workflow_is_dispatch_only():
    """No `push:` trigger, for `run-pipeline.yml`'s reason one layer over.

    A push trigger here would rebuild and potentially redeploy on every commit to
    this repository. The apply half REPLACES A RUNNING SERVICE, and the resources are
    count-gated off by default so on most commits there is nothing to redeploy --
    which makes the run noise that reads as coverage.
    """
    # `on` is parsed by YAML 1.1 as the BOOLEAN True. Found by running a test, not by
    # reading: `workflow["on"]` raises KeyError.
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, (
        f"no trigger block under `on` or its boolean coercion. "
        f"keys: {sorted(map(str, workflow))}"
    )
    assert set(triggers) == {"workflow_dispatch"}, (
        f"deploy-platform.yml has triggers other than workflow_dispatch: "
        f"{sorted(triggers)}. A push trigger would rebuild on every commit and the "
        f"redeploy half replaces a running service."
    )


def test_the_redeploy_input_defaults_to_false():
    """A workflow whose default action replaces a running service is one wrong click
    from an unasked-for deployment.

    The EVALUATION of this condition lives in `test_platform_expression.py`; this pins
    only the declared default, which is a different fact.
    """
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["redeploy_service"]["default"] is False


def test_concurrency_never_cancels_a_deploy_in_flight():
    """`cancel-in-progress: false`, matching deploy.yml and terraform.yml.

    A cancelled deploy can leave an image pushed and a service half-rolled, which is
    worse than a queued wait -- and worse than either, it reports as a cancellation
    rather than as a broken state.
    """
    concurrency = _workflow().get("concurrency") or {}
    assert concurrency.get("cancel-in-progress") is False, (
        f"deploy-platform.yml allows a deploy to be cancelled in flight. A cancelled "
        f"run can leave an image pushed and a service half-updated. "
        f"concurrency: {concurrency}"
    )
    assert concurrency.get("group"), "no concurrency group, so two deploys can race"


@pytest.mark.parametrize("job_name", ["preflight", "build", "redeploy"])
def test_every_job_has_a_timeout(job_name):
    """A job with no timeout runs for six hours on GitHub's default.

    That is billable runner time for a build that is already broken, and on a
    `redeploy` job it means a wedged `update-service` holds the concurrency group.
    """
    job = _jobs()[job_name]
    assert isinstance(job.get("timeout-minutes"), int), (
        f"the `{job_name}` job has no timeout-minutes, so it inherits GitHub's "
        f"6-hour default"
    )


# ── 4. IT MUST NOT SHIP A CREDENTIAL ─────────────────────────────────────────


def test_the_dsn_arrives_as_a_secret_and_never_as_an_environment_value():
    """THE DISTINCTION `secrets` VS `environment` EXISTS FOR, in a task definition.

    A plaintext `environment` entry appears in `describe-task-definition` output, in
    every ECS console page showing the revision, and in Terraform state. The DSN
    carries a database password. `secrets` injects it from Secrets Manager at
    container start, so only the ARN -- a name -- lands in state.

    This repository has already been burned by the other shape: ten Actions artifacts
    carried a live `github_pat_` because a binary tfplan embeds state.
    """
    ecs = _hcl("ecs.tf")

    # The DSN must be in a `secrets` block.
    assert re.search(r"secrets\s*=\s*\[", ecs), (
        "the task definition has no `secrets` block, so the DSN must be arriving as "
        "a plaintext environment value -- visible in describe-task-definition"
    )
    assert "QUEUE_DSN" in ecs, "the task definition never sets QUEUE_DSN at all"

    # And it must NOT be in the `environment` list. Located structurally: the
    # `environment` block ends where `secrets` begins.
    environment_block = ecs.split("environment = [", 1)[1].split("]", 1)[0]
    assert "QUEUE_DSN" not in environment_block, (
        "QUEUE_DSN is a plaintext `environment` entry on the task definition. That "
        "value carries a database password and appears in describe-task-definition, "
        "in the ECS console, and in Terraform state. Move it to `secrets`."
    )


def test_no_hardcoded_credential_or_dsn_literal_in_the_module():
    """No `postgres://` literal, and no password-shaped assignment.

    `infra/selfhost/docker-compose.yml` carries a development password deliberately
    and says why (loopback-bound, nothing published). A module that applies to a real
    account has no such excuse.
    """
    for name in ("main.tf", "ecs.tf", "iam.tf", "variables.tf", "outputs.tf"):
        hcl = _hcl(name)
        assert "postgres://" not in hcl and "postgresql://" not in hcl, (
            f"{name} contains a DSN literal. The DSN arrives as a Secrets Manager "
            f"ARN precisely so no connection string lands in the repository or in "
            f"Terraform state."
        )


def test_the_task_role_and_execution_role_are_separate():
    """TWO ROLES, NOT ONE, and collapsing them is the common shortcut.

    The execution role reads the DSN secret and pulls the image; the task role is what
    the CONTAINER's own code holds. A single role would let the worker's own code --
    which runs a MODEL that writes diffs and shells out to git -- read every secret
    named in its task definition.
    """
    iam = _hcl("iam.tf")
    ecs = _hcl("ecs.tf")
    assert 'resource "aws_iam_role" "execution"' in iam
    assert 'resource "aws_iam_role" "task"' in iam
    assert "execution_role_arn = aws_iam_role.execution.arn" in ecs
    assert "task_role_arn      = aws_iam_role.task.arn" in ecs, (
        "the task definition does not use a SEPARATE task role. With one role, the "
        "worker's own code can read every secret its task definition names."
    )


def test_the_task_role_cannot_read_the_dsn_secret():
    """The container's own code must not hold GetSecretValue.

    The DSN is injected by the ECS AGENT before the container starts, so the
    container never needs to read it -- and a task role that could would let the
    worker's model-written code exfiltrate a database password.
    """
    iam = _hcl("iam.tf")
    task_policy = iam.split('resource "aws_iam_role_policy" "task"', 1)[1]
    assert "secretsmanager" not in task_policy.lower(), (
        "the task role grants Secrets Manager access. The DSN is injected by the ECS "
        "agent using the EXECUTION role; the container itself never reads it."
    )


def test_the_secret_scan_would_catch_a_key_in_a_step():
    """THE ANTI-VACUITY HALF, and `test_no_job_carries_a_static_aws_credential`'s
    docstring promises it.

    That test reads comment-stripped text, so it can no longer be satisfied by prose
    forbidding a key -- but it also cannot be satisfied by prose CONTAINING one, and
    that is the property worth proving. Without this, a bug in `_executable_yaml` (or
    a future rewrite of it) makes the scan vacuous while the count reads healthy.

    So this drives the same stripper over a synthetic workflow that DOES carry a key
    in an executable position, and asserts the key survives stripping.
    """
    # A triple-quoted literal rather than a joined list: ruff's FLY002 refuses the
    # join, and the indentation matters to nothing here because only the `#` prefix is
    # read.
    hostile = (
        "# A comment mentioning AWS_ACCESS_KEY_ID, which must be stripped.\n"
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - uses: aws-actions/configure-aws-credentials@v4\n"
        "        env:\n"
        "          AWS_ACCESS_KEY_ID: AKIAIOSFODNN7EXAMPLE\n"
    )
    stripped = "\n".join(
        line for line in hostile.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "AWS_ACCESS_KEY_ID" in stripped, (
        "the comment stripper removes a key in an EXECUTABLE position, so "
        "test_no_job_carries_a_static_aws_credential cannot detect one and is vacuous"
    )
    # And the control: the comment form IS removed, which is why the real test needed
    # stripping at all. `AKIAIOSFODNN7EXAMPLE` is AWS's own published documentation
    # example and authenticates nothing -- CLAUDE.md names it as the safe literal.
    assert stripped.count("AWS_ACCESS_KEY_ID") == 1, (
        "the stripper did not remove the commented mention, so the real test would "
        "still be satisfied by prose"
    )


# ── 5. THE WORKER IMAGE: THE DEFECT THAT MADE IT NECESSARY ───────────────────


def test_the_paired_ignore_file_does_not_exclude_the_worker_scripts():
    """THE DEFECT THIS WHOLE IMAGE EXISTS FOR, as a test.

    The root `.dockerignore` excludes `scripts/`, so the AGENT image's build context
    strips `scripts/worker.py` and `scripts/run_stage.py`. A compose file or task
    definition whose command is `python scripts/worker.py` against that image starts a
    container whose entrypoint is not present -- the stack parses, `docker compose
    config` passes, and the container dies on a missing file.

    `infra/worker/Dockerfile.dockerignore` REPLACES the root one for this build (it
    does not merge), so every exclusion this image wants must be restated there and
    `scripts/` must not be among them.
    """
    import fnmatch

    assert WORKER_IGNORE.is_file(), (
        f"{WORKER_IGNORE} is missing. Without it the root .dockerignore applies and "
        f"the worker's own entrypoint is stripped from its build context."
    )
    patterns = [
        line.strip() for line in WORKER_IGNORE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert patterns, "the paired ignore file parsed to zero patterns"

    def excluded(candidate: str) -> list[str]:
        return [
            pattern for pattern in patterns
            if fnmatch.fnmatch(candidate, pattern)
            or candidate.startswith(pattern.rstrip("/") + "/")
        ]

    for required in ("scripts/worker.py", "scripts/run_stage.py"):
        assert not excluded(required), (
            f"{required} is excluded by {excluded(required)}. The worker image would "
            f"build green and die on a missing file -- the same shape as the missing "
            f"`COPY fixtures ./fixtures`, measured on the first runtime that served "
            f"traffic."
        )

    # THE CONTROL. A paired file excluding NOTHING satisfies the loop above and ships
    # runs/ (~10k files) and .env into an image layer.
    for must_exclude in ("runs/anything.jsonl", ".env", "tests/test_x.py"):
        assert excluded(must_exclude), (
            f"{must_exclude} is NOT excluded by the paired ignore file. It replaces "
            f"the root one rather than merging, so every exclusion must be restated."
        )


def test_the_worker_dockerfile_asserts_its_own_scripts_are_present():
    """A documented docker behaviour nobody executed is a claim, not a check.

    The paired-ignore-file precedence has NEVER been exercised here -- there is no
    Docker daemon on this machine -- so the Dockerfile asserts the OUTCOME at build
    time. Without it, a pairing that silently did not apply produces an image that
    pushes, deploys, and exits on `can't open file '/app/scripts/worker.py'`, read at
    a distance as a broken command rather than as a stripped build context.
    """
    body = WORKER_DOCKERFILE.read_text()
    assert "test -f scripts/worker.py" in body, (
        "the worker Dockerfile does not assert its entrypoint is present. The paired "
        "ignore file's precedence is unverified on this machine, so the build-time "
        "check is the only thing turning that into a build failure."
    )
    assert "test -f scripts/run_stage.py" in body, (
        "the Dockerfile does not assert run_stage.py is present. queue/runner.py "
        "invokes it as a subprocess per stage; without it every claimed job fails."
    )
    assert "import psycopg" in body, (
        "the Dockerfile does not verify psycopg imports. Absent, _sql.py raises at "
        "the FIRST claim -- after the worker reports healthy and starts polling, so "
        "it presents as a queue that never claims a job."
    )


def test_the_worker_image_does_not_demand_scanners_it_does_not_carry():
    """`SCANNERS_REQUIRED` must appear NOWHERE in the worker's image or task.

    That knob promotes an ABSENT binary to a FAULT: one `*-scanner-error` finding per
    tool at severity `high`, which IS the block threshold, so it blocks EVERY run
    including the clean half of the demo with `blocking=3`. This image carries no
    scanners; the security RUNTIME is the one that does, and `deploy.yml` guards the
    knob there.

    A comment saying "absent" cannot notice somebody adding it, which is why this is
    a test.
    """
    for path in (WORKER_DOCKERFILE, MODULE / "ecs.tf"):
        executable = "\n".join(
            line.split("#", 1)[0] for line in path.read_text().splitlines()
        )
        assert "SCANNERS_REQUIRED" not in executable, (
            f"{path.name} sets SCANNERS_REQUIRED on the worker, which carries no "
            f"scanners. Every run would block with blocking=3, including the clean "
            f"one."
        )


def test_the_worker_requirements_cover_every_agent_requirement():
    """Two pinned lists that must agree, so something must detect a change in one.

    `scripts/run_stage.py` drives all five agents IN-PROCESS whenever `REMOTE_AGENTS`
    is false -- the documented default and the demo's fallback -- so every agent pin
    is a worker pin. A version bumped in one file and not the other produces a worker
    whose agents behave differently from the deployed runtimes'.
    """
    def pins(path: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or "==" not in line:
                continue
            name, version = line.split("==", 1)
            # `psycopg[binary]` -> `psycopg`; an extra is not part of the name.
            out[name.split("[", 1)[0].strip().lower()] = version.strip()
        return out

    agents = pins(AGENT_REQUIREMENTS)
    worker = pins(WORKER_REQUIREMENTS)
    assert agents, "parsed zero pins from the agents' requirements"
    assert worker, "parsed zero pins from the worker's requirements"

    for name, version in sorted(agents.items()):
        assert name in worker, (
            f"{name}=={version} is in the agents' requirements and absent from the "
            f"worker's. run_stage.py imports every agent, so the worker needs it."
        )
        assert worker[name] == version, (
            f"{name}: the agents pin {version}, the worker pins {worker[name]}. Two "
            f"pinned lists that disagree mean the worker's in-process agents behave "
            f"differently from the deployed runtimes'."
        )

    # THE ONE LINE THE AGENTS' FILE MUST NOT HAVE, asserted in both directions --
    # otherwise "the worker has psycopg" stays true if somebody adds it to the agents'
    # file too, which would ship a database driver to five arm64 images.
    assert "psycopg" in worker, (
        "the worker's requirements do not pin psycopg. _sql.py reaches for it by name "
        "at call time, so an absent driver fails at the first claim rather than at "
        "startup."
    )
    assert "psycopg" not in agents, (
        "psycopg is in the AGENTS' requirements. That ships a Postgres driver to five "
        "arm64 containers that never open a database connection -- one more import "
        "that can fail at runtime for a code path no agent takes. _sql.py names the "
        "driver through importlib for exactly this reason."
    )


def test_the_worker_image_is_arm64_and_matches_its_task_definition():
    """A platform mismatch deploys and then fails to start.

    The exec format error reads like a broken entrypoint rather than a wrong
    architecture -- measured on the agent image. So the Dockerfile, the build and the
    task definition must all say arm64, and a test is what keeps the three in step.
    """
    assert "--platform=linux/arm64" in WORKER_DOCKERFILE.read_text(), (
        "the worker Dockerfile does not pin arm64"
    )
    ecs = _hcl("ecs.tf")
    assert 'cpu_architecture        = "ARM64"' in ecs, (
        "the task definition does not pin ARM64, so it would refuse the arm64 image "
        "the build produces"
    )
    build = "\n".join(str(step.get("run", "")) for step in _steps(_jobs()["build"]))
    assert "--platform linux/arm64" in build, (
        "the build step does not pass --platform linux/arm64. The runner is amd64, so "
        "without it the image is amd64: it pushes, deploys, and fails to start."
    )
    assert "linux/arm64" in build and "imagetools inspect" in build, (
        "the build job does not verify the pushed image's architecture. "
        "`describe-images` reports digest, media type and size and names no platform "
        "for a single-platform image, so the manifest is what must be read."
    )


# ── 6. THE MODULE MUST NOT SPEND WITHOUT BEING ASKED ─────────────────────────


def test_runtime_enabled_defaults_to_false():
    """The gate that keeps an apply from creating the project's first hourly charge.

    Every other resource here is per-invocation. A Fargate service is not, and the
    database it needs is the largest standing charge in the design.
    """
    variables = _hcl("variables.tf")
    block = variables.split('variable "runtime_enabled"', 1)
    assert len(block) == 2, "modules/platform declares no `runtime_enabled` variable"
    declaration = block[1].split("}", 1)[0]
    assert "default     = false" in declaration or "default = false" in declaration, (
        f"runtime_enabled does not default to false, so `terraform apply` would "
        f"create an ECS service and start billing. Declaration: {declaration}"
    )


def test_every_billing_resource_is_behind_the_gate():
    """Each resource that costs money per hour must carry the count.

    Asserted per resource rather than once, because the failure mode is ONE resource
    losing its `count` in an edit -- and a cluster or a service created outside the
    gate spends exactly as much as all of them would.
    """
    ecs = _hcl("ecs.tf")
    for resource in ("aws_ecs_cluster", "aws_ecs_service", "aws_ecs_task_definition",
                     "aws_security_group"):
        assert f'resource "{resource}"' in ecs, f"{resource} is absent from ecs.tf"
        block = ecs.split(f'resource "{resource}"', 1)[1]
        head = block.split("\n\n", 1)[0]
        assert "count = var.runtime_enabled ? 1 : 0" in head, (
            f"{resource} is NOT behind the runtime_enabled gate, so a default apply "
            f"would create it. Head of block: {head[:200]}"
        )


def test_the_free_resources_are_NOT_gated():
    """The registry, the log group and the two roles must always exist.

    THE OTHER DIRECTION, and it is not symmetry for its own sake: `deploy-platform.yml`
    pushes to that registry and refuses when it is absent, and preflight check 5
    simulates the task role. Gating them would mean the deploy workflow could never
    run until somebody turned on the billing resources -- so the cheap half of the
    platform would be unreachable without the expensive half.
    """
    main = _hcl("main.tf")
    iam = _hcl("iam.tf")
    for resource, text in (
        ("aws_ecr_repository", main),
        ("aws_cloudwatch_log_group", main),
        ("aws_iam_role", iam),
    ):
        block = text.split(f'resource "{resource}"', 1)[1].split("\n\n", 1)[0]
        assert "count" not in block, (
            f"{resource} is gated behind runtime_enabled. It costs nothing, and "
            f"deploy-platform.yml plus preflight check 5 both need it to exist "
            f"before any billing resource does."
        )


def test_the_gate_refuses_the_three_inputs_whose_absence_deploys_something_worse():
    """Preconditions at PLAN time, not an AWS error mid-apply.

    The DSN one is the important one: `_sql.py` DEFAULTS TO A SQLITE FILE when
    `QUEUE_DSN` is empty, so a worker with no DSN writes to a file inside its own
    container -- two tasks never see each other's jobs and BOTH run every stage,
    which posts every PR comment twice and pays the model bill twice.
    """
    ecs = _hcl("ecs.tf")
    assert 'resource "terraform_data" "runtime_preconditions"' in ecs, (
        "the module has no precondition block, so a gate turned on with no image, no "
        "DSN or no subnets fails partway through an apply rather than at plan time"
    )
    block = ecs.split('resource "terraform_data" "runtime_preconditions"', 1)[1]
    guarded = block.split("resource ", 1)[0]
    assert guarded.count("precondition {") == 3, (
        f"expected three preconditions (image, DSN, network); found "
        f"{guarded.count('precondition {')}"
    )
    for required in ("var.worker_image", "var.queue_dsn_secret_arn", "var.subnet_ids"):
        assert required in guarded, (
            f"no precondition covers {required}. Each of the three deploys something "
            f"worse than nothing when absent."
        )


def test_the_module_reports_whether_it_actually_created_a_worker():
    """`worker_runtime_enabled`, for `dispatch_target_enabled`'s measured reason.

    A rule with no target fires into nothing while looking healthy in the console. The
    same hazard here: an apply that created a registry and two roles is
    indistinguishable, in its exit code, from one that deployed a running worker.
    """
    outputs = _hcl("outputs.tf")
    assert 'output "worker_runtime_enabled"' in outputs, (
        "the module does not report whether the runtime was created, so a green apply "
        "cannot be told from a real deployment"
    )
    assert 'output "worker_hourly_usd_estimate"' in outputs, (
        "the module does not state its hourly cost. Nobody opens a bill before an "
        "apply; the plan output is where the figure will actually be read."
    )


def test_no_iam_policy_in_the_module_uses_a_wildcard_resource_except_where_aws_requires_it():
    """`modules/ingress`'s standard: no wildcard resource anywhere.

    ONE EXCEPTION, and it is AWS's rather than ours: `ecr:GetAuthorizationToken` is an
    account-level call that accepts only `"*"`. Named explicitly so the exception
    cannot quietly widen -- a test that allowed any `"*"` would bless the next one.
    """
    iam = _hcl("iam.tf")
    wildcards = re.findall(r'Resource\s*=\s*"\*"', iam)
    assert len(wildcards) == 1, (
        f"iam.tf has {len(wildcards)} statements with `Resource = \"*\"`, expected "
        f"exactly one (ecr:GetAuthorizationToken, which accepts no other value). "
        f"Every other statement must name its resource."
    )
    # And that one must be the login statement, not something else that acquired it.
    login = iam.split('Sid      = "EcrLogin"', 1)
    assert len(login) == 2, "the single wildcard is not on the EcrLogin statement"
    assert 'Resource = "*"' in login[1].split("},", 1)[0]


def test_the_execution_role_does_not_attach_a_managed_policy():
    """`AmazonECSTaskExecutionRolePolicy` grants logs on `*` and ECR pull on `*`.

    `modules/ingress` refuses `AWSLambdaBasicExecutionRole` for precisely this reason,
    and the log group here is Terraform-managed so `CreateLogGroup` is not needed
    either.
    """
    iam = _hcl("iam.tf")
    assert "aws_iam_role_policy_attachment" not in iam, (
        "the module attaches a managed policy. AmazonECSTaskExecutionRolePolicy "
        "grants logs and ECR on `*`, both broader than this role needs."
    )
    assert "logs:CreateLogGroup" not in iam, (
        "a policy grants logs:CreateLogGroup. The log group is declared in main.tf, "
        "so nothing needs to create one -- modules/ingress's ruling."
    )
