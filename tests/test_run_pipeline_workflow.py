"""Pins run-pipeline.yml's blast radius, and the gates it cannot be talked out of.

Owner: Task 3 (cloud-native platform lane).

WHAT THIS WORKFLOW IS, AND WHY IT NEEDED SPLITTING INTO SEVEN JOBS
-----------------------------------------------------------------
`agentorg.graph.run_pipeline` walks a ticket through five agents and three human
gates in ONE function call. That shape cannot survive on GitHub Actions, and the
reason is structural rather than stylistic: a human gate here is a GitHub
Environment with a required reviewer, and an Environment pauses a JOB. A job
cannot pause in its middle, so the pipeline is cut at the gate boundaries:

    plan -> [gate1] -> develop -> [gate2] -> sre -> [gate3] -> promote

`RunState` is handed along as an Actions artifact, written and read through
`agentorg.gates.save`/`resume`, which already existed for exactly this.

WHAT THIS WORKFLOW MAKES WORSE, WHICH IS WHAT THESE TESTS ARE ABOUT
------------------------------------------------------------------
Three new hazards, none of which existed in this repository before:

1. It runs the agents with `REMOTE_AGENTS=true`, so every agent call becomes an
   `invoke_agent_runtime` against a live Bedrock AgentCore runtime. Three of its
   jobs therefore hold `id-token: write` and assume the SHARED
   `github-actions-role` in account 339712964409.
2. It runs with a real `GITHUB_TOKEN` and a real `DEMO_REPO`, which is what makes
   `github_ops._use_local()` take its ONLINE branch (github_ops.py:56) and open a
   real pull request on the target repository. A misconfiguration here writes to
   somebody's repo.
3. Its three gate jobs are the entire human-approval story. A gate that can be
   skipped, renamed out of existence, or steered by a workflow input is not a
   gate -- it is a pause that looks like one.

WHY THESE PARSE THE YAML INSTEAD OF GREPPING IT
-----------------------------------------------
Measured on THIS plan, at Task 1: three sketched substring assertions all passed
against a workflow that had the knob set on nobody, because the strings they
looked for sat in the file's own comments. A workflow is a heavily commented
document, so `"environment: gate2" in text` is evidence about the prose and not
about any job. Everything below loads the document with `yaml.safe_load` and
asserts over the parsed structure.

The same discipline applies to the matchers. Every comprehension that builds a
list of jobs asserts the list is non-empty BEFORE asserting over it. Renaming a
gate job must turn this file red; it must not quietly empty a comprehension and
leave a test that runs zero assertions and reports green. Three tests in this
repository went vacuous that way in one week.

THE `on:` TRAP, inherited from tests/test_deploy_workflow.py: YAML 1.1 resolves
the unquoted key `on` to the BOOLEAN True, so `doc["on"]` raises KeyError.
`_triggers()` goes through True and asserts the key is present, so a rename
cannot make it return an empty dict and take every trigger assertion green.

WHAT THESE TESTS DO NOT CLAIM
-----------------------------
They do not claim the workflow has ever run. It cannot run until the commit
carrying it is on the default branch and the three Environments exist, and
creating an Environment is a repository SETTING -- no test in any language can
assert it from the filesystem. So these are structural, plus real unit tests
over `scripts/run_stage.py`, which is where the workflow's decisions actually
live and is therefore the part that can be executed here.
"""

import itertools
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RUN_PIPELINE = WORKFLOWS / "run-pipeline.yml"
STAGE_SCRIPT = REPO_ROOT / "scripts" / "run_stage.py"

# Read from docs/plan/week1-verification-log.md, never re-derived from live AWS.
RECORDED_ROLE_ARN = "arn:aws:iam::339712964409:role/github-actions-role"
RECORDED_REGION = "us-east-1"

# The pipeline, in order, one job per element. Cut at the gate boundaries because
# an Environment pauses a whole job -- see this file's header.
#
# Declared here as a LITERAL rather than derived from the workflow, which is the
# point: a job that appears, disappears or is renamed must be a deliberate edit
# to this list. Deriving the order from `needs` and then checking `needs` against
# it would be checking the file against itself.
STAGE_CHAIN = ["plan", "gate1", "develop", "gate2", "sre", "gate3", "promote"]

# The three gates, by name. `agentorg.state.HumanDecision.gate` is
# Literal["gate1", "gate2", "gate3"], so these names are not cosmetic: a job
# named gate4 would produce a decision the state model refuses to validate.
GATE_JOBS = ["gate1", "gate2", "gate3"]

# The jobs that reach AWS, because they invoke an AgentCore runtime through
# `agent_client.call_agent`. Everything else -- the three gates and promote --
# does local file I/O only and must hold no credential at all.
AGENT_JOBS = ["plan", "develop", "sre"]

STATIC_KEY_INPUTS = ("aws-access-key-id", "aws-secret-access-key", "aws-session-token")
KEY_ENV_NAMES = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


def _doc(path=RUN_PIPELINE):
    """Parse the workflow to a dict. safe_load, so no tag can execute anything."""
    return yaml.safe_load(path.read_text())


def _triggers(path=RUN_PIPELINE):
    """The parsed `on:` mapping, keyed on the YAML 1.1 boolean True.

    Asserting the key's presence means a future rename cannot make this return an
    empty dict and take every trigger assertion green with it.
    """
    doc = _doc(path)
    assert True in doc, f"{path.name} has no `on:` block (YAML 1.1 bool key)"
    return doc[True]


def _jobs(path=RUN_PIPELINE):
    doc = _doc(path)
    assert doc.get("jobs"), f"{path.name} has no `jobs:` block"
    return doc["jobs"]


def _job(name, path=RUN_PIPELINE):
    jobs = _jobs(path)
    assert name in jobs, f"{path.name} jobs are {sorted(jobs)}, expected {name!r}"
    return jobs[name]


def _steps(job):
    steps = job.get("steps")
    assert steps, "job has no steps"
    return steps


def _needs(job):
    """A job's `needs` as a list, whether written as a scalar or a sequence."""
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _effective_env(job_name, path=RUN_PIPELINE):
    """Workflow env overlaid with one job's env -- what that job's steps see.

    GitHub resolves it in this order, so a test that read only the job block
    would report REMOTE_AGENTS missing on every job while it sat at the top
    level, and a test that read only the top level would miss a job overriding
    it. Both halves, one accessor.
    """
    doc = _doc(path)
    env = dict(doc.get("env") or {})
    env.update(_job(job_name, path).get("env") or {})
    return env


def _artifact_steps(job, action):
    """Every upload-artifact/download-artifact step in one job, as `with:` dicts."""
    return [
        step.get("with") or {}
        for step in (job.get("steps") or [])
        if f"actions/{action}-artifact" in str(step.get("uses", ""))
    ]


def _uploaded_names(job):
    return [w.get("name") for w in _artifact_steps(job, "upload") if w.get("name")]


def _downloaded_names(job):
    return [w.get("name") for w in _artifact_steps(job, "download") if w.get("name")]


def _credential_steps(path=RUN_PIPELINE):
    """(job_name, step) for every configure-aws-credentials step."""
    found = []
    for job_name, job in _jobs(path).items():
        for step in job.get("steps") or []:
            if "configure-aws-credentials" in str(step.get("uses", "")):
                found.append((job_name, step))
    return found


# --------------------------------------------------------------------------
# The file parses at all. Everything below depends on this, so it fails first.
# --------------------------------------------------------------------------


def test_the_run_pipeline_workflow_and_its_stage_script_exist():
    assert RUN_PIPELINE.is_file(), f"{RUN_PIPELINE} is missing"
    assert STAGE_SCRIPT.is_file(), f"{STAGE_SCRIPT} is missing"


def test_the_workflow_parses_and_holds_exactly_the_seven_stage_jobs():
    """Set equality, not containment.

    Containment would let an extra job appear -- one holding credentials, or one
    quietly bypassing a gate -- while this stayed green.
    """
    jobs = _jobs()
    assert set(jobs) == set(STAGE_CHAIN), (
        f"run-pipeline.yml jobs are {sorted(jobs)}, expected {sorted(STAGE_CHAIN)}"
    )


# --------------------------------------------------------------------------
# HAZARD 3 -- the gates. The human-approval story, and the easiest to hollow out.
# --------------------------------------------------------------------------


def test_every_gate_job_declares_an_environment():
    """THE test the brief's RED step deletes `environment: gate2` to break.

    Reads the parsed `environment:` key on each job. A grep for
    "environment: gate2" would be satisfied by this workflow's own header
    comment, which names all three gates in prose.
    """
    gates = [name for name in _jobs() if name in GATE_JOBS]
    assert gates, (
        f"no job in run-pipeline.yml is named one of {GATE_JOBS}; either the gates "
        f"were renamed or removed, and this test would otherwise check nothing"
    )
    assert sorted(gates) == sorted(GATE_JOBS), (
        f"gate jobs present are {sorted(gates)}, expected {sorted(GATE_JOBS)}"
    )
    for name in gates:
        env = _job(name).get("environment")
        assert env, (
            f"job {name} declares no `environment:`, so it does not pause for a "
            f"human at all -- it just runs. That is the whole gate, gone."
        )


def test_the_three_gates_are_exactly_gate1_gate2_gate3_by_two_independent_routes():
    """The anti-vacuity construction, and the reason this test exists separately.

    Two sets, derived differently: jobs whose NAME is a known gate, and jobs that
    DECLARE an environment. Renaming `gate2` to `security_gate` empties the first
    and leaves the second, so the equality fails -- whereas a single
    name-keyed comprehension would silently shrink and every assertion over it
    would pass having checked one fewer gate.

    The environment NAME must equal the job name too. `gate2` declaring
    `environment: gate1` would pause twice at one reviewer's approval and never
    ask the second question, which reads identically in the Actions UI.
    """
    by_name = {name for name in _jobs() if name in GATE_JOBS}
    by_environment = {name for name, job in _jobs().items() if job.get("environment")}

    assert by_name, "no job is named gate1/gate2/gate3"
    assert by_environment, "no job in run-pipeline.yml declares an environment at all"
    assert by_name == by_environment, (
        f"jobs named as gates are {sorted(by_name)}, jobs declaring an environment "
        f"are {sorted(by_environment)}; these must be the same three jobs"
    )
    assert by_name == set(GATE_JOBS), (
        f"the gates are {sorted(by_name)}, expected {GATE_JOBS}"
    )

    for name in sorted(by_name):
        env = _job(name).get("environment")
        env_name = env.get("name") if isinstance(env, dict) else env
        assert env_name == name, (
            f"job {name} declares environment {env_name!r}; a gate job's "
            f"environment must be its own name or it asks the wrong question"
        )


def test_no_gate_can_be_steered_by_a_workflow_input():
    """A protection an input can turn off is not a protection.

    Two ways to hollow out a gate without deleting it:

      * `environment: ${{ inputs.auto_approve && 'none' || 'gate1' }}` -- the job
        still declares an environment, and this file's other tests would pass.
      * `if: inputs.auto_approve != true` -- the job is skipped entirely, and a
        skipped gate and an approved gate look the same from downstream.

    `auto_approve` exists and defaults false, and it deliberately does NOT bypass
    an Environment: it only stamps the recorded HumanDecision as automatic. An
    Environment with a required reviewer is a repository setting, and the whole
    value of putting the gates there is that workflow content cannot argue with
    it.
    """
    gates = [name for name in _jobs() if name in GATE_JOBS]
    assert gates, "no gate jobs found; this test would check nothing"
    for name in gates:
        job = _job(name)
        env = job.get("environment")
        env_text = str(env)
        assert "${{" not in env_text, (
            f"gate job {name} computes its environment ({env_text!r}); an "
            f"expression here means an input can choose which gate -- or no gate"
        )
        assert "if" not in job, (
            f"gate job {name} carries `if: {job.get('if')!r}`; a gate that can be "
            f"skipped cannot be told apart from a gate that was approved"
        )


def test_no_gate_job_can_reach_aws_or_run_an_agent():
    """A pause needs no credentials, and `gates.resume` is local file I/O.

    A gate job holding `id-token: write` would be a human-approval step able to
    assume a role shared with three other repositories -- capability with no
    purpose, on the one job whose entire job is to wait.
    """
    gates = [name for name in _jobs() if name in GATE_JOBS]
    assert gates, "no gate jobs found; this test would check nothing"
    credentialled = {name for name, _ in _credential_steps()}
    for name in gates:
        perms = _job(name).get("permissions") or {}
        assert perms.get("id-token") != "write", (
            f"gate job {name} holds id-token: write; it only records a decision"
        )
        assert name not in credentialled, (
            f"gate job {name} assumes an AWS role; a pause needs no credentials"
        )


def test_the_gates_sit_between_the_right_stages():
    """The order IS the pipeline, and `needs` is the only thing enforcing it.

    Without this, `sre` could `needs: [develop]` and skip gate2 entirely while
    gate2 still existed, still declared its environment, and still paused
    somebody -- in parallel with the run it was supposed to be gating.
    """
    jobs = _jobs()
    for earlier, later in itertools.pairwise(STAGE_CHAIN):
        needs = _needs(jobs[later])
        assert needs, f"job {later} declares no `needs`; it runs in parallel with everything"
        assert earlier in needs, (
            f"job {later} needs {needs}, which does not include {earlier}; the "
            f"chain {' -> '.join(STAGE_CHAIN)} is broken, so {later} does not "
            f"wait for {earlier}"
        )
    assert not _needs(jobs[STAGE_CHAIN[0]]), (
        f"job {STAGE_CHAIN[0]} has needs {_needs(jobs[STAGE_CHAIN[0]])}; it is first"
    )


def test_no_job_runs_regardless_of_whether_its_dependency_succeeded():
    """`needs` alone does NOT stop a job -- an `if:` can override the outcome.

    FOUND BY A MUTATION THAT SURVIVED THIS FILE. `if: always()` was added to the
    `sre` job: every other test here stayed green, because sre is not a gate job
    and the gate tests do not look at it, and `needs: [plan, gate2]` was still
    present and correct. But `always()` makes a job run even when what it needs
    FAILED or was SKIPPED -- so a run whose gate2 reviewer clicked reject, or
    whose develop job blocked on the poisoned diff, would carry on into SRE and
    on to gate3. The gates would all still be there, all still pausing, and all
    still non-binding.

    That is the failure this whole workflow exists to make impossible, and it
    survived, so the rule is asserted at the JOB level for every job rather than
    only on the three gates.

    STEPS ARE A DIFFERENT MATTER and are deliberately not covered: two upload
    steps in this file legitimately carry `if: always()`, because a BLOCKED run
    exits non-zero on purpose and its state -- the verdict, the findings, the PR
    url -- is the most valuable artifact the run produces. `always()` on a step
    preserves evidence; `always()` on a job discards a decision.
    """
    # `success()` is the default and needs no `if:` at all. Anything in this set
    # detaches a job from its dependency's outcome.
    outcome_ignoring = ("always(", "failure(", "cancelled(", "!cancelled(")
    jobs = _jobs()
    assert jobs, "no jobs found; this test would check nothing"
    for name, job in jobs.items():
        condition = str(job.get("if") or "")
        for token in outcome_ignoring:
            assert token not in condition, (
                f"job {name} carries `if: {condition}`, which contains {token!r}: "
                f"it runs even when what it `needs` failed or was skipped. Every "
                f"gate upstream of it becomes advisory -- a rejected gate2 or a "
                f"blocked develop would no longer stop the run."
            )


# --------------------------------------------------------------------------
# The state handoff. The property most likely to be pinned vacuously.
# --------------------------------------------------------------------------


def test_each_stage_uploads_the_state_and_the_next_stage_downloads_it():
    """Both halves, matched by NAME, for every consecutive pair in the chain.

    The vacuous version of this test asserts "some job uploads an artifact and
    some job downloads one" -- which stays green when the download disappears
    from `sre`, because `develop` still downloads and `plan` still uploads. So
    this walks the chain pairwise and requires the LATER job to name the artifact
    the EARLIER job produced. Delete any one download and exactly one pair fails,
    naming both jobs.

    Names are compared as sets rather than against a template, so renaming the
    scheme is free while removing a link is not.
    """
    jobs = _jobs()
    links = 0
    for earlier, later in itertools.pairwise(STAGE_CHAIN):
        uploaded = set(_uploaded_names(jobs[earlier]))
        downloaded = set(_downloaded_names(jobs[later]))
        assert uploaded, (
            f"job {earlier} uploads no named artifact, so the RunState it just "
            f"wrote dies with the runner and {later} starts from nothing"
        )
        assert downloaded, (
            f"job {later} downloads no artifact, so it cannot see the RunState "
            f"{earlier} produced -- it would silently start a fresh run"
        )
        shared = uploaded & downloaded
        assert shared, (
            f"job {earlier} uploads {sorted(uploaded)} but job {later} downloads "
            f"{sorted(downloaded)}; no name in common, so the handoff is broken"
        )
        links += 1
    assert links == len(STAGE_CHAIN) - 1, (
        f"checked {links} handoffs, expected {len(STAGE_CHAIN) - 1}"
    )


def test_every_upload_fails_the_job_when_there_is_nothing_to_upload():
    """`if-no-files-found` defaults to `warn`: an empty artifact is a green step.

    That is the defect this repository exists to prevent, in artifact form. The
    next job's download SUCCEEDS against an empty artifact, the stage script then
    finds no state file, and "the previous stage produced nothing" arrives as a
    missing-file error three jobs downstream.
    """
    uploads = [
        (name, w)
        for name, job in _jobs().items()
        for w in _artifact_steps(job, "upload")
    ]
    assert uploads, "no upload-artifact step anywhere; the state is never handed on"
    for job_name, with_inputs in uploads:
        assert with_inputs.get("if-no-files-found") == "error", (
            f"job {job_name} uploads {with_inputs.get('name')!r} with "
            f"if-no-files-found={with_inputs.get('if-no-files-found')!r}; the "
            f"default (warn) publishes an empty artifact as a successful step"
        )


# --------------------------------------------------------------------------
# HAZARD 1 -- credentials. No static key, and id-token exactly where needed.
# --------------------------------------------------------------------------


def test_no_step_passes_a_static_aws_key():
    """Structural: reads each step's `with:` inputs as data.

    A grep would be satisfied by this workflow's own header comment forbidding
    the practice -- the inverse defect, a test that can only ever fail.
    """
    checked = 0
    for job_name, job in _jobs().items():
        for step in job.get("steps") or []:
            with_inputs = step.get("with") or {}
            for forbidden in STATIC_KEY_INPUTS:
                assert forbidden not in with_inputs, (
                    f"job {job_name} step {step.get('name') or step.get('uses')!r} "
                    f"passes {forbidden}; every AWS step assumes the role via OIDC"
                )
            checked += 1
    assert checked, "no steps were examined; this test would pass vacuously"


def test_no_aws_key_arrives_through_env_or_a_secret():
    """The other half: a key can arrive through env rather than through `with:`."""
    doc = _doc()
    scopes = [("workflow env", doc.get("env") or {})]
    for job_name, job in _jobs().items():
        scopes.append((f"job {job_name} env", job.get("env") or {}))
        for step in job.get("steps") or []:
            label = f"job {job_name} step {step.get('name') or step.get('uses')!r} env"
            scopes.append((label, step.get("env") or {}))
    assert len(scopes) > 1, "only the workflow env was examined; expected job scopes too"

    for label, mapping in scopes:
        for name in KEY_ENV_NAMES:
            assert name not in mapping, f"{label} defines {name}; OIDC only"

    for job_name, job in _jobs().items():
        for step in job.get("steps") or []:
            script = step.get("run") or ""
            for name in KEY_ENV_NAMES:
                assert name not in script, f"job {job_name} run body references {name}"
            assert "secrets.AWS" not in script, (
                f"job {job_name} run body reads an AWS secret; OIDC needs none"
            )


def test_no_key_shaped_string_appears_anywhere_in_the_file():
    """RAW TEXT on purpose -- a pasted key in a COMMENT is still a pasted key.

    Comments do not survive parsing, so this is the one property where parsed
    structure is the wrong altitude. AKIAIOSFODNN7EXAMPLE is AWS's own published
    example and is the only permitted match.
    """
    found = set(re.findall(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", RUN_PIPELINE.read_text()))
    assert found <= {"AKIAIOSFODNN7EXAMPLE"}, f"key-shaped strings: {sorted(found)}"


def test_id_token_write_is_not_granted_at_the_top_level():
    """A top-level grant hands token-minting to every job anyone later adds."""
    top = _doc().get("permissions") or {}
    assert top.get("id-token") != "write", (
        f"run-pipeline.yml grants id-token: write at the top level ({top!r}); "
        f"scope it to the three jobs that invoke a runtime"
    )


def test_the_jobs_holding_id_token_write_are_exactly_the_jobs_that_assume_a_role():
    """Set equality both ways, and the set is named.

    A job with `id-token: write` and no credential step is unused capability on
    a shared role; a job with a credential step and no `id-token: write` simply
    cannot authenticate. Both are defects, and comparing the two derived sets
    against the DECLARED list catches the third case: the right shape on the
    wrong jobs.
    """
    with_token = {
        name
        for name, job in _jobs().items()
        if (job.get("permissions") or {}).get("id-token") == "write"
    }
    with_creds = {name for name, _ in _credential_steps()}
    assert with_token, "no job holds id-token: write, so no agent call can authenticate"
    assert with_token == with_creds, (
        f"jobs with id-token: write are {sorted(with_token)}, jobs assuming a role "
        f"are {sorted(with_creds)}; these must match"
    )
    assert with_token == set(AGENT_JOBS), (
        f"the AWS-reaching jobs are {sorted(with_token)}, expected {sorted(AGENT_JOBS)}"
    )


def test_every_credential_step_assumes_the_recorded_role_in_the_recorded_region():
    """Compared after resolving ${{ env.X }}, which GitHub expands before anything runs.

    Comparing the unexpanded template instead would go green precisely when
    somebody stopped using the env block.
    """
    env = _doc().get("env") or {}

    def resolve(text):
        return re.sub(
            r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
            lambda m: str(env[m.group(1)]) if m.group(1) in env else m.group(0),
            str(text),
        )

    steps = _credential_steps()
    assert steps, "no configure-aws-credentials step; the agent jobs cannot authenticate"
    for job_name, step in steps:
        with_inputs = step.get("with") or {}
        assert resolve(with_inputs.get("role-to-assume")) == RECORDED_ROLE_ARN, (
            f"job {job_name} assumes {with_inputs.get('role-to-assume')!r}, "
            f"expected {RECORDED_ROLE_ARN}"
        )
        assert resolve(with_inputs.get("aws-region")) == RECORDED_REGION, (
            f"job {job_name} uses region {with_inputs.get('aws-region')!r}"
        )


# --------------------------------------------------------------------------
# HAZARD 2 -- the configuration that decides whether this run is real at all.
# --------------------------------------------------------------------------


def test_remote_agents_is_true_for_every_job_that_calls_an_agent():
    """The knob that makes this the CLOUD pipeline rather than a local rehearsal.

    `config.REMOTE_AGENTS` parses `== "true"` case-insensitively
    (common/config.py:130) and defaults FALSE, so an unset or misspelled value
    silently runs all five agents in-process on the runner. Every stage would go
    green and the five deployed runtimes would be untouched -- a demo of nothing,
    reported as success.

    Checked per job, on the resolved workflow+job env, so setting it on two jobs
    and forgetting the third cannot pass.
    """
    for name in AGENT_JOBS:
        env = _effective_env(name)
        assert "REMOTE_AGENTS" in env, (
            f"job {name} does not set REMOTE_AGENTS; it defaults false and every "
            f"agent would run in-process on the runner"
        )
        assert str(env["REMOTE_AGENTS"]).lower() == "true", (
            f"job {name} sets REMOTE_AGENTS={env['REMOTE_AGENTS']!r}; config.py "
            f"parses `== \"true\"`, so anything else means the local path"
        )


def test_the_github_seam_is_configured_from_a_secret_and_a_repo_variable():
    """What makes `_use_local()` take its ONLINE branch and open a real PR.

    `github_ops._use_local()` (github_ops.py:48) returns
    `config.OFFLINE or not (config.GITHUB_TOKEN and config.GITHUB_REPO)`, and
    `config.GITHUB_REPO` reads the env var named DEMO_REPO (config.py:48). So all
    three have to be right at once, and each one being wrong fails the same
    silent way: the pipeline runs a local `git` branch in a temp directory, every
    stage goes green, and no pull request appears anywhere.

    The token must come from `secrets.` and the repo from `vars.` -- a literal
    repo name here is a workflow that can only ever target one repository, and a
    literal token is a leaked credential.
    """
    jobs_needing_github = [name for name in _jobs() if name != "promote"]
    assert jobs_needing_github, "no jobs found; this test would check nothing"

    checked = 0
    for name in _jobs():
        env = _effective_env(name)
        if "DEMO_REPO" not in env and "GITHUB_TOKEN" not in env:
            continue
        assert "DEMO_REPO" in env and "GITHUB_TOKEN" in env, (
            f"job {name} sets only one of DEMO_REPO/GITHUB_TOKEN ({sorted(env)}); "
            f"_use_local() needs BOTH to take the online branch"
        )
        assert "vars." in str(env["DEMO_REPO"]), (
            f"job {name} sets DEMO_REPO={env['DEMO_REPO']!r}; it must come from a "
            f"repository variable so a different target repo is a settings change"
        )
        assert "secrets." in str(env["GITHUB_TOKEN"]), (
            f"job {name} sets GITHUB_TOKEN={env['GITHUB_TOKEN']!r}; a token must "
            f"come from secrets, never from a literal or a variable"
        )
        checked += 1
    assert checked, (
        "no job configures DEMO_REPO and GITHUB_TOKEN, so github_ops takes its "
        "offline branch on every stage and no pull request is ever opened"
    )


def test_nothing_switches_the_run_onto_a_fixture_or_an_offline_path():
    """Three knobs that each turn this workflow into a rehearsal, silently.

    OFFLINE=true short-circuits `_use_local()` regardless of the credentials.
    LLM_DISABLED=true puts every agent on its fixture. SCANNERS_REQUIRED is the
    inverse -- see the shared context: set on anything but the security runtime it
    blocks the clean half of the demo, and it is a RUNTIME setting, not this
    workflow's business.
    """
    doc = _doc()
    scopes = [("workflow env", doc.get("env") or {})]
    for job_name, job in _jobs().items():
        scopes.append((f"job {job_name} env", job.get("env") or {}))
        for step in job.get("steps") or []:
            scopes.append((f"job {job_name} step env", step.get("env") or {}))
    assert len(scopes) > 1, "only the workflow env was examined"

    for label, mapping in scopes:
        for knob in ("OFFLINE", "LLM_DISABLED"):
            value = str(mapping.get(knob, "")).lower()
            assert value != "true", (
                f"{label} sets {knob}=true, which puts this run on the local/"
                f"fixture path while every stage still reports green"
            )
        assert "SCANNERS_REQUIRED" not in mapping, (
            f"{label} sets SCANNERS_REQUIRED; that belongs on the security "
            f"RUNTIME only -- set here it would block the clean demo run"
        )


# --------------------------------------------------------------------------
# The trigger surface. This workflow spends money and writes to another repo.
# --------------------------------------------------------------------------


def test_the_only_trigger_is_manual_dispatch():
    """Set equality. A `push:` here would run the whole billable pipeline, and
    open a pull request on the target repository, on every commit to this one.
    """
    triggers = _triggers()
    assert set(triggers) == {"workflow_dispatch"}, (
        f"run-pipeline.yml triggers are {sorted(str(k) for k in triggers)}; only "
        f"workflow_dispatch may fire a run that invokes five runtimes and writes "
        f"a pull request"
    )


def test_the_dispatch_inputs_are_exactly_the_four_the_ingress_will_send():
    """Named and typed, because EventBridge dispatches this workflow by API.

    `POST /repos/{owner}/{repo}/actions/workflows/run-pipeline.yml/dispatches`
    sends `inputs` as JSON, and the REST API rejects real JSON booleans there --
    every value arrives as a STRING. So the declared `type: boolean` is for the
    UI, and the parsing on the other side has to accept 'true'/'false' as text.
    test_the_flag_parser_* below is that half.
    """
    inputs = _triggers()["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"ticket_id", "ticket_text", "poisoned", "auto_approve"}, (
        f"dispatch inputs are {sorted(inputs)}"
    )
    for name in ("ticket_id", "ticket_text"):
        assert inputs[name].get("type") == "string", (
            f"input {name} is type {inputs[name].get('type')!r}, expected string"
        )
        assert inputs[name].get("required") is True, f"input {name} is not required"
    for name in ("poisoned", "auto_approve"):
        assert inputs[name].get("type") == "boolean", (
            f"input {name} is type {inputs[name].get('type')!r}, expected boolean"
        )
    assert inputs["auto_approve"].get("default") is False, (
        f"auto_approve defaults to {inputs['auto_approve'].get('default')!r}; the "
        f"brief requires false, so a run asks its humans unless told otherwise"
    )


def test_every_job_has_a_sane_timeout():
    """The default is six hours, and a gate job WAITS for a human by design.

    So the caps here are doing two different things: on the agent jobs they bound
    a hung runtime invocation, and on the gate jobs they bound how long a run can
    sit holding a concurrency slot waiting for a click.
    """
    for name, job in _jobs().items():
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"job {name} has timeout-minutes={timeout!r}"
        )
        assert timeout <= 90, f"job {name} may run {timeout} minutes"


def test_the_workflow_serialises_runs_without_cancelling_one_mid_flight():
    """Cancelling a run mid-pipeline can leave a pull request open, a comment
    posted and a gate approved with no record of the outcome. Queue instead.
    """
    concurrency = _doc().get("concurrency")
    assert concurrency, "run-pipeline.yml sets no concurrency group"
    assert concurrency.get("cancel-in-progress") is False, (
        f"cancel-in-progress is {concurrency.get('cancel-in-progress')!r}; a "
        f"cancelled run can leave a PR open and a gate decided with no outcome"
    )


# --------------------------------------------------------------------------
# The stage script. Where the decisions actually live, so it is unit-tested.
# --------------------------------------------------------------------------


def _stage_module():
    """Import scripts/run_stage.py without making scripts/ a package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_stage_under_test", STAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("", False),
    ],
)
def test_the_flag_parser_reads_the_string_forms_the_dispatch_api_sends(raw, expected):
    """`workflow_dispatch` inputs arrive as STRINGS, including the booleans.

    The REST dispatch API the EventBridge target uses rejects real JSON booleans
    in `inputs`, and `${{ inputs.poisoned }}` interpolates into a run body as the
    text `true` or `false` regardless. So the string forms are the real contract,
    and this is the exact shape config.py:96-99 documents getting wrong:
    `bool("false")` is True, which would run the POISONED diff on a run somebody
    asked to be clean.
    """
    assert _stage_module().flag(raw) is expected


@pytest.mark.parametrize("raw", ["yes", "no", "1", "0", "on", "off", "maybe", " true "])
def test_the_flag_parser_refuses_anything_it_does_not_recognise(raw):
    """Fail closed and LOUDLY, rather than fall back to False.

    Falling back would make `poisoned=yes` -- a plausible thing for a human or a
    misconfigured input transformer to send -- run the clean diff while the
    operator believed they had asked for the poisoned one. That is a check that
    cannot tell "did not run" from "passed", which is the defect class this
    repository exists to prevent. Note ' true ' is refused too: a value with
    whitespace means something upstream is mangling the input.
    """
    with pytest.raises(ValueError, match="poisoned|auto_approve|flag|expected"):
        _stage_module().flag(raw)


def test_the_workflow_routes_its_boolean_inputs_through_that_parser():
    """Otherwise the test above pins a function nobody calls.

    The failure mode this closes is a workflow that tests the inputs in bash
    instead -- `if [ "$POISONED" = true ]` -- leaving the parser above as
    well-tested dead code. So: the boolean inputs must reach the stage script as
    arguments, and no run body may branch on them itself.
    """
    scripts = [
        (name, step.get("run"))
        for name, job in _jobs().items()
        for step in (job.get("steps") or [])
        if step.get("run")
    ]
    assert scripts, "no run bodies in run-pipeline.yml"

    passing = [
        (name, body)
        for name, body in scripts
        if "--poisoned" in body or "--auto-approve" in body
    ]
    assert passing, (
        "no run body passes --poisoned or --auto-approve to the stage script, so "
        "the flag parser is dead code and the inputs are read some other way"
    )
    for name, body in scripts:
        for bad in ('= true', '== true', '= "true"', '== "true"', "= 'true'"):
            assert bad not in body, (
                f"job {name} compares a value against {bad!r} in bash; the "
                f"boolean inputs must be parsed by run_stage.flag, which refuses "
                f"the values bash silently treats as false"
            )


def test_the_stage_script_is_invoked_for_every_stage_in_the_chain():
    """Ties the workflow to the script, per job.

    Without this, a job could quietly stop calling the script -- or call it with
    a stage name that does not exist -- and every structural test above would
    still pass, because they only look at permissions, artifacts and gates.
    """
    module = _stage_module()
    known = set(module.STAGES)
    assert known, "run_stage.STAGES is empty"

    invoked = {}
    for name, job in _jobs().items():
        for step in job.get("steps") or []:
            body = step.get("run") or ""
            if "run_stage.py" not in body:
                continue
            found = [stage for stage in known if re.search(rf"run_stage\.py\s+{stage}\b", body)]
            assert found, (
                f"job {name} calls run_stage.py with no recognised stage; "
                f"known stages are {sorted(known)}"
            )
            invoked.setdefault(name, set()).update(found)

    assert set(invoked) == set(STAGE_CHAIN), (
        f"jobs invoking run_stage.py are {sorted(invoked)}, expected all of "
        f"{sorted(STAGE_CHAIN)}"
    )


def test_the_stage_script_refuses_an_unknown_stage_rather_than_doing_nothing():
    """A typo'd stage must be an error, not a no-op that reports success.

    Run as a subprocess, because the property is the exit STATUS: a script that
    exits 0 on an unrecognised stage gives a green job for a stage that never
    ran.

    A NON-ZERO EXIT IS NOT ENOUGH TO ASSERT, and this was measured rather than
    reasoned about. Written as `assert returncode != 0` alone, this test PASSED
    before scripts/run_stage.py existed at all -- python exits 2 for a missing
    file, so the only thing it proved was that the file was absent. That is
    exactly rule 4, "a check that cannot distinguish 'did not run' from
    'passed'", occurring inside the test meant to enforce it. So the refusal is
    made attributable: argparse names the invalid choice AND lists the valid
    ones, and a missing file produces neither.
    """
    result = subprocess.run(
        [sys.executable, str(STAGE_SCRIPT), "not-a-stage", "--run-id", "x"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, f"run_stage.py exited 0 for an unknown stage:\n{combined}"
    assert "not-a-stage" in combined, (
        f"run_stage.py refused without naming what it refused, so this exit status "
        f"is not evidence the script ran at all:\n{combined}"
    )
    known = _stage_module().STAGES
    assert any(stage in combined for stage in known), (
        f"the refusal names none of the valid stages {sorted(known)}, so it may not "
        f"have come from the argument parser:\n{combined}"
    )


def test_a_block_verdict_is_reported_with_its_own_exit_code():
    """"Blocked by the deterministic rule" and "the job crashed" are different facts.

    Both fail the job, which is correct -- a blocked run must not reach gate2, and
    `needs` is what stops it. But they must be distinguishable in the log, or the
    poisoned demo run looks like a broken workflow rather than a working block
    rule. The two codes are what makes that visible.
    """
    module = _stage_module()
    assert module.EXIT_BLOCKED != 0, "a block must fail the job so gate2 is not reached"
    assert module.EXIT_BLOCKED != 1, (
        "a block shares its exit code with a crash, so the poisoned demo run is "
        "indistinguishable from a broken pipeline"
    )
