"""Pins the deploy and terraform workflows' blast radius.

Owner: Sorour (Task 6). These two workflows are the only files in this
repository that can spend money or mutate live AWS infrastructure. They cannot
be proven on the authoring machine -- proving them means pushing to `main` and
letting them run -- so these tests pin the properties whose violation would
either leak a credential or fire a billable deploy by accident.

WHAT THESE WORKFLOWS MAKE WORSE, which is what these tests are actually about
----------------------------------------------------------------------------
Before them, NO workflow here could touch AWS. ci.yml holds `contents: read` and
nothing else, and its test job sets LLM_DISABLED precisely because the runner has
no credentials. deploy.yml and terraform.yml introduce hazards that did not
previously exist anywhere in the repo:

1. `id-token: write` -- the permission that lets a job mint an OIDC token and
   assume an IAM role. Any job holding it can reach account 339712964409.
2. An assumption of `github-actions-role`, which is SHARED with other
   repositories' CI (docs/plan/week1-verification-log.md:21-30). Editing that
   role from here would break someone else's pipeline.
3. `workflow_dispatch` -- once these files are on the default branch, one click
   is a real deploy.
4. Billable CREATEs: images pushed to five ECR repositories and five AgentCore
   runtimes configured and launched.

So these tests are not "is the YAML valid". They are: does the credential surface
stay closed, does `id-token: write` stay off ci.yml and off any workflow's top
level, is `terraform apply` gated to main, and does no step edit the shared role.

WHAT THESE TESTS DO NOT AND CANNOT CLAIM
----------------------------------------
They do NOT claim the deploy works. As of this commit the OIDC role assumption
FAILS: GitHub issues an immutable subject claim carrying numeric IDs
(`repo:mohamedsorour1998@<id>/TheAgentOrg@<id>:ref:refs/heads/main`) which the
role's `repo:mohamedsorour1998/TheAgentOrg:*` trust condition cannot match. That
needs a trust-policy addition on a role shared with other repositories, so it is
escalated, not worked around. Every test here asserts STRUCTURE. A test implying
these workflows currently run green would be a false claim; terraform.yml's own
comment block documents the same diagnosis.

WHY THESE PARSE THE YAML INSTEAD OF GREPPING IT
-----------------------------------------------
A substring assertion over a whole workflow file can be satisfied by that file's
own comments, and these files are heavily commented. Two measured examples:

  * `id-token: write` appears on 3 lines of deploy.yml, only TWO of which are
    the actual permission -- the rest is prose. So `"id-token: write" in text`
    stays green after a permission is deleted from a job.
  * deploy.yml:7-8 reads "do not add an AWS_ACCESS_KEY_ID secret". A test
    asserting that string is ABSENT from the file therefore fails on the comment
    forbidding it -- the inverse defect, a test that can only ever fail.

Task 5 hit exactly this trap: three mutations escaped tests that matched prose,
and the fix was to parse the file as data. So everything here loads the document
and asserts on the parsed structure -- which is why the credential tests read
each step's `with:` inputs and each `env:` mapping rather than the file text. The
one test that reads raw text
(test_no_workflow_contains_a_key_shaped_string_outside_the_aws_example) says why
it must, and cannot be satisfied by prose: it matches a key SHAPE that no comment
here contains.

THE `on:` TRAP: YAML 1.1 resolves the unquoted key `on` to the BOOLEAN True, so
`doc["on"]` raises KeyError and `"on" in doc` is False. GitHub Actions does not
read it that way. `_triggers()` goes through True, and
test_the_on_key_is_the_yaml_boolean_trap_not_the_string pins the trap itself so
nobody "fixes" the accessor into something that silently reads nothing.

NO LIVE AWS. Nothing here runs aws, terraform, docker, agentcore or git push. The
only subprocesses are actionlint and shellcheck over local files.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DEPLOY = WORKFLOWS / "deploy.yml"
TERRAFORM = WORKFLOWS / "terraform.yml"
CI = WORKFLOWS / "ci.yml"

# Every workflow in the repository, so a newly added one is covered by the
# repo-wide credential tests without anyone remembering to list it here.
ALL_WORKFLOWS = sorted(WORKFLOWS.glob("*.yml"))

# The workflows allowed to reach AWS at all. A new workflow assuming a role must
# be added here deliberately, which is the point.
AWS_WORKFLOW_NAMES = {"deploy.yml", "terraform.yml"}

# Read from docs/plan/week1-verification-log.md:11-30. Never recalled, never
# re-derived from live AWS state -- Task 6 is forbidden from calling AWS at all.
RECORDED_ACCOUNT = "339712964409"
RECORDED_REGION = "us-east-1"
RECORDED_OIDC_ROLE_ARN = "arn:aws:iam::339712964409:role/github-actions-role"
RECORDED_RUNTIME_ROLE_ARN = (
    "arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role"
)
RECORDED_ECR_PREFIX = "theagentorg-shared"

# The five agents. Runtime names use UNDERSCORES (theagentorg_planner); the ECR
# repositories use HYPHENS (theagentorg-shared-planner-agent). Two namespaces,
# and conflating them is a real failure mode -- see
# test_the_two_naming_namespaces_are_not_conflated.
AGENTS = ["planner", "developer", "reviewer", "security", "sre"]

# The credential inputs of aws-actions/configure-aws-credentials that take a
# long-lived key. The entire point of Task 6 is that none of these ever appears.
STATIC_KEY_INPUTS = (
    "aws-access-key-id",
    "aws-secret-access-key",
    "aws-session-token",
)
KEY_ENV_NAMES = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


def _doc(path):
    """Parse a workflow to a dict. safe_load, so no tag can execute anything."""
    return yaml.safe_load(path.read_text())


def _triggers(path):
    """The parsed `on:` mapping.

    Keyed on the BOOLEAN True, not the string "on": YAML 1.1 resolves the
    unquoted key `on` to a bool, so path["on"] raises KeyError. Asserting the
    key's presence here means a future rename cannot make this return an empty
    dict and take every trigger test green with it.
    """
    doc = _doc(path)
    assert True in doc, f"{path.name} has no `on:` block (YAML 1.1 bool key)"
    return doc[True]


def _jobs(path):
    doc = _doc(path)
    assert "jobs" in doc, f"{path.name} has no `jobs:` block"
    return doc["jobs"]


def _job(path, name):
    jobs = _jobs(path)
    assert name in jobs, f"{path.name} jobs are {sorted(jobs)}, expected {name!r}"
    return jobs[name]


def _steps(job):
    steps = job.get("steps")
    assert steps, "job has no steps"
    return steps


def _run_scripts(job):
    """Every `run:` body in one job, as a list of strings."""
    return [s["run"] for s in _steps(job) if "run" in s]


def _all_run_scripts(path):
    """Every `run:` body in every job of a workflow."""
    out = []
    for job in _jobs(path).values():
        out.extend(s["run"] for s in (job.get("steps") or []) if "run" in s)
    return out


def _resolve_env(path, text):
    """Substitute ${{ env.NAME }} from a workflow's top-level env block.

    GitHub does this before anything runs, so tests comparing ARNs have to do it
    too -- otherwise they can only assert on the unexpanded template and would go
    green precisely when someone stopped using the env block. An expression
    naming a key the env block does not define is left untouched, so it fails the
    comparison loudly instead of silently resolving to an empty string.
    """
    env = _doc(path).get("env") or {}
    return re.sub(
        r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
        lambda m: str(env[m.group(1)]) if m.group(1) in env else m.group(0),
        text,
    )


def _credential_steps(path):
    """(job_name, step) for every configure-aws-credentials step in a workflow."""
    found = []
    for job_name, job in _jobs(path).items():
        for step in job.get("steps") or []:
            if "configure-aws-credentials" in str(step.get("uses", "")):
                found.append((job_name, step))
    return found


# --------------------------------------------------------------------------
# The files parse at all. Everything below depends on this, so it fails first.
# --------------------------------------------------------------------------


def test_the_deploy_and_terraform_workflows_exist():
    assert DEPLOY.is_file(), f"{DEPLOY} is missing"
    assert TERRAFORM.is_file(), f"{TERRAFORM} is missing"


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_is_parseable_yaml_with_a_name_and_jobs(path):
    doc = _doc(path)
    assert isinstance(doc, dict), f"{path.name} parsed as {type(doc).__name__}"
    assert doc.get("name"), f"{path.name} has no top-level name"
    assert doc.get("jobs"), f"{path.name} has no jobs"


def test_the_on_key_is_the_yaml_boolean_trap_not_the_string():
    """Pins the trap, so nobody rewrites _triggers() into a silent no-op.

    If a future PyYAML or a quoted `"on":` key changes this, the accessor above
    must change too -- and this test is what says so out loud rather than letting
    every trigger assertion below quietly stop reading anything.
    """
    doc = _doc(DEPLOY)
    assert True in doc, "`on:` no longer parses as the boolean True"
    assert "on" not in doc, "`on:` now parses as the string 'on'; update _triggers()"


# --------------------------------------------------------------------------
# HAZARD 1 -- no static AWS credentials, in ANY workflow. The point of the task.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_no_step_in_any_workflow_passes_a_static_aws_key(path):
    """Structural, not textual: reads each step's `with:` inputs as data.

    A grep for "aws-access-key-id" would be satisfied by deploy.yml's own header
    comment forbidding it, which is the inverse defect -- a test that can only
    ever fail.
    """
    for job_name, job in _jobs(path).items():
        for step in job.get("steps") or []:
            with_inputs = step.get("with") or {}
            for forbidden in STATIC_KEY_INPUTS:
                assert forbidden not in with_inputs, (
                    f"{path.name} job {job_name} step "
                    f"{step.get('name') or step.get('uses')!r} passes {forbidden}; "
                    "every AWS step must assume the role via OIDC"
                )


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_reads_an_aws_key_from_env_or_a_secret(path):
    """The other half: a key can arrive through env or a secrets expression.

    Checks the workflow-level env, every job env, every step env, and every run
    body for the names a static-key setup would use.
    """
    doc = _doc(path)
    scopes = [("workflow env", doc.get("env") or {})]
    for job_name, job in _jobs(path).items():
        scopes.append((f"job {job_name} env", job.get("env") or {}))
        for step in job.get("steps") or []:
            label = f"job {job_name} step {step.get('name') or step.get('uses')!r} env"
            scopes.append((label, step.get("env") or {}))

    for label, mapping in scopes:
        for name in KEY_ENV_NAMES:
            assert name not in mapping, f"{path.name} {label} defines {name}; OIDC only"

    for script in _all_run_scripts(path):
        for name in KEY_ENV_NAMES:
            assert name not in script, f"{path.name} run body references {name}; OIDC only"
        assert "secrets.AWS" not in script, (
            f"{path.name} run body reads an AWS secret; OIDC needs none"
        )


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_contains_a_key_shaped_string_outside_the_aws_example(path):
    """RAW TEXT on purpose -- a leaked key in a COMMENT is still a leaked key.

    This is the one property where parsed structure is the wrong altitude:
    comments do not survive parsing, and a pasted key would sit in one. The regex
    is AWS's access-key-id shape; AKIAIOSFODNN7EXAMPLE is AWS's own published
    example and is the only permitted match.
    """
    found = set(re.findall(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", path.read_text()))
    assert found <= {"AKIAIOSFODNN7EXAMPLE"}, f"key-shaped strings in {path.name}: {sorted(found)}"


def test_ci_yml_never_gains_an_aws_credential_step():
    """ci.yml runs on every pull_request. It must stay credential-free.

    deploy.yml must not have been made to work by loosening the workflow that
    untrusted PR code can reach.
    """
    assert not _credential_steps(CI), "ci.yml now assumes an AWS role; it is PR-triggered"
    assert _doc(CI).get("permissions") == {"contents": "read"}, (
        f"ci.yml top-level permissions changed to {_doc(CI).get('permissions')!r}"
    )


# --------------------------------------------------------------------------
# HAZARD 2 -- id-token: write. Narrowest scope, never at top level, never on ci.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_id_token_write_is_never_granted_at_a_workflow_top_level(path):
    """Scope it to the jobs that need it.

    A top-level grant hands token-minting to every job anyone later adds to the
    file, which is how a lint job ends up able to assume a shared deploy role.
    """
    top = _doc(path).get("permissions") or {}
    assert top.get("id-token") != "write", (
        f"{path.name} grants id-token: write at the top level; move it to the job that needs it"
    )


def test_ci_yml_never_gains_id_token_write():
    """Checked at top level AND per job.

    ci.yml runs on every pull_request, so granting it id-token: write would let
    PR-triggered code mint a token for the shared role.
    """
    top = _doc(CI).get("permissions") or {}
    assert "id-token" not in top, f"ci.yml top-level permissions gained id-token: {top!r}"
    for job_name, job in _jobs(CI).items():
        perms = job.get("permissions") or {}
        assert "id-token" not in perms, f"ci.yml job {job_name} gained id-token: {perms!r}"


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_only_jobs_that_assume_a_role_hold_id_token_write(path):
    """The converse of the tests above, and the one that catches drift.

    A job holding id-token: write without a credential step is an unused
    capability on a shared role -- and a job with a credential step but no
    id-token: write simply cannot authenticate. Both are defects; this asserts
    the two sets match exactly.
    """
    with_token = {
        name
        for name, job in _jobs(path).items()
        if (job.get("permissions") or {}).get("id-token") == "write"
    }
    with_creds = {name for name, _ in _credential_steps(path)}
    assert with_token == with_creds, (
        f"{path.name}: jobs with id-token: write are {sorted(with_token)}, "
        f"jobs assuming a role are {sorted(with_creds)}; these must match"
    )


def test_every_workflow_that_reaches_aws_is_one_we_expect():
    """A new AWS-touching workflow should be a deliberate decision, not a surprise."""
    reaching = {p.name for p in ALL_WORKFLOWS if _credential_steps(p)}
    assert reaching == AWS_WORKFLOW_NAMES, (
        f"workflows assuming an AWS role are {sorted(reaching)}, "
        f"expected {sorted(AWS_WORKFLOW_NAMES)}"
    )


# --------------------------------------------------------------------------
# HAZARD 3 -- the triggers. Every widening costs money or risks a mutation.
# --------------------------------------------------------------------------


def test_the_deploy_trigger_is_a_filtered_push_to_main_plus_manual_dispatch():
    """Compared as data, and by set equality rather than containment.

    Containment would let someone add `pull_request:` -- which would deploy from
    any PR, including a fork's -- while staying green.
    """
    triggers = _triggers(DEPLOY)
    assert set(triggers) == {"push", "workflow_dispatch"}, (
        f"deploy.yml triggers are {sorted(str(k) for k in triggers)}"
    )
    assert triggers["push"].get("branches") == ["main"], (
        f"deploy push branches is {triggers['push'].get('branches')!r}; "
        "a wider filter deploys from feature branches"
    )
    paths = triggers["push"].get("paths")
    assert paths, "deploy.yml push has no paths filter; every push to main would redeploy"
    assert "agentorg/**" in paths or "agentorg/agents/**" in paths, (
        f"deploy paths {paths!r} does not cover the agent sources"
    )


@pytest.mark.parametrize("path", [DEPLOY, TERRAFORM], ids=["deploy", "terraform"])
def test_no_aws_workflow_fires_on_an_untrusted_or_unattended_event(path):
    """pull_request_target runs with repository secrets against a fork's code.

    `schedule` would deploy unattended. deploy.yml must not fire on any
    pull_request event at all; terraform.yml deliberately does, which the
    dedicated test below covers by requiring apply to be gated.
    """
    triggers = _triggers(path)
    for event in ("pull_request_target", "schedule", "repository_dispatch"):
        assert event not in triggers, (
            f"{path.name} fires on {event}; that is unattended or untrusted"
        )


def test_the_deploy_workflow_does_not_fire_on_pull_requests_at_all():
    """A deploy is billable and hard to reverse; a plan is neither."""
    assert "pull_request" not in _triggers(DEPLOY), (
        "deploy.yml fires on pull_request; that deploys from unmerged code"
    )


def test_terraform_apply_is_gated_to_main_and_never_runs_on_a_pull_request():
    """terraform.yml DOES run on pull_request, which is correct for plan.

    The whole safety of that choice rests on `apply` carrying an `if:` that
    excludes PR events. Without it, opening a PR would mutate live
    infrastructure. This asserts the guard names both conditions.
    """
    guard = _job(TERRAFORM, "apply").get("if")
    assert guard, "terraform.yml apply has no `if:` guard; a PR could apply"
    assert "refs/heads/main" in guard, f"apply guard does not pin main: {guard!r}"
    assert "pull_request" in guard, f"apply guard does not exclude pull_request: {guard!r}"


def test_the_deploy_workflow_serialises_concurrent_runs_without_cancelling():
    """Two deploys racing would push the same tags and configure the same runtimes.

    Cancelling mid-deploy is worse than queueing: it can leave images pushed and
    runtimes half-configured. So cancel-in-progress must be false, not absent.
    """
    concurrency = _doc(DEPLOY).get("concurrency")
    assert concurrency, "deploy.yml has no concurrency group; two deploys could race"
    assert concurrency.get("cancel-in-progress") is False, (
        f"cancel-in-progress is {concurrency.get('cancel-in-progress')!r}; "
        "a cancelled deploy can leave runtimes half-configured"
    )


# --------------------------------------------------------------------------
# HAZARD 4 -- the shared role is consumed, never modified.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", [DEPLOY, TERRAFORM], ids=["deploy", "terraform"])
def test_every_credential_step_assumes_the_recorded_role_by_arn(path):
    """Resolves ${{ env.* }} the way GitHub would, then compares whole ARNs.

    These workflows write `role-to-assume: ${{ env.AWS_ROLE }}`, so asserting the
    literal account appears in that string would be asserting a property the file
    never had -- it would only pass if someone inlined the ARN and stopped using
    the env block. Resolving first means this tracks what actually reaches AWS.
    """
    steps = _credential_steps(path)
    assert steps, f"{path.name} has no configure-aws-credentials step"
    for job_name, step in steps:
        assert str(step["uses"]).endswith("@v4"), (
            f"{path.name} job {job_name} action is not pinned: {step['uses']!r}"
        )
        with_inputs = step.get("with") or {}
        role = _resolve_env(path, with_inputs.get("role-to-assume", ""))
        assert role == RECORDED_OIDC_ROLE_ARN, (
            f"{path.name} job {job_name} role-to-assume resolves to {role!r}, "
            f"recorded is {RECORDED_OIDC_ROLE_ARN!r}"
        )
        region = _resolve_env(path, with_inputs.get("aws-region", ""))
        assert region == RECORDED_REGION, f"{path.name} job {job_name} region is {region!r}"


@pytest.mark.parametrize("path", [DEPLOY, TERRAFORM], ids=["deploy", "terraform"])
@pytest.mark.parametrize(
    "mutating",
    [
        "aws iam create",
        "aws iam put-role-policy",
        "aws iam attach-role-policy",
        "aws iam update-assume-role-policy",
        "aws iam delete",
        "aws iam detach-role-policy",
    ],
)
def test_no_step_mutates_the_shared_role(path, mutating):
    """`github-actions-role` is shared with other repositories' CI.

    Editing its trust policy or attached policies from here would break a
    pipeline nobody in this repo can see. Both workflows consume it only.
    """
    for script in _all_run_scripts(path):
        assert mutating not in script, (
            f"{path.name} run body invokes {mutating!r}; the shared role is consume-only"
        )


def test_the_deploy_workflow_runs_no_terraform_at_all():
    """Separation of concerns, and a blast-radius boundary.

    terraform.yml owns infrastructure and gates apply behind a main-only `if:`.
    A `terraform apply` smuggled into deploy.yml would inherit deploy's trigger
    instead, bypassing that gate entirely.
    """
    for script in _all_run_scripts(DEPLOY):
        assert "terraform " not in script, (
            "deploy.yml runs terraform; apply's main-only gate lives in terraform.yml"
        )


def test_the_runtime_role_passed_to_agentcore_is_the_recorded_arn():
    """A wrong execution role either fails or grants the runtime someone else's."""
    # The runtime is created through bedrock-agentcore-control, not the agentcore
    # CLI: the CLI overwrites agentorg/agents/Dockerfile and rebuilds in
    # CodeBuild, which died on a Docker Hub 429 three runs in a row. The API
    # takes our own ECR image, so the flag carrying the role is --role-arn.
    configure = [s for s in _all_run_scripts(DEPLOY) if "create-agent-runtime" in s]
    assert configure, "no create-agent-runtime step in deploy.yml"
    for script in configure:
        resolved = _resolve_env(DEPLOY, script)
        match = re.search(r"--role-arn\s+\"?([^\s\"\\]+)", resolved)
        assert match, f"no --role-arn in the runtime-creating step: {resolved!r}"
        assert match.group(1) == RECORDED_RUNTIME_ROLE_ARN, (
            f"--execution-role is {match.group(1)!r}, recorded is {RECORDED_RUNTIME_ROLE_ARN!r}"
        )


# --------------------------------------------------------------------------
# The deploy being able to work at all: the five agents, the image, the deps.
# --------------------------------------------------------------------------


def test_every_agent_loop_in_the_deploy_workflow_covers_all_five_agents():
    """EVERY loop, not just the first one. Measured: deploy.yml has four.

    They build the tag list, verify the pushed images, configure-and-launch, and
    check status. A mutation dropping `sre` from any ONE of them leaves the others
    intact -- so `re.search` (first match only) reported that mutation as caught
    when it was not. This uses re.findall and asserts on all four, which is what
    makes the test able to fail wherever the drift happens.

    Both directions matter: every agent this repo has must be deployed, and no
    loop may name a sixth that does not exist.
    """
    scripts = "\n".join(_all_run_scripts(DEPLOY))
    loops = re.findall(r"for agent in ([a-z ]+); do", scripts)
    assert loops, "no agent loop found in deploy.yml run bodies"
    for i, loop in enumerate(loops):
        assert loop.split() == AGENTS, (
            f"deploy.yml agent loop {i} covers {loop.split()}, expected {AGENTS}"
        )
    # A loop that stops being reached is as bad as one that lost an agent, so pin
    # the count too -- re-measured from this file, not carried forward.
    # Re-measured, not carried forward. Three loops: the tag list, the
    # pushed-image verification, and the runtime create/update. The runtime-status
    # wait polls the whole set in one query, and the endpoint readiness check is
    # now a retrying invoke of one agent rather than a per-agent poll, so neither
    # adds a loop.
    assert len(loops) == 3, f"expected 3 agent loops in deploy.yml, found {len(loops)}"


@pytest.mark.parametrize("agent", AGENTS)
def test_every_deployed_agent_has_a_module_the_server_can_dispatch_to(agent):
    """The containers serve agentorg/agents/server.py, which selects by AGENT_ROLE.

    A runtime deployed for an agent the server cannot dispatch would start and
    then fail every invocation -- READY, and useless.
    """
    module = REPO_ROOT / "agentorg" / "agents" / f"{agent}.py"
    assert module.is_file(), f"{module} does not exist but deploy.yml deploys it"
    server = (REPO_ROOT / "agentorg" / "agents" / "server.py").read_text()
    assert f'"{agent}"' in server, f"server.py has no AGENT_ROLE entry for {agent}"


def test_the_server_entrypoint_exists_and_requires_an_explicit_agent_role():
    """The gap Task 5 reported is closed, and closed the safe way.

    The five agents were library functions with no `__main__`, so
    `agentcore configure` had nothing to invoke. server.py is that entrypoint.
    AGENT_ROLE must RAISE when unset rather than defaulting: a container that
    silently defaulted to one agent would serve the wrong agent's results under
    another's runtime name, which is indistinguishable from a correct deploy
    until someone reads the output.
    """
    server = REPO_ROOT / "agentorg" / "agents" / "server.py"
    assert server.is_file(), (
        "agentorg/agents/server.py is missing; the containers have no entrypoint"
    )
    text = server.read_text()
    assert "AGENT_ROLE" in text, "server.py does not read AGENT_ROLE"
    assert "/invocations" in text and "/ping" in text, (
        "server.py does not serve the AgentCore HTTP contract (/invocations, /ping)"
    )


def test_every_agent_gets_its_role_from_the_environment_not_from_the_image():
    """One image, five tags, five AGENT_ROLE values.

    If configure stopped passing AGENT_ROLE, all five runtimes would run whatever
    the image defaults to -- five runtimes serving one agent, all reporting READY.
    """
    configure = "\n".join(s for s in _all_run_scripts(DEPLOY) if "create-agent-runtime" in s)
    assert "AGENT_ROLE=" in configure, (
        "the runtime-creating step does not pass AGENT_ROLE; all five runtimes "
        "would serve whatever agent the image defaults to, all reporting READY"
    )
    assert "--environment-variables" in configure, (
        "AGENT_ROLE must travel as --environment-variables on the runtime API"
    )


def test_the_dockerfile_the_workflow_builds_actually_exists():
    """A --file pointing at a moved Dockerfile fails after OIDC and ECR login.

    This repo has TWO Dockerfiles (agentorg/agents/ and infra/agentcore/), so the
    path the workflow names is a real choice and not a formality.
    """
    scripts = "\n".join(_all_run_scripts(DEPLOY))
    match = re.search(r"--file\s+(\S+)", scripts)
    assert match, "the build step names no --file"
    dockerfile = REPO_ROOT / match.group(1)
    assert dockerfile.is_file(), f"--file {match.group(1)} does not exist at {dockerfile}"


def test_the_image_is_built_for_arm64_because_agentcore_runs_arm64():
    """The runner is amd64.

    Without an explicit platform the image builds, pushes and deploys, then dies
    at startup with an exec format error -- a failure that appears only after
    everything expensive has already succeeded.
    """
    scripts = "\n".join(_all_run_scripts(DEPLOY))
    assert "linux/arm64" in scripts, "the build does not target linux/arm64"
    uses = [
        str(s.get("uses", ""))
        for job in _jobs(DEPLOY).values()
        for s in (job.get("steps") or [])
    ]
    assert any("setup-qemu-action" in u for u in uses), (
        "no QEMU setup step; a cross-platform arm64 build on an amd64 runner needs it"
    )


def test_the_requirements_file_the_image_installs_exists_and_is_pinned():
    """That file, NOT pyproject.toml, is the pinned source of truth for the image.

    An unpinned line would let an image built in September contain different code
    from the one demonstrated in August, which is the whole reason it is pinned.
    """
    requirements = REPO_ROOT / "agentorg" / "agents" / "requirements.txt"
    assert requirements.is_file(), f"{requirements} is missing; the image build would fail"

    dockerfile = REPO_ROOT / "agentorg" / "agents" / "Dockerfile"
    assert "agentorg/agents/requirements.txt" in dockerfile.read_text(), (
        "the Dockerfile does not install agentorg/agents/requirements.txt"
    )

    lines = [
        line.strip()
        for line in requirements.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines, "requirements.txt declares nothing"
    unpinned = [line for line in lines if "==" not in line]
    assert not unpinned, f"unpinned requirement lines: {unpinned}"


def test_the_two_naming_namespaces_are_not_conflated():
    """Runtime names use underscores; ECR repositories use hyphens.

    theagentorg_planner is a runtime; theagentorg-shared-planner-agent is a
    repository. Getting one where the other belongs fails late and reads as an
    AWS problem rather than a naming problem.
    """
    scripts = "\n".join(_all_run_scripts(DEPLOY))
    assert "theagentorg_${agent}" in scripts, "runtime names are not built with an underscore"
    env = _doc(DEPLOY).get("env") or {}
    assert env.get("ECR_PREFIX") == RECORDED_ECR_PREFIX, (
        f"ECR_PREFIX is {env.get('ECR_PREFIX')!r}, recorded is {RECORDED_ECR_PREFIX!r}"
    )
    assert "theagentorg_shared" not in scripts, (
        "an ECR repository name was written with underscores"
    )


def test_images_are_tagged_with_the_commit_sha_not_only_latest():
    """`latest` cannot tell you which commit is running.

    Asserting `"github.sha" in scripts` was too weak to fail: deploy.yml
    references github.sha in five places, so deleting it from the BUILD step's
    tag list left the test green while every image became :latest only. This
    asserts on the tag list specifically, and on the runtime being pinned to the
    SHA tag rather than to latest -- a redeploy must be reproducible instead of
    resolving to whatever `latest` happened to be.
    """
    build_steps = [s for s in _run_scripts(_job(DEPLOY, "build")) if "--tag" in s]
    assert build_steps, "no build step assembles image tags"
    for script in build_steps:
        tag_lines = [line for line in script.splitlines() if "--tag" in line]
        assert any("github.sha" in line or "${sha}" in line for line in tag_lines), (
            f"no image tag carries the commit SHA: {tag_lines}"
        )

    # The runtime API takes the image URI directly, so the pin lives in the
    # containerUri the create/update step builds rather than in a CLI --image-tag.
    #
    # THIS ASSERTS ON THE URI LINE, NOT ON THE WHOLE SCRIPT. The first version
    # searched the entire step for "github.sha" and passed against a mutation
    # that pinned the image to :latest -- because the SHA also appears in the
    # runtime's --description two lines below. A test that any nearby mention
    # satisfies is not a test of the pin.
    launch = "\n".join(s for s in _all_run_scripts(DEPLOY) if "create-agent-runtime" in s)
    uri_lines = [line for line in launch.splitlines() if "uri=" in line]
    assert uri_lines, f"no containerUri assembled in the runtime step: {launch!r}"
    for line in uri_lines:
        assert "${{ github.sha }}" in line or "${sha}" in line, (
            f"the image URI is not pinned to the commit; it would deploy "
            f"whatever :latest resolves to: {line.strip()!r}"
        )


def test_the_env_block_holds_the_recorded_identifiers():
    """Everything downstream interpolates these, so a typo here is a typo in
    every ARN and registry path the workflow builds.
    """
    env = _doc(DEPLOY).get("env") or {}
    assert env.get("AWS_REGION") == RECORDED_REGION, f"AWS_REGION is {env.get('AWS_REGION')!r}"
    assert env.get("AWS_ROLE") == RECORDED_OIDC_ROLE_ARN, f"AWS_ROLE is {env.get('AWS_ROLE')!r}"
    assert env.get("RUNTIME_ROLE") == RECORDED_RUNTIME_ROLE_ARN, (
        f"RUNTIME_ROLE is {env.get('RUNTIME_ROLE')!r}"
    )


# --------------------------------------------------------------------------
# Failures must be reported, not swallowed. The expensive defect class.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", [DEPLOY, TERRAFORM], ids=["deploy", "terraform"])
def test_every_run_body_containing_a_pipe_sets_pipefail(path):
    """Scoped to pipes, because that is the gap the runner does NOT close for you.

    Measured on this machine rather than assumed:

      * GitHub's default shell is `bash -e {0}`, so a bare failing command
        already fails the step. Probe: a script whose middle command is `false`
        exits 1 under `bash -e` and 0 under plain `bash`.
      * `-e` does NOT imply pipefail. Probe: `false | cat` exits 0 under
        `bash -e`, and 1 only with `set -o pipefail`.

    So a multi-line body without `set -euo pipefail` is a style inconsistency,
    while a body that PIPES without pipefail silently discards the exit status of
    every command but the last. That is where the property is load-bearing, so
    that is what this asserts -- requiring `set -euo pipefail` on every
    multi-line body instead would have flagged deploy.yml's preflight step,
    which contains no pipe at all and where pipefail therefore changes nothing.
    """
    for job_name, job in _jobs(path).items():
        for step in _steps(job):
            script = step.get("run")
            if not script or "|" not in script:
                continue
            # `||` is not a pipe, and `<<<"$x"` is a herestring, not a pipeline.
            piped = [
                line
                for line in script.splitlines()
                if re.search(r"(?<!\|)\|(?!\|)", line) and not line.strip().startswith("#")
            ]
            if not piped:
                continue
            assert "set -o pipefail" in script or "set -euo pipefail" in script, (
                f"{path.name} job {job_name} step {step.get('name')!r} pipes without "
                f"pipefail, so a failure in {piped[0].strip()!r} would be discarded"
            )


def test_no_configure_or_launch_step_swallows_its_failure():
    """`|| true` on a configure or launch is the one outcome the task forbids:
    reporting a deploy that did not happen.

    Narrowly scoped to the commands that MUTATE. `agentcore status` legitimately
    uses `|| true` -- it captures output from a command expected to fail while a
    runtime is absent, then greps for READY and sets failed=1, so the failure is
    still reported. Asserting no `|| true` anywhere would flag that correct usage;
    scoping it to mutating commands catches the defect that actually matters.
    """
    mutating_commands = ("agentcore configure", "agentcore launch", "docker buildx", "aws ecr put")
    for script in _all_run_scripts(DEPLOY):
        for line in script.splitlines():
            if "|| true" not in line:
                continue
            for mutating in mutating_commands:
                assert mutating not in line, (
                    f"a mutating command swallows its failure: {line.strip()!r}"
                )


def test_the_status_check_fails_the_job_when_a_runtime_is_not_ready():
    """The `|| true` above only stays safe because the loop reports separately.

    If the final `[ "$failed" = "0" ]` were dropped, the step would print
    ::error:: lines and still exit 0 -- an honest-looking log on a green run,
    which is worse than a red one.
    """
    # The wait polls list-agent-runtimes until all five report READY. It asserts
    # by exiting non-zero on timeout and on any terminal state, rather than by a
    # failure flag -- the same property, expressed by the loop's own exits, and
    # both are pinned here so a rewrite cannot drop either one silently.
    # Keyed on the polling loop, not merely on the words: the invoke step also
    # calls list-agent-runtimes and also mentions READY in a comment, and a
    # matcher loose enough to catch it reported this assertion as unsatisfied
    # against a correct workflow.
    # Keyed on the runtime-status poll specifically. Two polling loops exist now
    # -- one on runtime status, one on endpoint liveVersion -- and both call
    # list-agent-runtimes inside a `for attempt in` loop, so a matcher that
    # catches either reported a correct workflow as broken.
    status = [
        s for s in _all_run_scripts(DEPLOY)
        if "for attempt in" in s and "agentRuntimeName,status" in s
    ]
    assert status, "no status wait; a deploy could report success with nothing READY"
    for script in status:
        assert 'ready" = "5"' in script or "ready\" = \"5\"" in script, (
            "the wait never requires all five runtimes to be READY"
        )
        assert "CREATE_FAILED" in script, (
            "the wait does not fail fast on a terminal state; it would poll out "
            "the whole timeout on a runtime that can never become READY"
        )
        assert "exit 1" in script, (
            "the wait never exits non-zero; it would report success on timeout"
        )


def test_the_smoke_invoke_asserts_on_response_content_not_just_exit_status():
    """A runtime returning 200 with an empty body exits 0.

    "The call succeeded" is exactly the reassuring non-answer this project keeps
    having to distinguish from a real completion.

    Two escapes drove the current shape. `any("tasks" in s ...)` matched the word
    inside the step's own ::error:: message ("planner returned no tasks"), so
    replacing the real `grep -q '"tasks"'` with `true` stayed green -- and so did
    deleting the invoke step outright, because the assertion only ran `if invoke`.
    Now the presence of an invoke is asserted unconditionally, and the content
    check must be a real grep against the captured output.
    """
    steps = [
        step
        for step in _steps(_job(DEPLOY, "deploy"))
        # `invoke-agent-runtime`, not "agentcore invoke": the deploy calls the
        # runtime API directly rather than the agentcore CLI, and this matcher
        # silently matched nothing after that change -- so every assertion below
        # it stopped running while the test stayed green. A matcher that can
        # match nothing has to be asserted on, which is what the next line does.
        if "invoke-agent-runtime" in (step.get("run") or "")
    ]
    assert steps, "nothing invokes a runtime; READY is not the same as working"

    for step in steps:
        # A step disabled by `if: false` -- or by any condition -- is not a smoke
        # test. Deleting the invoke by neutralising it must fail this, which an
        # earlier version missed because it only looked at run-body text.
        assert "if" not in step, (
            f"the invoke step is conditional ({step.get('if')!r}); "
            "a smoke test that can skip itself proves nothing"
        )
        assert re.search(r"grep\s+-q[a-z]*\s+'\"tasks\"'", step["run"]), (
            "the invoke step does not grep the response for a real result field; "
            "exit status alone cannot distinguish a completion from an empty 200"
        )


def test_the_preflight_job_runs_before_anything_billable_and_needs_no_credentials():
    """It fails the run before any image is built if packaging regressed.

    It must also hold NO id-token: write -- a credential-free check that gained
    one would be a credential surface for no reason.
    """
    preflight = _job(DEPLOY, "preflight")
    perms = preflight.get("permissions") or {}
    assert "id-token" not in perms, f"preflight gained id-token: {perms!r}"

    build = _job(DEPLOY, "build")
    assert "preflight" in (build.get("needs") or []), (
        f"build does not need preflight: {build.get('needs')!r}"
    )
    deploy_job = _job(DEPLOY, "deploy")
    assert "build" in (deploy_job.get("needs") or []), (
        f"deploy does not need build: {deploy_job.get('needs')!r}"
    )


def test_the_preflight_check_imports_from_outside_the_source_tree():
    """`cd /tmp` is load-bearing, not tidiness.

    From the repo root, `import agentorg` finds ./agentorg whether or not the
    install shipped it -- so the check would pass against the broken packaging
    declaration it exists to catch. This is the inverse-defect guard for that
    check: without the cd, it asserts a property it cannot observe.
    """
    scripts = "\n".join(_run_scripts(_job(DEPLOY, "preflight")))
    assert "cd /tmp" in scripts, (
        "the preflight install check does not leave the source tree; "
        "it would pass even with agentorg/agents missing from the wheel"
    )
    assert "pip install --quiet ." in scripts or "pip install ." in scripts, (
        "the preflight check does not do a non-editable install"
    )
    assert "-e ." not in scripts, "the preflight install is editable; that cannot detect the defect"


# --------------------------------------------------------------------------
# House style carried over from ci.yml.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_every_job_in_every_workflow_has_a_sane_timeout(path):
    """The default is 6 hours.

    On workflows that bill for what they create, an unbounded job is the
    expensive kind of hang.
    """
    for name, job in _jobs(path).items():
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"{path.name} job {name} has timeout-minutes={timeout!r}"
        )
        assert timeout <= 90, f"{path.name} job {name} may run {timeout} minutes"


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_major_version_not_a_branch(path):
    """`@main` on a third-party action means its next commit runs here, with
    whatever permissions the job holds -- on deploy.yml, a shared AWS role.
    """
    for job_name, job in _jobs(path).items():
        for step in job.get("steps") or []:
            uses = step.get("uses")
            if not uses:
                continue
            assert "@" in uses, f"{path.name} job {job_name} uses {uses!r} with no version"
            ref = str(uses).rsplit("@", 1)[1]
            assert ref not in ("main", "master", "latest", "HEAD"), (
                f"{path.name} job {job_name} pins {uses!r} to a moving ref"
            )


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_python_is_pinned_to_the_same_version_everywhere(path):
    """A deploy testing on a different Python than CI is a silent difference."""
    for job_name, job in _jobs(path).items():
        for step in job.get("steps") or []:
            if "setup-python" not in str(step.get("uses", "")):
                continue
            version = (step.get("with") or {}).get("python-version")
            assert version == "3.12", (
                f"{path.name} job {job_name} uses python {version!r}; ci.yml uses 3.12"
            )


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_names_an_owner_in_its_header(path):
    """ci.yml's convention: the header block says who owns the file.

    Checks the leading comment block rather than only line 1 -- deploy.yml puts
    its title on line 1 and OWNER on line 2, which is equally readable.
    """
    header = []
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        header.append(line)
    assert any("OWNER:" in line for line in header), (
        f"{path.name} header names no owner: {header[:3]}"
    )


# --------------------------------------------------------------------------
# External validators. Skipped, never faked, when the binary is absent.
# --------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("actionlint") is None, reason="actionlint not on PATH")
@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_actionlint_accepts_every_workflow(path):
    """Keys on returncode, not on parsing actionlint's human output.

    test_actionlint_can_actually_fail below proves this binary reports rather
    than rubber-stamping -- an exit-0-always validator is worse than none.
    """
    result = subprocess.run(
        ["actionlint", str(path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, (
        f"actionlint failed on {path.name}:\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.skipif(shutil.which("actionlint") is None, reason="actionlint not on PATH")
def test_actionlint_can_actually_fail(tmp_path):
    """Proves the validator above is capable of reporting a problem.

    Writes a workflow with an unquoted shell variable -- actionlint runs
    shellcheck over `run:` bodies -- and requires a NON-zero exit. If this went
    green-on-broken, the acceptance test above would prove nothing, which is the
    harness failure mode this repo has hit twice.
    """
    bad = tmp_path / ".github" / "workflows" / "bad.yml"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "name: bad\n"
        "on: push\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: |\n"
        "          if [ $UNQUOTED = x ]; then echo hi; fi\n"
    )
    result = subprocess.run(
        ["actionlint", str(bad)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode != 0, (
        f"actionlint passed a workflow it should reject; it cannot report.\n{result.stdout}"
    )


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not on PATH")
def test_shellcheck_accepts_every_run_body_in_the_aws_workflows(tmp_path):
    """actionlint already runs shellcheck, but only with its own default flags.

    Running it directly, with each body wrapped in the `set -euo pipefail` the
    workflow itself declares, checks the script as it will actually execute.
    GitHub expands ${{ }} before the shell sees it, so those are substituted with
    a plain token first -- shellcheck cannot parse the raw expression syntax.
    """
    checked = 0
    for path in (DEPLOY, TERRAFORM):
        for job_name, job in _jobs(path).items():
            for i, step in enumerate(_steps(job)):
                script = step.get("run")
                if not script:
                    continue
                shell = re.sub(r"\$\{\{[^}]*\}\}", "EXPANDED", script)
                target = tmp_path / f"{path.stem}_{job_name}_{i}.sh"
                header = "" if "set -euo pipefail" in shell else "set -euo pipefail\n"
                target.write_text("#!/usr/bin/env bash\n" + header + shell)
                result = subprocess.run(
                    ["shellcheck", str(target)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert result.returncode == 0, (
                    f"shellcheck rejected {path.name} job {job_name} step {i}:\n{result.stdout}"
                )
                checked += 1
    assert checked > 0, "no run bodies were checked; this test would pass vacuously"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not on PATH")
def test_shellcheck_can_actually_fail(tmp_path):
    """Same self-test, one level down: prove shellcheck reports."""
    path = tmp_path / "bad.sh"
    path.write_text("#!/usr/bin/env bash\nif [ $UNQUOTED = x ]; then echo hi; fi\n")
    result = subprocess.run(
        ["shellcheck", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "shellcheck passed a script it should reject"


# --------------------------------------------------------------------------
# What the image must contain to be able to answer at all.
# --------------------------------------------------------------------------


def test_the_image_ships_every_runtime_path_the_code_reads_from_disk():
    """A healthy runtime that cannot complete one invocation.

    THIS IS A REGRESSION TEST FOR A DEPLOY THAT REACHED PRODUCTION-SHAPED
    FAILURE. All five runtimes reported READY, /ping answered 200, and every
    /invocations returned 500:

        FileNotFoundError: [Errno 2] No such file or directory:
        '/app/fixtures/plan_result.json'

    `fixtures/` lives at the repo ROOT, outside the package, and
    agentorg/fixtures_loader.py resolves it by walking up from its own file. So
    `pip install .` does not ship it -- not even with the
    [tool.setuptools.packages.find] fix, which covers subpackages and
    agentorg/security's data files. Only an explicit COPY puts it in the image.

    Every agent falls back to a fixture when no model answers, so this directory
    is not test scaffolding: it is the runtime's answer of last resort, and
    without it the container is healthy and useless.

    Asserts on the loader's own resolution rather than a hardcoded list, so a
    future fixture directory cannot be added to the code and forgotten here.
    """
    dockerfile = REPO_ROOT / "agentorg" / "agents" / "Dockerfile"
    assert dockerfile.is_file(), f"no Dockerfile at {dockerfile}"
    body = dockerfile.read_text(encoding="utf-8")

    loader = (REPO_ROOT / "agentorg" / "fixtures_loader.py").read_text(encoding="utf-8")
    assert 'parent.parent / "fixtures"' in loader, (
        "fixtures_loader no longer resolves fixtures/ from the repo root; this "
        "test is pinned to that resolution and must be updated with it"
    )

    copied = re.findall(r"^COPY\s+(\S+)", body, re.MULTILINE)
    assert "fixtures" in copied, (
        f"the image does not COPY fixtures/, which fixtures_loader reads at "
        f"runtime from the repo root. COPY lines present: {copied}"
    )

    # The directory must actually hold the fixtures the agents load, or the COPY
    # would ship an empty directory and the failure would look identical.
    present = {p.name for p in (REPO_ROOT / "fixtures").glob("*.json")}
    assert "plan_result.json" in present, (
        f"fixtures/ does not contain plan_result.json; found {sorted(present)}"
    )


# --------------------------------------------------------------------------
# SCANNERS_REQUIRED: the knob that decides whether the cloud verdict is REAL.
#
# THE ASYMMETRY IS THE WHOLE POINT AND IT CUTS BOTH WAYS.
#
# Without the knob, a security runtime whose image lacks the three binaries
# takes the ABSENT path: every wrapper raises, agents/security.py catches it,
# and the verdict is read out of fixtures/security_result_block.json. The gate
# still says "blocked" -- from JSON deserialisation, not from
# compute_security_verdict. That is failing OPEN while looking green, which is
# the single defect this repository exists to prevent.
#
# With the knob on an agent whose image lacks the binaries, absent is promoted
# to FAULT: three *-scanner-error findings, blocking=3, and the CLEAN run
# blocks too (agentorg/common/config.py:64-100). Set on all five, the demo's
# first half dies on a projector.
#
# So the property is not "the knob is present". It is "the knob is present on
# EXACTLY ONE of the five agents, and that agent is security, in BOTH the
# update and the create branch". Nothing short of that is the property.
# --------------------------------------------------------------------------

# agentorg/common/config.py parses this knob case-insensitively against the
# literal "true", so "1", "yes" and "TRUE " are all read as False. A workflow
# setting an unparseable value would produce a runtime that believes it has
# fail-closed scanners and does not -- config.py's own comment calls that the
# worst available outcome. Pinned here as the value, and below as the parse.
SCANNERS_REQUIRED_NAME = "SCANNERS_REQUIRED"
SCANNERS_REQUIRED_VALUE = "true"

# The one agent whose image is expected to carry gitleaks, trivy and semgrep,
# and therefore the only one that may demand them.
SCANNER_AGENT = "security"

# Record separator for the stub's argv log. Chosen because no argument in
# deploy.yml contains it, so splitting on it recovers argv exactly -- unlike
# splitting on spaces, which the --description argument would break.
_ARGV_SEP = "\x1f"


def _runtime_loop_script():
    """deploy.yml's one run body that creates or updates the five runtimes.

    Asserts it found exactly one. If the loop is ever split across two steps,
    every test below would silently start checking half of it, so this refuses
    to guess.
    """
    scripts = [
        s
        for s in _all_run_scripts(DEPLOY)
        if "create-agent-runtime" in s and "update-agent-runtime" in s
    ]
    assert len(scripts) == 1, (
        f"expected exactly one run body holding BOTH the update and create "
        f"branches of the runtime loop; found {len(scripts)}. Splitting them "
        f"would let the two branches drift apart unnoticed."
    )
    return scripts[0]


def test_both_runtime_api_calls_pass_the_SAME_environment_variables():
    """update-agent-runtime and create-agent-runtime must not drift apart.

    `--environment-variables` appears TWICE in the loop, once per branch. An
    existing runtime is updated; an absent one is created. A knob added to only
    one branch means the first deploy of a fresh runtime and every deploy after
    it configure the environment differently -- and which branch runs depends
    on account state, not on this repository, so the difference would surface
    as an intermittent demo failure rather than as a diff.

    Compares the two argument values as text rather than requiring a
    particular variable name, so a refactor that keeps them equal passes.
    """
    script = _runtime_loop_script()
    values = re.findall(r"--environment-variables\s+(\S+)", script)
    assert values, (
        "the runtime loop passes no --environment-variables at all; AGENT_ROLE "
        "would be unset and every runtime would fail at startup"
    )
    assert len(values) == 2, (
        f"expected one --environment-variables per branch (2 total), found "
        f"{len(values)}: {values}"
    )
    assert values[0] == values[1], (
        f"the update branch passes {values[0]} and the create branch passes "
        f"{values[1]}. A fresh runtime's first deploy would then differ from "
        f"every later one."
    )


def test_config_still_parses_the_knob_the_way_this_file_assumes():
    """Pins the PARSE, not just the name.

    These tests assert the workflow sets SCANNERS_REQUIRED=true. That is only
    meaningful while config.py reads the value with `.lower() == "true"`. If
    the parser changed, the tests below would keep passing while pinning a
    value the code no longer honours -- a test that stopped testing.
    """
    config = (REPO_ROOT / "agentorg" / "common" / "config.py").read_text(encoding="utf-8")
    expected = f'{SCANNERS_REQUIRED_NAME} = os.environ.get("{SCANNERS_REQUIRED_NAME}", "false").lower() == "true"'
    assert expected in config, (
        f"agentorg/common/config.py no longer parses {SCANNERS_REQUIRED_NAME} as "
        f"{expected!r}. The workflow sets {SCANNERS_REQUIRED_VALUE!r}; re-measure "
        f"what the code now accepts before trusting the tests below."
    )


def _stub_aws(bin_dir, calls_log, existing_runtimes):
    """An `aws` that records its argv and answers from a file. Never calls AWS.

    Written into a directory placed FIRST on PATH, which is how
    tests/provenance.py shadows the scanner binaries. Shadowing is enough: the
    workflow body invokes plain `aws`, and the first PATH match wins. The
    caller asserts the shadow took effect BEFORE running anything, so a real
    credentialed call cannot happen by accident.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "aws"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "{ for a in \"$@\"; do printf '%s\\x1f' \"$a\"; done; printf '\\n'; } "
        f'>> "{calls_log}"\n'
        'case "$1 $2" in\n'
        '  "sts get-caller-identity") echo 339712964409 ;;\n'
        "  \"bedrock-agentcore-control list-agent-runtimes\")\n"
        f'    cat "{existing_runtimes}" ;;\n'
        "  *)\n"
        "    echo arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/stub ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _env_vars_per_agent(tmp_path, *, runtimes_exist):
    """Run deploy.yml's runtime loop for real, with `aws` stubbed, and read back
    the environment each of the five agents would be configured with.

    THIS EXECUTES THE GUARD RATHER THAN GREPPING FOR IT, which is the only way
    to tell "a line mentioning security exists somewhere in the body" from "the
    knob actually reaches security and only security". deploy.yml is heavily
    commented -- this file's own header records `id-token: write` appearing on
    three lines, only two of them real -- so a substring check for either the
    knob or the guard is satisfiable by prose.

    `runtimes_exist` picks the branch: True feeds `list-agent-runtimes` five
    existing runtimes so the loop UPDATEs, False feeds it nothing so the loop
    CREATEs. Both branches carry their own --environment-variables argument.

    Returns {agent: {NAME: value}} parsed from the recorded argv.
    """
    script = _runtime_loop_script()
    # GitHub expands ${{ }} before the shell sees it. env.* resolve from the
    # workflow's own env block so ECR_PREFIX and AWS_REGION are real; anything
    # else (github.sha) becomes an inert token.
    shell = re.sub(r"\$\{\{[^}]*\}\}", "EXPANDED", _resolve_env(DEPLOY, script))

    bin_dir = tmp_path / "stub-bin"
    calls_log = tmp_path / "aws-calls.log"
    existing = tmp_path / "existing-runtimes.txt"
    existing.write_text(
        "".join(f"theagentorg_{a}\tstub-id-{a}\n" for a in AGENTS)
        if runtimes_exist
        else "",
        encoding="utf-8",
    )
    _stub_aws(bin_dir, calls_log, existing)

    path = os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])
    # BEFORE running: prove the stub shadows any real aws. Without this the
    # body would reach a credentialed CLI, which no test in this file may do.
    resolved = shutil.which("aws", path=path)
    assert resolved == str(bin_dir / "aws"), (
        f"the aws stub is not first on PATH (resolved {resolved!r}); refusing "
        f"to run the loop, because a real AWS call would be billable and could "
        f"mutate live runtimes"
    )

    target = tmp_path / "runtime-loop.sh"
    target.write_text("#!/usr/bin/env bash\n" + shell, encoding="utf-8")
    result = subprocess.run(
        ["bash", str(target)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PATH": path},
        check=False,
    )
    assert result.returncode == 0, (
        f"deploy.yml's runtime loop exited {result.returncode} under the stub, "
        f"so nothing below is measuring the real thing:\n"
        f"--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}"
    )

    assert calls_log.is_file(), (
        "the aws stub was never invoked; the loop cannot have configured "
        "anything and every assertion below would pass vacuously"
    )
    per_agent = {}
    for line in calls_log.read_text(encoding="utf-8").splitlines():
        argv = [a for a in line.split(_ARGV_SEP) if a]
        if not any(
            a in ("update-agent-runtime", "create-agent-runtime") for a in argv
        ):
            continue
        if "--environment-variables" not in argv:
            continue
        raw = argv[argv.index("--environment-variables") + 1]
        pairs = dict(
            item.split("=", 1) for item in raw.split(",") if "=" in item
        )
        role = pairs.get("AGENT_ROLE")
        assert role, f"a runtime was configured without AGENT_ROLE: {raw!r}"
        per_agent[role] = pairs

    assert sorted(per_agent) == sorted(AGENTS), (
        f"the loop configured {sorted(per_agent)}, expected all of {AGENTS}. "
        f"The recorded calls were:\n{calls_log.read_text(encoding='utf-8')!r}"
    )
    return per_agent


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
@pytest.mark.parametrize("runtimes_exist", [True, False], ids=["update", "create"])
def test_only_the_security_runtime_demands_its_scanners(tmp_path, runtimes_exist):
    """SCANNERS_REQUIRED on an agent without binaries blocks the CLEAN run too.

    config.py:64-100 measures it: the knob promotes ABSENT to FAULT, so a
    runtime that cannot find gitleaks returns three *-scanner-error findings
    and blocking=3. Setting it on all five would take the demo's first half
    down; setting it on none leaves the cloud verdict a fixture read.

    Parametrised over BOTH branches of the loop, because `--environment-
    variables` is passed twice and which one runs depends on whether the
    runtime already exists in the account.
    """
    per_agent = _env_vars_per_agent(tmp_path, runtimes_exist=runtimes_exist)

    demanding = sorted(a for a, e in per_agent.items() if SCANNERS_REQUIRED_NAME in e)
    assert demanding, (
        f"no runtime sets {SCANNERS_REQUIRED_NAME}: the deployed security agent "
        f"would take the ABSENT path, catch every FileNotFoundError and read its "
        f"verdict out of fixtures/security_result_block.json. 'The security gate "
        f"ran in the cloud' would be false."
    )
    assert demanding == [SCANNER_AGENT], (
        f"{SCANNERS_REQUIRED_NAME} reaches {demanding}, expected only "
        f"[{SCANNER_AGENT!r}]. On any agent whose image lacks the three binaries "
        f"the knob promotes ABSENT to FAULT, so even the CLEAN run blocks with "
        f"blocking=3 (agentorg/common/config.py:64-100)."
    )
    assert per_agent[SCANNER_AGENT][SCANNERS_REQUIRED_NAME] == SCANNERS_REQUIRED_VALUE, (
        f"{SCANNER_AGENT} gets {SCANNERS_REQUIRED_NAME}="
        f"{per_agent[SCANNER_AGENT][SCANNERS_REQUIRED_NAME]!r}, expected "
        f"{SCANNERS_REQUIRED_VALUE!r}. config.py compares against the literal "
        f'"true" after .lower(), so any other spelling reads as False and the '
        f"runtime falls back to the fixture while appearing configured."
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
@pytest.mark.parametrize("runtimes_exist", [True, False], ids=["update", "create"])
def test_every_runtime_still_gets_its_own_agent_role(tmp_path, runtimes_exist):
    """The knob must not be added by clobbering what was already there.

    `env_vars` is now built up rather than passed as a literal, so the failure
    mode this guards is an assignment that drops AGENT_ROLE: five runtimes
    would serve whatever the image defaults to -- except the image has no
    default, so all five would fail at startup instead. Cheap to pin, and it
    keeps the accumulation honest.
    """
    per_agent = _env_vars_per_agent(tmp_path, runtimes_exist=runtimes_exist)
    for agent in AGENTS:
        assert per_agent[agent]["AGENT_ROLE"] == agent, (
            f"runtime for {agent} was configured with AGENT_ROLE="
            f"{per_agent[agent]['AGENT_ROLE']!r}"
        )
