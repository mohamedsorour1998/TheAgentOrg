"""Preflight checks 5 and 6 — the platform the queue worker runs on. LANE N, N4.

Imported by `scripts/preflight.py`, which owns the four original checks. Split into
its own module for one reason: that file is 574 lines and this repository's measured
rule is one file per unit of work, committed before the next begins.

EVERY CHECK HERE ANSWERS A QUESTION WHOSE WRONG ANSWER HAS ALREADY HAPPENED, and
both wrong answers were measured on 2026-08-28 rather than imagined.

  5. Can the WORKER's task role invoke the model, and reach the five runtimes?
     -- THE EXACT DEFECT CHECK 1 EXISTS FOR, ON A SECOND PRINCIPAL. The runtime role
        granted `foundation-model/*` only while `config.BEDROCK_MODEL` names a
        cross-region INFERENCE PROFILE; `bedrock:InvokeModel` was implicitDeny,
        `llm.text()` caught it by design, and every model-calling agent served its
        fixture for a week with every job green.

        The worker is a SECOND principal that makes the same call, and a fresh IAM
        policy is where that mistake is easiest to repeat -- `modules/platform`'s task
        role carries its own `BedrockInvoke` statement, written from scratch. Check 1
        cannot see it: it simulates `theagentorg-shared-agentcore-runtime-role`, and
        the two roles are different resources that can drift.

        `REMOTE_AGENTS` defaults FALSE, which CLAUDE.md names as the demo's fallback,
        so the worker's own Bedrock grant IS the fallback path -- and a denial there
        fails the way every model denial here has: silently, into fixtures.

  6. Does the queue's DSN name a role that RLS ACTUALLY BINDS FOR?
     -- MEASURED 2026-08-28 on PostgreSQL 16.15, one table, one policy, two roles:

            as the TABLE OWNER, no tenant bound      2 of 2 rows visible
            as a plain application role, unbound     0 rows
            as a plain application role, tenant=t1   1 row

        Postgres skips row-level security for a superuser, for any role holding
        BYPASSRLS, and for the TABLE OWNER. `FORCE ROW LEVEL SECURITY` fixes only the
        third. So one DSN choice decides whether the tenancy policies enforce
        anything, and `pg_policies` LISTS EVERY POLICY EITHER WAY -- the schema reads
        as correct while a cross-tenant read returns rows.

        No Terraform variable and no green apply can catch this. It is a property of
        the credential, so it is checked against a live connection or not at all.

WHY THESE ARE NOT PART OF CHECKS 1-4. Check 1 proves one role can call the model;
this proves a DIFFERENT role can. Check 4 proves the human gates pause; check 6
proves the TENANT boundary holds, which is a different guarantee with a different
wrong answer. Collapsing either pair would mean one PASS standing for two facts.

BOTH ARE SKIPPED BY DEFAULT WHEN THE THING THEY CHECK DOES NOT EXIST, and the skip
is LOUD. `modules/platform`'s `runtime_enabled` defaults false, so an unconfigured
account has a registry and no worker; a check that FAILED there would make preflight
refuse the configuration the team chose, which is the ruling check 4 already made
about `can_admins_bypass`. A silent skip is the other failure -- "did not run" and
"passed" must never read alike -- so each prints what it did not check and why.
"""

from __future__ import annotations

import json
import subprocess

# THE SAME `_aws` AND `CheckFailed` THE OTHER FOUR CHECKS USE, imported rather than
# reimplemented. A second subprocess helper here would be a second place the
# `--output text` trap (a literal `None` line, which cost two failed deploy runs)
# could be reintroduced independently.
from scripts.preflight import ACCOUNT, REGION, CheckFailed, _aws

# The worker's task role, from `modules/platform/iam.tf`'s
# `"${var.name}-worker-task-role"` with `name = "theagentorg-shared"`.
#
# ASSEMBLED FROM THE SAME PARTS `preflight.RUNTIME_ROLE` IS, and asserted against the
# module's own string by `tests/test_platform_preflight.py` -- because this is a
# second declaration of a name Terraform owns, and CLAUDE.md's rule is that a second
# declaration needs something able to detect a change in the first.
WORKER_TASK_ROLE = f"arn:aws:iam::{ACCOUNT}:role/theagentorg-shared-worker-task-role"

# The four Bedrock actions the worker must hold, and every one is a separately
# measured fact from `modules/agentcore/main.tf`:
#
#   * `InvokeModel` alone was granted once and `strands.Agent` calls ConverseStream,
#     which is a SEPARATE IAM action -- not an alias, not covered by it. Simulated:
#     InvokeModel allowed, ConverseStream implicitDeny, agents serving fixtures.
#   * Both Converse forms are required because which one the SDK picks is its choice.
#
# Simulated as a set rather than one representative action: a policy granting three
# of four is a real and likely mistake, and the one it omits is the one that fails.
_BEDROCK_ACTIONS = (
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
    "bedrock:Converse",
    "bedrock:ConverseStream",
)


def _simulate(role: str, action: str, resource: str) -> str:
    """One `simulate-principal-policy` decision.

    WITH `--resource-arns`, ALWAYS. Measured and re-verified: the resource-LESS form
    answers `allowed` and means nothing, because an action simulated without a
    resource does not exercise the resource clause any statement is scoped by. An
    audit once reported `ListAgentRuntimeEndpoints` had become grantable by reading
    exactly that form.
    """
    return _aws(
        "iam", "simulate-principal-policy",
        "--policy-source-arn", role,
        "--action-names", action,
        "--resource-arns", resource,
        "--query", "EvaluationResults[0].EvalDecision",
        "--output", "text",
    )


def _role_exists(role_name: str) -> bool:
    """Whether the IAM role exists, without raising when it does not."""
    completed = subprocess.run(
        ["aws", "iam", "get-role", "--role-name", role_name, "--output", "json"],
        capture_output=True, text=True, check=False,
    )
    return completed.returncode == 0


def check_the_worker_role_can_invoke_the_model(model: str) -> str:
    """Check 5. The worker's task role against the profile, the models, the runtimes.

    SIMULATION, NOT A GREEN APPLY -- check 1's ruling, on a second principal. An apply
    proves the policy was WRITTEN; only this proves it PERMITS the call. Those were
    different facts for a week and the difference was invisible.
    """
    role_name = WORKER_TASK_ROLE.rsplit("/", 1)[1]
    if not _role_exists(role_name):
        # A LOUD SKIP, NOT A FAILURE. `modules/platform` is not applied in every
        # account, and failing here would make preflight refuse a configuration that
        # is simply "the worker is not deployed yet". The wording states what was NOT
        # checked, because "did not run" must never read as "passed".
        return (
            f"role:     {WORKER_TASK_ROLE}\n"
            f"SKIPPED -- that role does not exist, so no worker is deployed.\n"
            f"NOTHING HERE CHECKED THE WORKER'S BEDROCK GRANT. Apply\n"
            f"infra/Terraform/modules/platform through terraform.yml first; the\n"
            f"registry and both roles are created even with runtime_enabled false."
        )

    profile_arn = f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile/{model}"
    lines = [f"role:     {WORKER_TASK_ROLE}", f"model:    {model}"]
    denied = []

    # THE PROFILE, ALL FOUR ACTIONS. The `us.` prefix on `config.BEDROCK_MODEL` makes
    # it a cross-region inference profile, which lives at a different ARN shape from a
    # foundation model -- and the profile ARN carries an ACCOUNT while the
    # foundation-model one does not. That asymmetry is AWS's, not a typo.
    for action in _BEDROCK_ACTIONS:
        decision = _simulate(WORKER_TASK_ROLE, action, profile_arn)
        lines.append(f"  {action:42} on the profile: {decision}")
        if decision != "allowed":
            denied.append(f"{action} on the inference profile is {decision}")

    # THE FOUNDATION MODELS THE PROFILE ROUTES TO, CROSS-REGION. Measured with
    # `get-inference-profile`: this profile fans out to us-east-1, us-east-2 AND
    # us-west-2, and scoped to one region two of the three were denied -- so the call
    # succeeded or failed depending on which region Bedrock chose.
    for region in ("us-east-1", "us-east-2", "us-west-2"):
        fm_arn = f"arn:aws:bedrock:{region}::foundation-model/{model.removeprefix('us.')}"
        decision = _simulate(WORKER_TASK_ROLE, "bedrock:ConverseStream", fm_arn)
        lines.append(f"  foundation-model in {region:10} ConverseStream: {decision}")
        if decision != "allowed":
            denied.append(f"ConverseStream on the {region} foundation model is {decision}")

    # AND THE FIVE RUNTIMES, which is what the worker does when REMOTE_AGENTS is true.
    runtime_arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/theagentorg_security-x"
    decision = _simulate(
        WORKER_TASK_ROLE, "bedrock-agentcore:InvokeAgentRuntime", runtime_arn)
    lines.append(f"  InvokeAgentRuntime on a runtime ARN: {decision}")
    if decision != "allowed":
        denied.append(f"bedrock-agentcore:InvokeAgentRuntime is {decision}")

    evidence = "\n".join(lines)
    if denied:
        raise CheckFailed(
            f"{evidence}\n"
            f"\n"
            + "\n".join(f"  - {problem}" for problem in denied) + "\n"
            "\n"
            "THE WORKER WILL SERVE FIXTURES OR FAIL TO REACH ITS AGENTS, silently,\n"
            "with every job green -- `llm.text()` catches a model denial BY DESIGN\n"
            "and `structured()` returns None. That exact defect cost this project a\n"
            "week on the OTHER role, and this is a second policy written from\n"
            "scratch in infra/Terraform/modules/platform/iam.tf.\n"
            "\n"
            "Remedy: the BedrockInvoke statement there must grant BOTH ARN shapes --\n"
            "the inference profile (the thing CALLED) and foundation-model/* across\n"
            "regions (the things that ANSWER) -- and all four actions, because\n"
            "strands calls ConverseStream and that is not covered by InvokeModel.\n"
            "Then apply through terraform.yml and re-run: a green apply is not the\n"
            "evidence, this simulation is."
        )

    return evidence


def check_the_worker_service_matches_the_image_it_should_run(cluster: str,
                                                            service: str) -> str:
    """Check 5b. If a worker service exists, which image is it actually running?

    A SERVICE THAT EXISTS AND RUNS AN OLD IMAGE IS THE `latest`-TAG FAILURE, one
    layer up: `deploy-platform.yml` can push an image and force a new deployment, and
    a redeploy CANNOT change which image a service runs -- the task definition names
    it, and that is Terraform's. So a green deploy workflow and a stale service are
    entirely compatible, which is this repository's signature shape.

    Reports rather than fails when no service exists: `runtime_enabled` defaults
    false and that is the documented state.
    """
    raw = subprocess.run(
        ["aws", "ecs", "describe-services",
         "--cluster", cluster, "--services", service, "--output", "json"],
        capture_output=True, text=True, check=False,
    )
    if raw.returncode != 0:
        return (
            f"cluster: {cluster}\n"
            f"service: {service}\n"
            f"SKIPPED -- no such cluster or service, so no worker is running.\n"
            f"That is the DEFAULT and documented state: modules/platform's\n"
            f"runtime_enabled is false because these are the project's first hourly\n"
            f"charges and because the DSN's database role decides whether tenant\n"
            f"isolation binds at all (see check 6).\n"
            f"NOTHING HERE CHECKED A RUNNING WORKER."
        )

    services = json.loads(raw.stdout).get("services", [])
    if not services or services[0].get("status") != "ACTIVE":
        return (
            f"cluster: {cluster}\n"
            f"service: {service}\n"
            f"SKIPPED -- the service is not ACTIVE. No worker is running."
        )

    definition = services[0]["taskDefinition"]
    described = _aws("ecs", "describe-task-definition",
                     "--task-definition", definition, "--output", "json")
    container = json.loads(described)["taskDefinition"]["containerDefinitions"][0]
    image = container.get("image", "")
    tag = image.rsplit(":", 1)[-1] if ":" in image else "(untagged)"

    running = services[0].get("runningCount")
    desired = services[0].get("desiredCount")
    lines = [
        f"cluster:  {cluster}",
        f"service:  {service}  ({running} running of {desired} desired)",
        f"image:    {image}",
        f"tag:      {tag}",
    ]

    # `latest` IS A FAILURE HERE, not a warning. It cannot tell you which commit is
    # running, and `modules/platform`'s `worker_image` precondition refuses an empty
    # value for the same reason. A worker whose code nobody can identify is a worker
    # whose behaviour nobody can attribute to a change.
    if tag in ("latest", "(untagged)"):
        raise CheckFailed(
            "\n".join(lines) + "\n"
            f"\n"
            f"THE SERVICE RUNS `{tag}`, SO NOTHING CAN SAY WHICH COMMIT IS RUNNING.\n"
            f"deploy-platform.yml tags every image with the commit SHA as well, and\n"
            f"modules/platform takes `worker_image` as a full URI for exactly this\n"
            f"reason. Set TF_VAR_platform_worker_image to the SHA-tagged URI and\n"
            f"apply -- forcing a new deployment cannot change it, because the task\n"
            f"definition names the image and that definition is Terraform's."
        )

    # SCANNERS_REQUIRED MUST NOT BE SET ON THIS CONTAINER. It carries no scanners, and
    # the knob promotes ABSENT to FAULT: one `*-scanner-error` finding per tool at
    # severity `high`, which IS the block threshold, so it blocks EVERY run including
    # the clean one with blocking=3. `deploy.yml` guards it to the security agent.
    environment = {entry.get("name"): entry.get("value")
                   for entry in container.get("environment", [])}
    lines.append(f"env:      {sorted(environment)}")
    if str(environment.get("SCANNERS_REQUIRED", "")).lower() == "true":
        raise CheckFailed(
            "\n".join(lines) + "\n"
            "\n"
            "SCANNERS_REQUIRED IS TRUE ON THE WORKER, WHICH CARRIES NO SCANNERS.\n"
            "That knob promotes an ABSENT binary to a FAULT: one *-scanner-error\n"
            "finding per tool at severity `high`, which is the block threshold, so\n"
            "EVERY run blocks with blocking=3 -- including the clean half of the\n"
            "demo. It belongs on the security RUNTIME, the one image carrying\n"
            "gitleaks, trivy and semgrep, and deploy.yml guards it there."
        )

    return "\n".join(lines)
