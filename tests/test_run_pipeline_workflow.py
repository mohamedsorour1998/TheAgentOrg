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

import argparse
import itertools
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

from agentorg import github_ops

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

# The rejection recorders: one per gate, each running when its gate job did NOT.
# GitHub SKIPS a job whose Environment a reviewer rejected, so nothing inside the
# gate job runs on a refusal and a branch in there could never record one. These
# jobs are the path that executes on rejection, and without them a refused run and
# an in-flight run are byte-identical on disk -- the bug agentorg/gates.py:16-20
# documents being fixed once already.
#
# They are the ONE place in this workflow where an outcome-ignoring `if:` is
# correct, because they RECORD that a run stopped rather than ADVANCING it past a
# gate. test_no_job_runs_regardless_of_whether_its_dependency_succeeded exempts
# exactly these names and no others.
REJECTION_RECORDER_JOBS = ["gate1-rejected", "gate2-rejected", "gate3-rejected"]

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


def test_the_workflow_parses_and_holds_exactly_the_expected_jobs():
    """Set equality, not containment.

    Containment would let an extra job appear -- one holding credentials, or one
    quietly bypassing a gate -- while this stayed green.
    """
    jobs = _jobs()
    expected = set(STAGE_CHAIN) | set(REJECTION_RECORDER_JOBS)
    assert set(jobs) == expected, (
        f"run-pipeline.yml jobs are {sorted(jobs)}, expected {sorted(expected)}"
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

    `continue-on-error` IS COVERED HERE TOO, and it was added after a second
    surviving mutation. It is the same defect one keyword over: a job marked
    `continue-on-error: true` reports SUCCESS to everything that `needs` it even
    when it failed. Put on `develop`, a blocked poisoned run would sail into
    gate2; put on a gate job itself, a failed gate would no longer stop anything.
    Measured before the fix: `continue-on-error` appeared NOWHERE in `tests/` or
    `.github/workflows/` in the whole repository, and 148 tests passed with it set
    on `develop` and again with it set on `gate2`.

    THE ONE LEGITIMATE EXEMPTION is the rejection recorders, and it is narrow by
    name rather than by pattern. They exist BECAUSE GitHub skips a rejected gate
    job, so their whole purpose is to run when their dependency did not -- they
    RECORD that a run stopped rather than ADVANCING it past a gate. Exempting them
    by exact name means a new job cannot inherit the exemption by looking similar.
    """
    # `success()` is the default and needs no `if:` at all. Anything in this set
    # detaches a job from its dependency's outcome.
    outcome_ignoring = ("always(", "failure(", "cancelled(", "!cancelled(")
    jobs = _jobs()
    assert jobs, "no jobs found; this test would check nothing"

    # Asserted non-empty, and asserted to be REAL jobs: an exemption list naming
    # jobs that do not exist would silently exempt nothing while looking like it
    # covered something -- and, worse, would let the recorders be deleted entirely
    # without this test noticing.
    exempt = set(REJECTION_RECORDER_JOBS)
    assert exempt, "the exemption list is empty; it would exempt nothing"
    missing = exempt - set(jobs)
    assert not missing, (
        f"the exemption list names jobs that do not exist in the workflow: "
        f"{sorted(missing)}. Either the rejection recorders were removed -- in "
        f"which case a refused gate now leaves no record at all -- or they were "
        f"renamed and this list was not updated."
    )

    checked = 0
    for name, job in jobs.items():
        if name in exempt:
            continue
        condition = str(job.get("if") or "")
        for token in outcome_ignoring:
            assert token not in condition, (
                f"job {name} carries `if: {condition}`, which contains {token!r}: "
                f"it runs even when what it `needs` failed or was skipped. Every "
                f"gate upstream of it becomes advisory -- a rejected gate2 or a "
                f"blocked develop would no longer stop the run."
            )
        # `continue-on-error: true` makes a FAILED job report success to every job
        # that needs it, which defeats `needs` from the other direction.
        assert job.get("continue-on-error") in (None, False), (
            f"job {name} sets continue-on-error="
            f"{job.get('continue-on-error')!r}: it reports SUCCESS to everything "
            f"that `needs` it even when it failed. A blocked develop would reach "
            f"gate2, and a failed gate would stop nothing."
        )
        checked += 1
    assert checked, "every job was exempt; this test checked nothing"


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

    bodies = 0
    for job_name, job in _jobs().items():
        for step in job.get("steps") or []:
            script = step.get("run") or ""
            bodies += 1
            for name in KEY_ENV_NAMES:
                assert name not in script, f"job {job_name} run body references {name}"
            assert "secrets.AWS" not in script, (
                f"job {job_name} run body reads an AWS secret; OIDC needs none"
            )

    # The `len(scopes) > 1` guard above covers the env loop and NOTHING ELSE: it is
    # satisfied by one job merely existing, while this second loop needs a job with
    # STEPS. With no steps anywhere, the assertions above would run zero times and
    # this test would report green having read no run body at all -- the
    # guard-on-A-iterate-B shape a vacuity sweep found elsewhere in this file.
    # Counted rather than assumed, because a universal negative passes loudest
    # when it examines nothing.
    assert bodies, "no run bodies were examined; this half of the test was vacuous"


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
    # EVERY job configures the seam, and `promote` is no longer an exception.
    #
    # It was exempted here with the reason "it posts nothing -- it writes a status
    # and a log row", which was true when written and became false the moment
    # promote started calling `github_ops.merge_pr`. The exemption then hid the
    # exact defect this test exists to catch.
    #
    # MEASURED 2026-08-22, run 32558114927: all seven jobs green, `status=promoted`
    # recorded, and NO pull request merged on the target repository. Without
    # DEMO_REPO and GITHUB_TOKEN, `_use_local()` takes the offline branch and
    # `merge_pr` returns `local://<branch>` -- a ref that reads like a success and
    # merges nothing.
    #
    # An exemption list is a standing invitation to this failure: the entry outlives
    # the reason for it, silently. If a future job genuinely needs no seam, prove it
    # with an assertion about what that job does rather than by name.
    must_configure_github = set(_jobs())
    assert must_configure_github, "no jobs found; this test would check nothing"

    configured = {
        name for name in _jobs()
        if "DEMO_REPO" in _effective_env(name) or "GITHUB_TOKEN" in _effective_env(name)
    }
    assert configured == must_configure_github, (
        f"jobs configuring the GitHub seam are {sorted(configured)}, expected "
        f"{sorted(must_configure_github)}. A stage missing it posts its output "
        f"nowhere -- github_ops takes the offline branch and every comment "
        f"degrades to a local:// ref while the job still reports green."
    )

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


def test_the_dispatch_inputs_are_exactly_the_five_the_ingress_will_send():
    """Named and typed, because EventBridge dispatches this workflow by API.

    `POST /repos/{owner}/{repo}/actions/workflows/run-pipeline.yml/dispatches`
    sends `inputs` as JSON, and the REST API rejects real JSON booleans there --
    every value arrives as a STRING. So the declared `type: boolean` is for the
    UI, and the parsing on the other side has to accept 'true'/'false' as text.
    test_the_flag_parser_* below is that half.

    `trigger` added 2026-08-22, `type: string` and default `manual`. It is the
    only field that can record that a run was started by an opened issue --
    EventBridge dispatches through the same REST API `gh workflow run` uses, so
    `github.event_name` reads `workflow_dispatch` either way. Its default must
    differ from what the ingress sends; tests/test_trigger_provenance.py asserts
    that, because identical values would make the field prove nothing.
    """
    inputs = _triggers()["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "ticket_id", "ticket_text", "poisoned", "auto_approve", "trigger",
    }, f"dispatch inputs are {sorted(inputs)}"
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

    expected = set(STAGE_CHAIN) | set(REJECTION_RECORDER_JOBS)
    assert set(invoked) == expected, (
        f"jobs invoking run_stage.py are {sorted(invoked)}, expected all of "
        f"{sorted(expected)}"
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


# --------------------------------------------------------------------------
# The stage script's BEHAVIOUR, executed rather than inspected.
#
# Everything above reads the workflow as data. These run the stage functions,
# because the three defects they pin -- a rejection that leaves no record, a
# missing state file that starts a fresh run, and a cloud path that posts one
# comment where the local path posts eight -- are all invisible to a YAML parse.
# --------------------------------------------------------------------------


def _cloud_run(monkeypatch, tmp_path, *, poisoned="false", reject_at=None,
               comment_ref=None, never_approves=False):
    """Drive the stage script end to end the way the workflow does, in-process.

    Returns (posted_bodies, final_state). `reject_at` names a gate whose
    Environment the reviewer refuses -- modelled the way GitHub actually behaves:
    the gate job is SKIPPED and its rejection recorder job runs instead. That
    distinction is the whole of CRITICAL 2, so the harness has to reproduce it
    rather than call the gate stage with a flag.

    `runs/` is redirected at tmp_path so a test never writes into the repository,
    and both state and log go to the same place -- gates.py and log.py each own
    their own module-level directory constant.
    """
    module = _stage_module()

    monkeypatch.setattr(module.gates, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(module.log, "_LOG_DIR", tmp_path)

    posted: list[str] = []

    def _record(state, body, finding=None):
        posted.append(body)
        # `comment_ref` exists so a test can choose a DELIVERED (https://) ref.
        # The default stays local:// -- github_ops.post_comment returns the
        # comment's https:// url when the body reached the PR and
        # comment://<run_id> when it did not, and a harness that can only
        # produce one of those two cannot tell them apart.
        if comment_ref is not None:
            return comment_ref
        return f"local://captured/{len(posted)}"

    def _record_note(state, body, destination, ref):
        """`report_outcome`'s offline writer, which does NOT go through post_comment.

        Same reason as `_capture` in tests/test_agent_comments.py: the outcome comment
        always means the ISSUE, so it bypasses `_destination` -- and a harness patched
        only at `post_comment` stops seeing the run's ending while every stage comment
        still records, which reads as "no outcome was posted".
        """
        return _record(state, body)

    # Patched on github_ops as a module attribute, because that is how graph.py's
    # comment helpers reach it -- resolved at call time.
    monkeypatch.setattr(github_ops, "post_comment", _record)
    monkeypatch.setattr(github_ops, "_note_locally", _record_note)

    if never_approves:
        # The SHIPPED fixtures approve on the first pass -- measured:
        # revision_count=0, one develop comment, one review comment. So without
        # this branch the harness CANNOT produce a multi-pass run, and
        # "each pass captures its OWN results" (run_stage.py:316-319) is
        # satisfied in shape while being untestable in substance. The local path
        # already learned this: tests/test_agent_comments.py:283-289 records the
        # mutation that survived its whole file until per-pass markers existed.
        #
        # Imported from the local path's test rather than restated so the two
        # cannot drift on the thing that makes them discriminating.
        from test_agent_comments import _developer_per_pass, _never_approves

        from agentorg.agents import developer, reviewer
        monkeypatch.setattr(reviewer, "run", _never_approves)
        monkeypatch.setattr(developer, "run", _developer_per_pass)

    def args(**kw):
        base = {
            "run_id": "", "ticket_id": "", "ticket_text": "",
            "poisoned": "false", "auto_approve": "false", "approver": "reviewer-1",
        }
        base.update(kw)
        return argparse.Namespace(**base)

    rc = module.STAGES["plan"](args(ticket_id="DEMO-1", ticket_text="Add a per-IP login rate limit."))
    assert rc == module.EXIT_OK, f"plan stage exited {rc}"
    run_id = next(p.stem.removesuffix(".state") for p in tmp_path.glob("*.state.json"))

    for stage in STAGE_CHAIN[1:]:
        if stage == reject_at:
            # GitHub skips the gate job; the recorder runs in its place.
            rc = module.STAGES[f"{stage}-rejected"](args(run_id=run_id))
            assert rc == module.EXIT_REJECTED, f"{stage} recorder exited {rc}"
            break
        rc = module.STAGES[stage](args(run_id=run_id, poisoned=poisoned))
        if rc != module.EXIT_OK:
            break

    state = module.RunState.model_validate_json(
        (tmp_path / f"{run_id}.state.json").read_text()
    )
    # `rc` is the exit code of the LAST stage that ran -- the one that stopped the
    # chain. Returned rather than discarded because "the run stopped" and "the run
    # stopped WITH THE RIGHT CODE" are different facts, and only the second one
    # keeps a block distinguishable from a crash.
    return posted, state, rc


# One comment per stage on a run that goes all the way through is
# _PROMOTED_RUN_COMMENTS, imported from the local path's test. A run whose
# reviewer NEVER approves is a different shape and needs its OWN expectation:
# the promoted dict declares `develop: 1, review: 1`, so it cannot describe a
# capped run and must not be stretched to try. See the multi-pass test below.
#
# `outcome` is here for the same reason it is in the promoted dict: `_emit` posts the
# run's ending on every terminal status, and a capped run is terminal (`failed`). An
# ending posted only on the happy path would be missing from exactly the runs a reader
# most needs it on -- the ones that did not ship.
_CAPPED_RUN_STAGE_COMMENTS = {"plan", "gate1", "develop", "review", "security",
                              "outcome"}


def test_the_cloud_paths_revision_loop_renders_each_pass_not_the_last_one(
    monkeypatch, tmp_path
):
    """Each flushed comment must carry ITS OWN pass's diff, not the final one.

    `scripts/run_stage.py:316-319` warns about this exact failure in prose --
    "re-reading would render the same diff into all three attempt comments --
    append in shape, replace in substance, green either way" -- copied across
    from `graph.py`. THE WARNING WAS COPIED; THE TEST THAT ENFORCES IT WAS NOT.

    MEASURED: replacing the captured per-pass results at the flush loop with
    `state.dev` / `state.review` survived all 793 tests, because the SHIPPED
    fixture reviewer approves on pass 1 -- so `loop_results` was always length 1
    and there was no second pass to render wrongly. A harness that cannot vary
    cannot tell appending from overwriting, which is Task 4's mutation E one path
    over.

    WHY THE SHARED CONSTANT COULD NOT CATCH IT, and this is the point:
    `_PROMOTED_RUN_COMMENTS` is what keeps the two paths from drifting (a stage
    added to one fails until the other posts it), but it declares
    `develop: 1, review: 1` -- so the only run shape that discriminates here
    would fail that equality. The shared definition is a real control and a blind
    spot in the same line. Hence the separate expectation above.
    """
    from test_agent_comments import _PASS_MARKER, _by_stage

    assert _PASS_MARKER, "the shared per-pass marker is empty"

    posted, state, _rc = _cloud_run(monkeypatch, tmp_path, never_approves=True)

    assert state.revision_count >= 2, (
        f"this test needs a MULTI-PASS run to mean anything; got "
        f"revision_count={state.revision_count}. Without more than one pass, "
        f"rendering 'the last pass' and 'its own pass' are the same string."
    )

    grouped = _by_stage(posted)
    assert "unlabelled" not in grouped, (
        f"comments with no stage label: {[b[:80] for b in grouped.get('unlabelled', [])]}"
    )
    assert set(grouped) == _CAPPED_RUN_STAGE_COMMENTS, (
        f"a capped cloud run posted for {sorted(grouped)}, expected "
        f"{sorted(_CAPPED_RUN_STAGE_COMMENTS)}"
    )

    develop = grouped["develop"]
    passes = state.revision_count + 1
    assert len(develop) == passes, (
        f"{passes} developer passes ran but {len(develop)} develop comments were "
        f"posted; the loop replaced rather than appended"
    )

    # THE DISCRIMINATOR. Each body must carry its own marker and NO other pass's.
    for n, body in enumerate(develop, start=1):
        assert f"{_PASS_MARKER}{n}\n" in body, (
            f"develop comment {n} does not carry pass {n}'s diff -- it rendered "
            f"another pass's, which is 'append in shape, replace in substance'"
        )
        others = [m for m in range(1, passes + 1) if m != n]
        assert not [m for m in others if f"{_PASS_MARKER}{m}\n" in body], (
            f"develop comment {n} also carries another pass's diff: "
            f"{[m for m in others if f'{_PASS_MARKER}{m}' in body]}"
        )


def test_a_blocked_cloud_run_exits_with_the_block_code_and_logs_a_delivered_ref(
    monkeypatch, tmp_path
):
    """The poisoned run's two facts: the code it exits with, and what it recorded.

    Both were unpinned. `_cloud_run` accepted a `poisoned=` argument that NO
    CALLER EVER PASSED, so the blocked branch of `_stage_develop` had never been
    executed by a test at all. MEASURED, two separate survivors:

      * `return EXIT_BLOCKED` -> `return EXIT_OK` survived all 793. The only
        existing check asserts the CONSTANT (`EXIT_BLOCKED != 0`, `!= 1`), never
        that the branch returns it -- while `EXIT_REJECTED` IS asserted against an
        executed stage. Parallel code, non-parallel coverage.
      * `artifact_ref=ref` -> a hardcoded `comment://` survived all 793. That is
        the same property `graph.py`'s R8 step pins on the local path, where it
        takes four tests red.

    WHY THE REF MATTERS MORE THAN IT LOOKS: `post_comment` cannot raise, so a
    delivery failure leaves no trace unless it is recorded. The log row is the
    artifact, and without a truthful ref `runs/<run_id>.jsonl` is byte-identical
    whether the block reason reached the PR or evaporated. A run that shows a
    block on screen while the log says nobody was told is the worst available
    outcome on the surface the judges read.
    """
    delivered = "https://github.com/o/r/pull/1#issuecomment-999"
    _posted, state, rc = _cloud_run(
        monkeypatch, tmp_path, poisoned="true", comment_ref=delivered
    )
    module = _stage_module()

    assert state.status == "blocked", (
        f"the poisoned run ended status={state.status!r}; this test is about a "
        f"run the deterministic block rule stopped"
    )
    assert rc == module.EXIT_BLOCKED, (
        f"the blocked stage exited {rc}, not EXIT_BLOCKED "
        f"({module.EXIT_BLOCKED}). EXIT_OK here would let gate2 run on a run the "
        f"scanners refused; 1 would make it indistinguishable from a crash."
    )

    blocked = [e for e in module.log.read(state.run_id)
               if e.stage == "security" and e.action == "blocked"]
    assert blocked, (
        "no security/blocked row was logged for a blocked run, so the refusal "
        "exists nowhere a reader of the run can find it"
    )
    assert any(e.artifact_ref == delivered for e in blocked), (
        f"the block was DELIVERED as {delivered!r} but no log row carries that "
        f"ref: {[e.artifact_ref for e in blocked]}. A run whose block reached the "
        f"PR must not be recorded as one where nobody was told."
    )


def test_the_cloud_path_posts_the_same_per_stage_comments_as_the_local_path(
    monkeypatch, tmp_path
):
    """IMPORTANT 4: the cloud path used to post ONE comment where local posts eight.

    Measured before the fix: `graph.py` defines `_plan_comment`, `_gate_comment`,
    `_develop_comment`, `_review_comment`, `_security_comment` and `_sre_comment`,
    and `scripts/run_stage.py` called NONE of them -- its only comment call was a
    bare `github_ops.post_comment(state, state.security.explanation)`. So on the
    demo's own surface the PR would show no gate approvals, no revision loop and
    no SRE go/no-go: exactly the timeline the demo is built around, missing.

    THE EXPECTED SET IS IMPORTED FROM THE LOCAL PATH'S OWN TEST rather than
    restated here, which is the point of this test. Restating it would let the two
    paths drift apart again while both files stayed green -- each asserting against
    its own idea of what a run posts. Sharing one definition means a stage added to
    the local path fails here until the cloud path posts it too.
    """
    from test_agent_comments import _PROMOTED_RUN_COMMENTS, _by_stage

    assert _PROMOTED_RUN_COMMENTS, "the shared expectation is empty"

    posted, state, _rc = _cloud_run(monkeypatch, tmp_path)
    assert state.status == "promoted", (
        f"this test is about a run that finishes; got status={state.status!r}"
    )

    grouped = _by_stage(posted)
    missing = [stage for stage in _PROMOTED_RUN_COMMENTS if stage not in grouped]
    assert not missing, (
        f"the CLOUD path posted nothing for these stages: {missing}. The local "
        f"path posts all of {sorted(_PROMOTED_RUN_COMMENTS)}, so the PR a judge "
        f"reads would be missing exactly these."
    )
    assert "unlabelled" not in grouped, (
        f"comments with no stage label: {[b[:80] for b in grouped.get('unlabelled', [])]}"
    )

    counts = {stage: len(bodies) for stage, bodies in grouped.items()}
    assert counts == _PROMOTED_RUN_COMMENTS, (
        f"cloud path posted {counts}, local path posts {_PROMOTED_RUN_COMMENTS}"
    )

    # A LABEL IS NOT OUTPUT. Every value below is read off the RunState the run
    # produced, so none of these strings is one this test invented -- otherwise
    # eight bare headers would satisfy the counts above.
    assert state.plan.tasks[0] in grouped["plan"][0]
    assert state.dev.summary in grouped["develop"][0]
    assert state.review.verdict in grouped["review"][0]
    assert state.security.explanation in grouped["security"][0]
    assert state.sre.verdict.upper() in grouped["sre"][0]
    assert state.decisions[0].decision.upper() in grouped["gate1"][0]

    assert len(set(posted)) == len(posted), (
        "some comments are byte-identical; posting one string repeatedly is the "
        "cheapest way to satisfy a presence check"
    )


def test_a_rejected_gate_is_recorded_on_disk_and_the_run_stops():
    """CRITICAL 2: `gates.py:16-20`'s own fixed bug, reintroduced by the cloud path.

    That file says, verbatim:

        Before it did, every finished run still read status="running" with its
        last decision missing -- so a run the graph had REJECTED could be resumed
        and approved, because nothing on disk said it was over.

    `_stage_gate` hardcodes `decision="approved"`, and it cannot be otherwise:
    GitHub SKIPS a job whose Environment a reviewer rejected, so no branch inside
    that job ever executes on a refusal. Before the rejection recorders existed, a
    refused run and an in-flight run were byte-identical on disk -- both
    `status="running"`, both carrying no decision for the gate -- and the refusal
    existed nowhere but a greyed-out job in the Actions UI. On the one surface
    whose entire purpose is that human gates hold, that is the worst available
    defect, because the run could then be resumed and approved.

    Parametrised over all three gates in the loop below rather than testing gate2
    alone: the recorder is per-gate wiring, and two of three working is the shape
    that reads as done.
    """
    for gate in GATE_JOBS:
        with (
            tempfile.TemporaryDirectory() as tmp,
            pytest.MonkeyPatch.context() as mp,
        ):
            posted, state, _rc = _cloud_run(mp, Path(tmp), reject_at=gate)

        assert state.status == "rejected", (
            f"after {gate} was refused the state reads status={state.status!r}. "
            f"'running' here is indistinguishable from a run still in flight, "
            f"which is the gates.py bug quoted above."
        )
        refusals = [d for d in state.decisions if d.decision == "rejected"]
        assert refusals, (
            f"{gate} was refused and no rejected HumanDecision was written; "
            f"state.decisions holds {[(d.gate, d.decision) for d in state.decisions]}"
        )
        assert refusals[-1].gate == gate, (
            f"the refusal was recorded against {refusals[-1].gate!r}, not {gate!r}"
        )

        # The refusal reaches the surface a judge reads, not only the disk.
        from test_agent_comments import _by_stage
        grouped = _by_stage(posted)
        assert gate in grouped, (
            f"{gate} was refused and posted no comment; the PR would show the run "
            f"simply stopping, with no record of who refused it or why"
        )
        assert "REJECTED" in grouped[gate][-1], (
            f"the {gate} comment does not say it was rejected: {grouped[gate][-1][:200]!r}"
        )

        # And the run stopped: no stage after the refused gate recorded anything.
        later = STAGE_CHAIN[STAGE_CHAIN.index(gate) + 1:]
        for stage in later:
            assert stage not in grouped, (
                f"{gate} was refused but {stage} still posted, so the run carried "
                f"on past the refusal"
            )


def test_a_missing_state_file_is_refused_rather_than_starting_a_fresh_run():
    """CRITICAL 3: `_load`'s refusal was correct but pinned by nothing.

    Measured: making `_load` fall back to a fresh `RunState` survived all 735
    tests. That fallback is the artifact-handoff defect in its most expensive
    form -- a stage whose upload published nothing, or whose download was removed,
    would silently begin a NEW run and report success for work nobody planned,
    discarding every approval already given.

    Asserted as SystemExit specifically, and with the message checked, because an
    exception type alone does not distinguish "refused deliberately" from "crashed
    while reading a file".
    """
    module = _stage_module()
    with (
        tempfile.TemporaryDirectory() as tmp,
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(module.gates, "_STATE_DIR", Path(tmp))
        with pytest.raises(SystemExit) as excinfo:
            module._load("a-run-that-was-never-saved")

    message = str(excinfo.value)
    assert "no state file" in message, (
        f"the refusal does not say what was missing: {message!r}"
    )
    assert "a-run-that-was-never-saved" in message, (
        f"the refusal does not name the run it could not find: {message!r}"
    )


def test_every_gate_has_a_rejection_recorder_wired_to_it():
    """The workflow half of CRITICAL 2, so the recorders cannot be silently dropped.

    Reads the parsed jobs. A recorder that exists in `run_stage.py` but has no job
    calling it records nothing, and the behavioural test above would still pass
    because it invokes the stage function directly.
    """
    module = _stage_module()
    mapping = module.REJECTION_STAGES
    assert mapping, "run_stage.REJECTION_STAGES is empty"
    assert set(mapping) == set(GATE_JOBS), (
        f"rejection stages cover {sorted(mapping)}, expected {sorted(GATE_JOBS)}"
    )

    jobs = _jobs()
    for gate, stage in sorted(mapping.items()):
        job_name = f"{gate}-rejected"
        assert job_name in jobs, (
            f"gate {gate} has no {job_name} job, so a reviewer refusing it leaves "
            f"no record anywhere -- see gates.py:16-20"
        )
        job = jobs[job_name]

        needs = _needs(job)
        assert gate in needs, (
            f"job {job_name} needs {needs}, which does not include {gate}; it "
            f"cannot know whether that gate was refused"
        )

        # It MUST carry an outcome-ignoring condition: a recorder that only runs on
        # success is a recorder that never runs, since the gate it records is one
        # GitHub skipped.
        condition = str(job.get("if") or "")
        assert "always()" in condition, (
            f"job {job_name} has `if: {condition!r}`; without always() it is "
            f"skipped alongside the gate it exists to record"
        )
        assert f"needs.{gate}.result" in condition, (
            f"job {job_name} has `if: {condition!r}`, which does not test "
            f"{gate}'s result -- so it would also fire on a successful approval "
            f"and record a refusal that never happened"
        )

        # And it must actually call its stage.
        bodies = [s.get("run") or "" for s in (job.get("steps") or [])]
        assert any(f"run_stage.py {stage}" in b for b in bodies), (
            f"job {job_name} never invokes `run_stage.py {stage}`"
        )

        # No credentials: recording a refusal reaches no runtime.
        perms = job.get("permissions") or {}
        assert perms.get("id-token") != "write", (
            f"job {job_name} holds id-token: write; recording a refusal needs none"
        )


def test_each_rejection_recorder_requires_the_stage_before_its_gate_to_have_succeeded():
    """A SKIPPED gate does not mean a human refused it. This is the discriminator.

    MEASURED, run 32509257195 of this workflow, on the POISONED ticket:

        plan=success gate1=success develop=failure gate1-rejected=skipped
        gate2-rejected=failure gate2=skipped gate3-rejected=failure
        sre=skipped gate3=skipped promote=skipped

    `develop=failure` was CORRECT -- EXIT_BLOCKED, the deterministic rule blocking
    the poisoned diff, with `status=blocked` written to the state. Then two things
    went wrong, both from the same missing clause:

      * gate2 was SKIPPED because it `needs: develop`. The recorder's condition
        was `always() && needs.gate2.result != 'success'`, and 'skipped' is not
        'success', so it FIRED. It called `gates.resume`, which wrote
        `status=rejected` OVER `status=blocked`, attributed to a github.actor who
        never saw a gate. The block -- the one thing that demo beat exists to
        show -- was erased by the job written to preserve refusals.
      * gate3-rejected fired the same way with `sre` skipped, so artifact
        `run-state-<id>-sre` did not exist and the job died in
        download-artifact: a red job on the demo screen that recorded nothing.

    The root cause is that `needs.<gate>.result` alone CANNOT tell those apart. A
    rejected Environment makes GitHub skip its job, and a gate the run never
    reached is skipped too -- one observation, two meanings, which is rule 4 of
    this repository ("denied" versus "not ready yet") in workflow form. The stage
    BEFORE the gate is the field that separates them: if it succeeded, the only
    remaining reason the gate did not run is the human.

    WHY THE PRECEDING STAGE IS DERIVED FROM `STAGE_CHAIN` and not written out as a
    second `{gate: stage}` map: a hardcoded copy is exactly where two definitions
    drift. `STAGE_CHAIN` is already the declared literal this file checks the
    workflow's `needs` against, so the gate's predecessor in it IS the stage
    whose success is being demanded -- and reordering the chain moves both at
    once.

    `test_every_gate_has_a_rejection_recorder_wired_to_it` above asserts
    `always()` and `needs.<gate>.result`; this adds only the clause that was
    missing, and it does NOT contradict
    `test_no_job_runs_regardless_of_whether_its_dependency_succeeded`, which
    exempts these three jobs by exact name: `always()` must stay, because a
    recorder that runs only on success never runs at all. What is asserted here is
    that `always()` is NARROWED by the upstream result, not removed.
    """
    jobs = _jobs()

    # The gate -> preceding stage map, derived. Asserted non-empty and asserted to
    # cover all three gates, so a STAGE_CHAIN edit that puts a gate first (making
    # it have no predecessor) fails here rather than silently checking two gates.
    preceding = {
        later: earlier
        for earlier, later in itertools.pairwise(STAGE_CHAIN)
        if later in GATE_JOBS
    }
    assert set(preceding) == set(GATE_JOBS), (
        f"derived a preceding stage for {sorted(preceding)}, expected all of "
        f"{sorted(GATE_JOBS)}; STAGE_CHAIN is {STAGE_CHAIN}, so either a gate was "
        f"reordered to the front of the chain or it left the chain entirely -- "
        f"and this test would otherwise check one fewer recorder"
    )

    checked = 0
    for gate, stage in sorted(preceding.items()):
        job_name = f"{gate}-rejected"
        assert job_name in jobs, (
            f"gate {gate} has no {job_name} job; see "
            f"test_every_gate_has_a_rejection_recorder_wired_to_it"
        )
        job = jobs[job_name]
        condition = str(job.get("if") or "")

        # 1. The `if:` demands the preceding stage SUCCEEDED. Compared against
        #    both quotings GitHub accepts, so a switch to double quotes is not a
        #    silent failure.
        wanted = [
            f"needs.{stage}.result == 'success'",
            f'needs.{stage}.result == "success"',
        ]
        assert any(clause in condition for clause in wanted), (
            f"job {job_name} has `if: {condition}`, which does not require "
            f"needs.{stage}.result == 'success'. Without it a {gate} that was "
            f"SKIPPED because {stage} failed or was skipped reads identically to "
            f"one a human refused, and this job writes status=rejected over "
            f"whatever the run actually ended as -- measured on run 32509257195, "
            f"where it erased a status=blocked poisoned run."
        )

        # 2. And the job NEEDS it. A condition may reference any job in the
        #    workflow, but `needs.<job>.result` for a job this one does not `needs`
        #    evaluates against nothing -- GitHub renders it as an empty/skipped
        #    result, so the clause above would be permanently false and the
        #    recorder would never fire on a real refusal either. Separate
        #    assertion with its own message because it is a separate defect:
        #    assertion 1 passing while this one fails is a recorder that is now
        #    dead rather than one that over-fires.
        needs = _needs(job)
        assert stage in needs, (
            f"job {job_name} tests needs.{stage}.result but needs {needs}, which "
            f"does not include {stage}. A condition referencing a job this one "
            f"does not `needs` always evaluates against `skipped`, so the clause "
            f"is never true and this recorder never fires -- a refused {gate} "
            f"would leave no record at all, which is the gates.py:16-20 bug back."
        )

        # 3. The artifact it downloads is that same stage's. The recorder reads
        #    the state the preceding stage uploaded, so if the two disagree the
        #    job dies in download-artifact -- which is exactly how gate3-rejected
        #    failed on run 32509257195, with `Artifact not found for name:
        #    run-state-32509257195-sre`.
        downloaded = _downloaded_names(job)
        assert downloaded, (
            f"job {job_name} downloads no named artifact, so it has no state to "
            f"record a refusal against and would start from nothing"
        )
        expected = set(_uploaded_names(jobs[stage]))
        assert expected, (
            f"job {stage} uploads no named artifact, so {job_name} has nothing "
            f"to read; this assertion would otherwise compare against an empty set"
        )
        assert set(downloaded) & expected, (
            f"job {job_name} downloads {sorted(downloaded)} but {stage} -- the "
            f"stage whose success it now requires -- uploads {sorted(expected)}. "
            f"The recorder must read the artifact of the stage it gates on, or it "
            f"dies in download-artifact and records nothing."
        )
        checked += 1

    assert checked == len(GATE_JOBS), (
        f"checked {checked} rejection recorders, expected {len(GATE_JOBS)}"
    )


def test_the_run_id_guard_fires_when_the_plan_stage_prints_no_run_id():
    """MINOR 7: the guard was unreachable in the exact case its message describes.

    MEASURED, by running the shipped script rather than reading it. Under
    `set -euo pipefail`, a `stage.log` with no `run_id=` line makes grep exit 1 and
    kill the step at that line -- so the step failed, but the guard never ran and
    its `::error::` diagnostic never printed. Probed against the original:

        exit=1, and "GUARD FIRED" never printed

    The guard therefore fired only for a literal empty `run_id=` line, never for
    the missing-line case. That is the "cannot distinguish did-not-run from
    passed" shape one level up: the failure and the diagnostic came apart, so a
    plan stage that printed nothing looked like a crashed grep.

    This EXECUTES the workflow's own run body -- extracted from the YAML, not
    retyped -- against a log with no run_id, and requires the diagnostic in the
    output. Retyping the script here would pin a copy and let the real one drift.
    """
    body = None
    for step in _steps(_job("plan")):
        if "run_stage.py plan" in (step.get("run") or ""):
            body = step["run"]
    assert body, "no step in the plan job invokes run_stage.py plan"

    # Replace the python invocation with a stub that WRITES a log carrying no
    # run_id line, then exits 0. Everything else runs as shipped.
    #
    # `printf > stage.log`, NOT `echo | tee stage.log`, and the difference is the
    # whole validity of this test. A first version used the tee form and the
    # mutation SURVIVED: `tee` is the last command of that pipeline, it succeeds,
    # and with pipefail the pipeline's status is taken from the last failing
    # command -- so the stub's own pipeline masked nothing, but it also meant the
    # script under test no longer resembled the shipped one at the point that
    # matters. Traced with `bash -x` to find it: under pipefail the ASSIGNMENT
    # `run_id="$(grep ... | cut ...)"` inherits status 1 when grep matches
    # nothing, which is what kills the step before the guard. A stub that cannot
    # reproduce that assignment cannot test the guard.
    lines = body.splitlines()
    patched = []
    skipping = False
    for line in lines:
        if line.strip().startswith("python scripts/run_stage.py plan"):
            patched.append(
                'printf "planner ran but printed no run_id\\n" > stage.log'
            )
            skipping = line.rstrip().endswith("\\")
            continue
        if skipping:
            skipping = line.rstrip().endswith("\\")
            continue
        patched.append(line)
    script = "\n".join(patched)
    assert "stage.log" in script and "GITHUB_OUTPUT" in script, (
        f"the extracted script does not look like the run_id plumbing:\n{script}"
    )

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "gh_output"
        out.touch()
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=tmp,
            env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(out)},
            check=False,
        )
        combined = result.stdout + result.stderr

        assert result.returncode != 0, (
            f"the step succeeded with no run_id; everything downstream would then "
            f"look for a run that does not exist:\n{combined}"
        )
        # THE POINT OF THIS TEST. A non-zero exit alone was already true before the
        # fix -- grep's own failure produced it. What was missing is the guard
        # SAYING so, which is the difference between a diagnosable failure and a
        # step that died at an unexplained grep.
        assert "printed no run_id" in combined, (
            f"the step failed without the guard's diagnostic, so the guard is "
            f"unreachable in the case it documents -- grep's exit status killed "
            f"the step first:\n{combined}"
        )
        assert not out.read_text().strip(), (
            f"an empty run_id was still written to GITHUB_OUTPUT: {out.read_text()!r}"
        )


# --------------------------------------------------------------------------
# The CODE-LEVEL half of the same guard, behind the workflow's `if:`.
#
# The workflow clause above is the real fix, and it is one line of YAML with no
# compiler and no test that can execute it. These execute the refusal.
# --------------------------------------------------------------------------


def test_the_recorder_refuses_to_overwrite_a_blocked_run(monkeypatch, tmp_path):
    """The measured defect, driven end to end: a real block, then the recorder.

    Run 32509257195 in full. `develop` on the POISONED ticket exited EXIT_BLOCKED
    with `status=blocked`; gate2 was skipped because it `needs: develop`; the
    recorder's `if:` fired on that `skipped` and `gates.resume` wrote
    `status=rejected` over the block, by a github.actor who never saw a gate.

    So the state this drives is not synthesised -- it is produced by running the
    poisoned pipeline through `_cloud_run` until the block rule stops it, exactly
    as `test_a_blocked_cloud_run_exits_with_the_block_code_and_logs_a_delivered_ref`
    does, and THEN invoking the recorder on it the way the broken workflow did.
    A test that hand-set `status="blocked"` on a fresh RunState would pass against
    a guard keyed on anything at all; this one requires the guard to survive a
    state the block rule really wrote.

    WHAT MUST HOLD AFTERWARDS is the point, and it is three separate facts:
    the status still says `blocked`, no rejected decision was appended, and the
    exit code is neither EXIT_OK (which would report the overwrite as a success)
    nor EXIT_REJECTED (which would claim a human refused a run nobody saw).
    """
    module = _stage_module()

    # A real poisoned run, stopped by the deterministic rule.
    _posted, blocked_state, rc = _cloud_run(monkeypatch, tmp_path, poisoned="true")
    assert blocked_state.status == "blocked", (
        f"this test needs a run the BLOCK RULE stopped; got "
        f"status={blocked_state.status!r}. Without a genuinely blocked state "
        f"there is no outcome for the recorder to overwrite and this test would "
        f"pin nothing."
    )
    assert rc == module.EXIT_BLOCKED, f"the poisoned run exited {rc}, not EXIT_BLOCKED"
    decisions_before = [(d.gate, d.decision) for d in blocked_state.decisions]
    assert not [d for d in decisions_before if d[1] == "rejected"], (
        f"the blocked run already carries a rejection {decisions_before}; the "
        f"overwrite this test looks for would then be invisible"
    )

    # Now the workflow's defect: gate2 was skipped, so its recorder ran.
    args = argparse.Namespace(
        run_id=blocked_state.run_id, ticket_id="", ticket_text="",
        poisoned="false", auto_approve="false", approver="mohamedsorour1998",
    )
    rc = module.STAGES["gate2-rejected"](args)

    after = module.RunState.model_validate_json(
        (tmp_path / f"{blocked_state.run_id}.state.json").read_text()
    )
    assert after.status == "blocked", (
        f"the recorder overwrote a blocked run with status={after.status!r}. That "
        f"is the measured defect from run 32509257195: the block -- the one thing "
        f"the poisoned demo beat exists to show -- erased by the job written to "
        f"preserve refusals."
    )
    assert [(d.gate, d.decision) for d in after.decisions] == decisions_before, (
        f"the recorder appended a decision to a run that had already ended: "
        f"{[(d.gate, d.decision) for d in after.decisions]}, was "
        f"{decisions_before}. status holding is not enough -- an appended "
        f"'gate2 rejected' renders on the timeline the judges read."
    )
    assert rc not in (module.EXIT_OK, module.EXIT_REJECTED), (
        f"the recorder exited {rc}: EXIT_OK would report the refusal-to-record as "
        f"an ordinary success, and EXIT_REJECTED would claim a human refused this "
        f"run -- the precise false claim the guard exists to prevent"
    )
    assert rc == module.EXIT_ALREADY_FINAL, (
        f"the recorder exited {rc}, expected EXIT_ALREADY_FINAL "
        f"({module.EXIT_ALREADY_FINAL})"
    )


@pytest.mark.parametrize("status", ["blocked", "promoted", "failed", "rejected"])
@pytest.mark.parametrize("gate", GATE_JOBS)
def test_no_recorder_records_a_refusal_on_a_run_that_already_ended(
    monkeypatch, tmp_path, gate, status
):
    """All four terminal statuses, all three recorders. Twelve combinations.

    `blocked` is what was measured, but it is not the only one that matters and
    the guard is deliberately not keyed on it: a `promoted` run is as over as a
    blocked one, and a recorder firing on a promoted run would write
    `status=rejected` onto a change that already shipped. `failed` is the
    revision-cap and SRE no-go ending; `rejected` is the double-record case, where
    firing twice appends a second refusal by a second actor.

    Parametrised over all three gates for the reason
    `test_a_rejected_gate_is_recorded_on_disk_and_the_run_stops` gives: the
    recorder is per-gate wiring and two of three working is the shape that reads
    as done.

    The state here IS hand-set, unlike the test above -- and that is the division
    of labour between them. Only the poisoned pipeline can produce a real
    `blocked`, but no run in this suite ends `promoted` AND has a gate left to
    record, so the other three endings cannot be reached any other way. The test
    above proves the guard survives a state the pipeline really wrote; this one
    proves it covers every ending the frozen contract can express.
    """
    module = _stage_module()
    _posted, state, _rc = _cloud_run(monkeypatch, tmp_path)

    state.status = status
    before = len(state.decisions)
    module.gates.save(state)

    args = argparse.Namespace(
        run_id=state.run_id, ticket_id="", ticket_text="",
        poisoned="false", auto_approve="false", approver="somebody",
    )
    rc = module.STAGES[f"{gate}-rejected"](args)

    after = module.RunState.model_validate_json(
        (tmp_path / f"{state.run_id}.state.json").read_text()
    )
    assert rc == module.EXIT_ALREADY_FINAL, (
        f"{gate}-rejected exited {rc} on a status={status!r} run, expected "
        f"EXIT_ALREADY_FINAL ({module.EXIT_ALREADY_FINAL})"
    )
    assert after.status == status, (
        f"{gate}-rejected changed a finished run's status from {status!r} to "
        f"{after.status!r}"
    )
    assert len(after.decisions) == before, (
        f"{gate}-rejected appended a decision to a status={status!r} run: "
        f"{[(d.gate, d.decision) for d in after.decisions]}"
    )
    refusals = [d for d in after.decisions if d.decision == "rejected"]
    assert not [d for d in refusals if d.gate == gate], (
        f"a rejection was recorded against {gate} on a run that ended as "
        f"{status!r}, so a reader cannot tell a human's refusal from a run that "
        f"stopped earlier -- which is the defect this whole file is about"
    )


def test_a_recorder_still_records_a_genuine_refusal_on_a_live_run():
    """The mirror image: a guard that refuses everything passes every test above.

    This is the over-refusal test, and it is not decoration -- the four statuses
    the guard knows are the four that are NOT "running", so a guard keyed one
    character wrong (`not in`, or a set including "running") turns every real
    rejection into a silent no-op and makes the gates non-binding in the opposite
    direction. Rather than restate that path, it defers to the behavioural test
    that already owns it, which drives all three gates through `_cloud_run` with
    `reject_at=` and asserts status, decision, gate attribution and the comment.

    Called directly so this file fails HERE, naming over-refusal, rather than
    only in a test whose name is about recording.
    """
    test_a_rejected_gate_is_recorded_on_disk_and_the_run_stops()


def test_the_recorders_terminal_statuses_are_every_ending_the_contract_has():
    """Derived from `RunState.status`, so a new ending is terminal on arrival.

    A hardcoded set would treat a fifth ending as "running" and let a recorder
    overwrite it -- the same defect one status later. Derived the same way
    `tests/test_approve_server.py:1068-1080` derives `approve_server._TERMINAL`,
    and asserted to EQUAL it: the two are separate literals in separate modules
    (approve_server does not import run_stage and must not), and two guards
    against one hazard that disagree about what "over" means is worse than one.
    """
    import typing

    from agentorg import approve_server

    module = _stage_module()
    hints = typing.get_type_hints(module.RunState, include_extras=False)
    statuses = set(typing.get_args(hints["status"]))
    assert statuses, "RunState.status is not a Literal; nothing was derived"

    assert module._TERMINAL_STATUSES == statuses - {"running"}
    assert module._TERMINAL_STATUSES == approve_server._TERMINAL, (
        f"run_stage says {sorted(module._TERMINAL_STATUSES)} is terminal and "
        f"approve_server says {sorted(approve_server._TERMINAL)}; two guards "
        f"against the same hazard must agree on what 'over' means"
    )


def test_the_already_final_exit_code_is_distinguishable_from_every_other_outcome():
    """Four facts, four codes, and 1 is spoken for by an uncaught exception.

    `run_stage.py:98-109` reasons this out for EXIT_BLOCKED: a block sharing a
    code with a crash makes the poisoned demo run indistinguishable from a broken
    workflow on the projector. The same argument is what forbids reusing any
    existing code here -- and EXIT_REJECTED most of all, since that code asserts a
    human refused the run, which is the false claim being refused.
    """
    module = _stage_module()
    codes = {
        "EXIT_OK": module.EXIT_OK,
        "EXIT_BLOCKED": module.EXIT_BLOCKED,
        "EXIT_REJECTED": module.EXIT_REJECTED,
        "EXIT_ALREADY_FINAL": module.EXIT_ALREADY_FINAL,
    }
    assert len(set(codes.values())) == len(codes), (
        f"two exit codes collide: {codes}. Each names a different fact about the "
        f"run, and a shared value makes two of them unreadable in a job log."
    )
    assert module.EXIT_ALREADY_FINAL not in (0, 1), (
        f"EXIT_ALREADY_FINAL is {module.EXIT_ALREADY_FINAL}: 0 would report the "
        f"refusal as an ordinary success and 1 is what an uncaught exception "
        f"already exits with"
    )


def test_the_refused_recorder_leaves_the_blocked_banner_on_the_timeline(
    monkeypatch, tmp_path
):
    """The guard's OWN log row must not erase the word the demo beat is judged on.

    FOUND BY RUNNING IT, not by reading it, and it is the same defect one layer
    out from the one being fixed. `timeline._outcome` (timeline.py:196-211) reads
    the banner off the action of the LAST log row -- never off `RunState.status`,
    which no row carries -- and `_OUTCOME` holds only promoted/blocked/rejected.
    So the guard's explanatory row, whose action is deliberately `opened` because
    the vocabulary has no word for "declined to write", became the last row:

        before the recorder ran:  ⛔ BLOCKED — the change was stopped
        after, with one row:      … INCOMPLETE — run stopped at gate2 without
                                     an ending

    The state file still said `blocked`. The projector said INCOMPLETE. A guard
    that preserves the evidence in a file while erasing it from the surface the
    judges read is worse than no guard, because it looks like it worked -- and
    every assertion in this file's other new tests reads the STATE, so all of them
    stayed green through it. That is this repository's own pattern: a check that
    cannot express the failing case.

    ASSERTED ON THE RENDERED TEXT, not on the log rows, because the rendering is
    what came apart. Asserting "a row with action='blocked' exists" would have
    passed against the bug too -- the security row already had one; what was wrong
    was which row came LAST.
    """
    from agentorg import timeline

    module = _stage_module()
    monkeypatch.setattr(timeline.log, "_LOG_DIR", tmp_path)

    _posted, state, rc = _cloud_run(monkeypatch, tmp_path, poisoned="true")
    assert state.status == "blocked", f"needs a blocked run; got {state.status!r}"
    assert rc == module.EXIT_BLOCKED, f"the poisoned run exited {rc}"

    before = timeline.render_text(state.run_id).splitlines()[1]
    assert "BLOCKED" in before, (
        f"the banner does not say BLOCKED before the recorder even runs, so this "
        f"test cannot detect the downgrade it exists for: {before!r}"
    )

    args = argparse.Namespace(
        run_id=state.run_id, ticket_id="", ticket_text="",
        poisoned="false", auto_approve="false", approver="mohamedsorour1998",
    )
    assert module.STAGES["gate2-rejected"](args) == module.EXIT_ALREADY_FINAL

    after = timeline.render_text(state.run_id).splitlines()[1]
    assert "BLOCKED" in after, (
        f"the recorder's own log row downgraded the timeline banner from "
        f"{before!r} to {after!r}. The state file still says blocked, so every "
        f"state-reading assertion stays green while the word BLOCKED disappears "
        f"from the surface the demo is judged on."
    )
    assert "INCOMPLETE" not in after, (
        f"the banner reads {after!r}: the run has a real ending and the renderer "
        f"is now reporting it as one that never finished"
    )
    # And the refusal itself is still findable by a reader of the run -- the
    # restated ending must not have replaced the explanation.
    rendered = timeline.render_text(state.run_id)
    assert "declined to record a refusal" in rendered, (
        "the timeline no longer explains why the recorder wrote nothing, so a "
        "reader sees a recorder job that ran and left no trace"
    )


def test_every_terminal_status_has_an_ending_action_the_timeline_recognises():
    """The map that keeps the banner honest, checked against the renderer itself.

    Two failure modes, both silent: a terminal status missing from
    `_OUTCOME_ACTIONS` raises a KeyError inside the guard (turning a refusal into
    a crash), and an action that `timeline._OUTCOME` does not know renders as
    INCOMPLETE. Both are asserted against the real map in the real renderer
    rather than a restatement, so adding a status to the frozen contract fails
    here rather than on a projector.
    """
    from agentorg import timeline

    module = _stage_module()

    assert set(module._OUTCOME_ACTIONS) == module._TERMINAL_STATUSES, (
        f"_OUTCOME_ACTIONS covers {sorted(module._OUTCOME_ACTIONS)} but the "
        f"terminal statuses are {sorted(module._TERMINAL_STATUSES)}; a status "
        f"missing here makes the guard raise KeyError instead of refusing"
    )
    assert timeline._OUTCOME, "timeline._OUTCOME is empty; nothing was checked"
    for status, action in sorted(module._OUTCOME_ACTIONS.items()):
        assert action in timeline._OUTCOME, (
            f"status {status!r} maps to action {action!r}, which "
            f"timeline._OUTCOME does not know ({sorted(timeline._OUTCOME)}); the "
            f"banner would read INCOMPLETE for a run that really ended"
        )


# ── A CANCELLED RUN IS NOT A HUMAN REFUSAL ────────────────────────────────────
#
# GitHub gives a job that did not succeed three distinct results, and the recorders
# treated two of them as one:
#
#     'success'    approved -- the gate job records it itself
#     'skipped'    a reviewer refused -- the recorder's whole reason to exist
#     'cancelled'  NOBODY DECIDED -- someone clicked Cancel, or concurrency evicted it
#
# `needs.<gate>.result != 'success'` matches the last two, so a cancelled run had
# `REJECTED by <github.actor>` written onto its issue and attributed to a named human
# who never saw the gate. MEASURED on run 32575709109: the poisoned run was cancelled
# at gate1 and issue #37 received
#
#     Agent Org · gate1
#     REJECTED by mohamedsorour1998
#     at 2026-08-22T13:30:21.835861+00:00
#
# then the recorder exited 4. Fabricating a decision against a person's name is the
# inverse of the defect these jobs exist to prevent -- and the upstream-stage clause
# does not help, because the upstream stage of a cancelled run often DID succeed.

@pytest.mark.parametrize(("recorder", "gate"), [
    ("gate1-rejected", "gate1"),
    ("gate2-rejected", "gate2"),
    ("gate3-rejected", "gate3"),
])
def test_a_rejection_recorder_does_not_fire_on_a_cancelled_gate(recorder, gate):
    """THE test. Without the clause a cancelled run is recorded as a refusal."""
    condition = _job(recorder)["if"]
    assert condition, f"{recorder} has no if: at all"

    assert f"needs.{gate}.result != 'cancelled'" in condition, (
        f"{recorder} fires when {gate} is CANCELLED, which is not a refusal -- so a "
        f"cancelled run gets `REJECTED by <github.actor>` posted to its issue, "
        f"naming a human who never made that decision. Condition was:\n  {condition}"
    )


@pytest.mark.parametrize(("recorder", "gate", "upstream"), [
    ("gate1-rejected", "gate1", "plan"),
    ("gate2-rejected", "gate2", "develop"),
    ("gate3-rejected", "gate3", "sre"),
])
def test_a_rejection_recorder_still_fires_on_a_skipped_gate(recorder, gate, upstream):
    """The clause above must not have narrowed the job out of existence.

    A guard that suppresses the case it was written for is the failure mode of every
    fix in this file, so the three surviving requirements are asserted here rather
    than left implied: the recorder still keys on the gate NOT succeeding, and still
    carries the upstream-stage discriminator that separates "a human refused" from
    "the run never got here".
    """
    condition = _job(recorder)["if"]

    assert f"needs.{gate}.result != 'success'" in condition, (
        f"{recorder} no longer fires on a refused gate, which is the only thing it "
        f"exists to record. Condition:\n  {condition}"
    )
    assert f"needs.{upstream}.result == 'success'" in condition, (
        f"{recorder} lost its upstream-stage discriminator, so a gate the run never "
        f"REACHED now reads as a refusal. Condition:\n  {condition}"
    )
    assert "always()" in condition, (
        f"{recorder} no longer runs on a failed run, so a refusal after a failing "
        f"stage would go unrecorded. Condition:\n  {condition}"
    )


# ── THE RECORDED REASON IS WHAT A HUMAN READS ─────────────────────────────────
#
# This comment IS the record: GitHub skips the gate job on a refusal, so there is no
# job log to read and the issue comment is all a reader gets. It used to say
#
#     "gate1 was refused, or its job did not complete."
#
# -- honest hedging when the recorder genuinely could not tell those apart, and
# unreadable as a result: a person is told a decision was recorded against their name
# and then told it might not have been a decision. Now that `cancelled` is excluded
# above, one cause remains, so the sentence names it.

def test_the_recorded_refusal_reason_names_one_cause_not_two():
    """A reason that hedges between two causes tells a reader neither."""
    module = _stage_module()
    # Reached through the module, the way this file reaches RunState -- run_stage.py
    # is loaded by path, not imported, so its names are only on that object.
    HumanDecision = module.HumanDecision
    captured = {}

    def _resume(run_id, decision):
        captured["decision"] = decision
        state = module.RunState(ticket_id="7", ticket_text="x")
        state.status = "rejected"
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(module.gates, "resume", _resume)
        monkeypatch.setattr(module.gates, "load", lambda run_id: module.RunState(
            ticket_id="7", ticket_text="x"))
        monkeypatch.setattr(module.graph, "_gate_comment", lambda *a, **k: None)
        monkeypatch.setattr(module, "_emit", lambda *a, **k: None)
        module._stage_gate_rejected(argparse.Namespace(
            run_id="r", approver="someone", stage="gate1-rejected"), "gate1")
    finally:
        monkeypatch.undo()

    decision = captured.get("decision")
    assert isinstance(decision, HumanDecision), "no decision was recorded at all"
    reason = decision.reason

    assert " or " not in reason, (
        f"the recorded reason hedges between two causes, so a reader learns neither. "
        f"`cancelled` is excluded by the workflow now, so there is one cause. "
        f"Reason was:\n  {reason}"
    )
    assert "refused" in reason, f"the reason does not say a human refused: {reason}"
    assert "not merged" in reason, (
        f"the reason does not say the change did not ship, which is the fact a "
        f"reader of an issue most needs: {reason}"
    )


def test_the_sre_stage_measures_ci_before_invoking_the_agent():
    """CI must be measured on the RUNNER, which holds the token, not in the container.

    `sre.run` needs a GitHub token to read check runs, and under REMOTE_AGENTS=true its
    body executes inside an AgentCore runtime whose whole environment is `AGENT_ROLE`
    and `DEMO_REPO`. So asking there returns `unknown` from `_use_local()` without an
    API call -- measured on the verified clean run, whose checks had been green for 49
    seconds. This job holds `DEMO_GITHUB_TOKEN`, so it measures and the container reads
    the answer off the state.

    Asserted over the AST, on ORDER, because "measures it" is not the requirement --
    measuring after the call would leave the sent state blank and read exactly as
    correct. A source-substring check would also pass on a comment saying so, which is
    this repository's most repeatable false positive.
    """
    import ast

    tree = ast.parse(STAGE_SCRIPT.read_text())
    func = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_stage_sre"), None)
    assert func is not None, "_stage_sre not found; this test would pin nothing"

    measure_line = call_line = None
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        source = ast.unparse(node)
        # MATCHED ON `.ci_status(` RATHER THAN ON `github_ops.ci_status`, because Lane
        # D's port moved the call to `integrations.host().ci_status(state)` and the
        # literal spelling stopped existing. The PROPERTY this test pins is unchanged
        # and still load-bearing: whoever measures, it must happen before the agent is
        # invoked, or the state sent to the container is blank.
        #
        # This is the failure mode CLAUDE.md names as "when you change a mechanism,
        # tests referencing the old one do not fail -- they stop testing." Here it DID
        # fail, because the first assertion below refuses a matcher that matched
        # nothing. Without that guard the port would have silently disarmed the one
        # test standing between the SRE stage and a permanent `CI unknown`.
        if ".ci_status(" in source and measure_line is None:
            measure_line = node.lineno
        if "call_agent" in source and "'sre'" in source.replace('"', "'"):
            call_line = node.lineno

    assert measure_line is not None, (
        "_stage_sre never measures ci_status, so the container measures it "
        "instead -- and the container has no token, so the answer is always `unknown`"
    )
    assert call_line is not None, "_stage_sre does not invoke the sre agent at all"
    assert measure_line < call_line, (
        f"ci_status is measured at line {measure_line}, AFTER the agent is invoked at "
        f"line {call_line}. The state sent to the container is still blank, so the "
        f"measurement cannot reach the agent that needs it."
    )


# ── THE REVISION CAP IS ASYMMETRIC, AND THAT IS THE POINT ─────────────────────
#
# A poisoned run CANNOT converge. `developer.run` re-substitutes the reference diff
# whenever the model's answer no longer carries the key on an added line, so the
# developer removes the credential, the safety net puts it back, and the reviewer
# objects again. MEASURED on PR #44: four rounds, four DIFFERENT model summaries -- so
# the model was complying every time -- and the same rejection each round.
#
# A clean run genuinely uses its retries. MEASURED on run 32557597915: the reviewer
# asked for email-based limiting, the developer produced IP-based, and the run ended
# `failed` at the cap with security reporting PASS. One shared value cannot serve both,
# which is why `poisoned` selects it.

def test_only_the_develop_job_sets_the_revision_cap():
    """The loop lives in `develop`; the value elsewhere would be decoration.

    Asserted as an exact set rather than "develop has it", because a cap on `sre` or
    `promote` would read as though it did something and change nothing.
    """
    carriers = {name for name, job in _jobs().items()
                if "MAX_REVISION_LOOPS" in (job.get("env") or {})}
    assert carriers == {"develop"}, (
        f"the revision cap is set on {sorted(carriers)}; only `develop` runs the "
        f"developer/reviewer loop, so anywhere else it is inert but looks meaningful"
    )


def test_the_poisoned_run_gets_a_smaller_cap_than_the_clean_run():
    """THE test. Both branches must be present AND they must differ.

    Two identical values would leave the expression, the comment and this test all
    looking correct while the asymmetry -- the entire reason it exists -- was gone.
    That is this repository's most repeated failure shape, so the values are parsed
    out and compared rather than matched as a literal string.
    """
    expression = _job("develop")["env"]["MAX_REVISION_LOOPS"]

    assert "inputs.poisoned" in expression, (
        f"the cap does not depend on `poisoned`, so both halves of the demo share one "
        f"value: {expression}"
    )
    # NOT AN EQUALITY AGAINST 'true'. THIS ASSERTION USED TO REQUIRE THE BUG.
    #
    # `poisoned` is declared `type: boolean`, so in an expression context
    # `inputs.poisoned` is a real boolean on a UI or `gh workflow run` dispatch, and
    # `== 'true'` against a boolean is always FALSE. MEASURED on run 32585947588: the
    # develop job printed `POISONED: true` and `MAX_REVISION_LOOPS: 3` -- the cap took
    # the clean branch on a poisoned run and the demo ran four review rounds.
    #
    # The first version of this test asserted `== 'true'` was PRESENT, so it passed on
    # the broken expression and would have failed on the fix. A test can be worse than
    # absent: this one actively defended the defect, and only a deployed run could tell.
    assert "== 'true'" not in expression and '== "true"' not in expression, (
        f"the cap compares `inputs.poisoned` against the string 'true'. That input is "
        f"declared `type: boolean`, so on a UI or `gh workflow run` dispatch the "
        f"comparison is always false and every run silently gets the clean cap -- "
        f"measured on run 32585947588: `POISONED: true` with "
        f"`MAX_REVISION_LOOPS: 3`. Expression: {expression}"
    )
    # And the string shape still has to be handled: a REST dispatch (EventBridge, and
    # every `gh api` call) sends `"false"` as a non-empty STRING, which a bare
    # truthiness test reads as true. Both shapes are real, so both must be covered.
    assert "!= 'false'" in expression or '!= "false"' in expression, (
        f"the cap does not exclude the STRING \"false\" that a REST dispatch sends, "
        f"so an API-triggered clean run would take the poisoned branch: {expression}"
    )

    numbers = [int(n) for n in re.findall(r"'(\d+)'", expression)]
    assert len(numbers) == 2, (
        f"expected two branch values in the cap expression, found {numbers}: "
        f"{expression}"
    )
    poisoned_cap, clean_cap = numbers
    assert poisoned_cap < clean_cap, (
        f"the poisoned cap ({poisoned_cap}) is not smaller than the clean one "
        f"({clean_cap}). Identical values would make this expression, its comment and "
        f"this test all read as correct while doing nothing."
    )
    assert poisoned_cap >= 1, (
        f"a cap of {poisoned_cap} would stop the loop before the reviewer ever ran, "
        f"so the poisoned PR would carry no review comment at all"
    )


def test_the_cap_still_admits_at_least_one_reviewer_pass_on_a_clean_run():
    """A clean run must be able to recover from a first-pass disagreement.

    Pinned because the tempting simplification is one small value for both halves --
    and measured, that ends the clean run `failed` with the scanners reporting PASS,
    which is the demo's promoted beat gone.
    """
    expression = _job("develop")["env"]["MAX_REVISION_LOOPS"]
    clean_cap = [int(n) for n in re.findall(r"'(\d+)'", expression)][-1]
    assert clean_cap >= 2, (
        f"the clean run's cap is {clean_cap}, so a reviewer withholding approval once "
        f"ends the run `failed` -- measured on run 32557597915"
    )


# ── THE CAP EXPRESSION IS EVALUATED, NOT READ ─────────────────────────────────
#
# Every test above reads the workflow TEXT, and that is why the first version of this
# cap shipped broken. `inputs.poisoned` is declared `type: boolean`, so on a UI or
# `gh workflow run` dispatch it is a real boolean in an expression context and
# `== 'true'` is always false. MEASURED on run 32585947588: the develop job printed
#
#     POISONED: true
#     MAX_REVISION_LOOPS: 3
#
# -- the cap took the CLEAN branch on a poisoned run, four review rounds ran, and three
# text-level tests were green the whole time. One of them asserted `== 'true'` was
# present, so it required the bug.
#
# This evaluates the expression against BOTH real input shapes instead. A tiny
# evaluator, not a general GitHub-expression engine: it handles exactly the operators
# this one line uses, and it FAILS LOUDLY on anything it does not recognise, so a
# rewritten expression cannot silently start being un-evaluated -- which is the same
# trap one level up.

def _github_truthy(value):
    """GitHub's truthiness: `false`, `''` and `0` are false; any other string is true.

    The non-empty-string rule is the whole reason `!= 'false'` is needed: a REST
    dispatch sends the STRING "false", which is truthy here.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value not in ("", "false")
    return bool(value)


def _eval_cap(expression, poisoned):
    """Evaluate `${{ (A && B) && 'x' || 'y' }}` for one value of inputs.poisoned.

    Raises on any shape it was not written for, rather than guessing -- an evaluator
    that silently mishandles an operator is exactly the false confidence this file
    documents seven times over.
    """
    body = re.fullmatch(r"\$\{\{\s*(.+?)\s*\}\}", expression.strip())
    assert body, f"not a single GitHub expression: {expression!r}"
    inner = body.group(1)

    m = re.fullmatch(r"(.+?)\s*&&\s*'(\d+)'\s*\|\|\s*'(\d+)'", inner)
    assert m, (
        f"the cap expression is no longer `<condition> && 'n' || 'm'`, so this "
        f"evaluator cannot check it. Extend it deliberately rather than deleting this "
        f"test: {inner!r}"
    )
    condition, when_true, when_false = m.group(1).strip(), m.group(2), m.group(3)
    condition = condition.strip("()").strip()

    def one(term):
        term = term.strip()
        if term == "inputs.poisoned":
            return _github_truthy(poisoned)
        neq = re.fullmatch(r"inputs\.poisoned\s*!=\s*'([^']*)'", term)
        if neq:
            return str(poisoned).lower() != neq.group(1)
        eq = re.fullmatch(r"inputs\.poisoned\s*==\s*'([^']*)'", term)
        if eq:
            # A real boolean never equals a string in GitHub expressions.
            return (not isinstance(poisoned, bool)) and str(poisoned) == eq.group(1)
        raise AssertionError(f"unrecognised term in the cap condition: {term!r}")

    return when_true if all(one(t) for t in condition.split("&&")) else when_false


@pytest.mark.parametrize(("poisoned", "shape"), [
    (True, "boolean true -- a UI or `gh workflow run` dispatch"),
    ("true", "string 'true' -- a REST dispatch, which EventBridge uses"),
])
def test_a_poisoned_run_evaluates_to_the_small_cap(poisoned, shape):
    """THE test the text-level ones could not be. Both dispatch shapes are real."""
    expression = _job("develop")["env"]["MAX_REVISION_LOOPS"]
    result = _eval_cap(expression, poisoned)
    assert result == "1", (
        f"a poisoned run ({shape}) evaluates the cap to {result}, not 1. This is the "
        f"defect measured on run 32585947588, where `POISONED: true` came with "
        f"`MAX_REVISION_LOOPS: 3`. Expression: {expression}"
    )


@pytest.mark.parametrize(("poisoned", "shape"), [
    (False, "boolean false -- the declared default"),
    ("false", "string 'false' -- what a REST dispatch sends, and TRUTHY to GitHub"),
])
def test_a_clean_run_evaluates_to_the_full_cap(poisoned, shape):
    """The other half, and the string case is the one a bare truthiness test breaks."""
    expression = _job("develop")["env"]["MAX_REVISION_LOOPS"]
    result = _eval_cap(expression, poisoned)
    assert result == "3", (
        f"a clean run ({shape}) evaluates the cap to {result}, not 3. A clean run needs "
        f"its retries -- measured on run 32557597915, which ended `failed` at the cap "
        f"with security reporting PASS. Expression: {expression}"
    )
