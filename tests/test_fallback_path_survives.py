"""LANE N, N3 — `run-pipeline.yml` IS THE DEMO'S FALLBACK AND MUST NOT BE DELETED.

N3 reads "keep `run-pipeline.yml` alive until Phase 3 proves the queue". Phase 3 has
proved it -- both demo paths ran through the queue with no GitHub Actions involved --
and the answer is STILL KEEP IT. This file is that decision as a test, because a
decision recorded only in a plan is one the next person reverses in good faith.

THREE REASONS, and the second is the one that surprised people.

  1. IT IS THE PATH WITH A VERIFIED RUN HISTORY. CLAUDE.md records the demo pair on
     this workflow: issue #41 -> PR #42 MERGED, issue #43 -> PR #44 blocked with
     `provenance: scanners` at added-lines 3 and 4, all three gates paused for a
     click, all three recorders correctly skipped. The queue path has run the same
     two scenarios once, on one machine, by one person. Two paths where one has a
     recorded history is redundancy; one path is a single point of failure on a
     projector.

  2. `scripts/run_stage.py` IS NOT BEING DELETED EITHER, and the queue is the reason.
     `agentorg/queue/runner.py:49` invokes it AS A SUBPROCESS, deliberately, for
     per-stage process isolation: `llm.last_source()` and the scanner fan-out memo are
     module state that an in-process call would inherit, and both produce a FALSE
     MEASUREMENT rather than a crash. So the queue depends on the same script the 15
     `run_stage.py` invocations in this workflow depend on. Deleting the workflow
     would not simplify the tree; it would remove the second caller of a file the
     first caller needs.

  3. THE THREE GATES ARE GITHUB ENVIRONMENTS. `docs/final/01-specification.md` §6
     names this as the honest limit of the self-hosted story: "the human gates
     currently depend on GitHub Environments. A fully self-hosted deployment needs its
     own approval mechanism". The queue's pause is a durable row and `web/` can
     release it, but the Environment-backed gate -- with a named reviewer GitHub
     itself enforces -- exists only here.

WHAT THIS FILE DOES NOT DO: duplicate `tests/test_run_pipeline_workflow.py`. That file
holds 68 tests on this workflow's blast radius and ungameable gates. This one asserts
only that the workflow and its script CONTINUE TO EXIST AND REMAIN WIRED TOGETHER --
the property no other test covers, because every other test presupposes it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_PIPELINE = REPO_ROOT / ".github" / "workflows" / "run-pipeline.yml"
RUN_STAGE = REPO_ROOT / "scripts" / "run_stage.py"
QUEUE_RUNNER = REPO_ROOT / "agentorg" / "queue" / "runner.py"

# The nine stages the workflow must still be able to walk. Named as a literal rather
# than read from `state.Stage`, and that is a deliberate exception to this repository's
# no-second-declaration rule for Lane C's reason: a second declaration is the only way
# to detect a change in the first. A stage silently dropped from the workflow while
# `Stage` still admits it is exactly the drift this catches.
_PIPELINE_JOBS = ("plan", "gate1", "develop", "gate2", "sre", "gate3", "promote")
_RECORDERS = ("gate1-rejected", "gate2-rejected", "gate3-rejected")


def _workflow() -> dict:
    assert RUN_PIPELINE.is_file(), (
        f"{RUN_PIPELINE} IS MISSING. It is the demo's fallback path and the only one "
        f"with a verified run history -- see this file's docstring. If the queue is "
        f"meant to replace it, that is a decision for the integrator with both paths "
        f"green, not a deletion."
    )
    return yaml.safe_load(RUN_PIPELINE.read_text())


def test_the_cloud_pipeline_workflow_still_exists():
    """The bluntest possible assertion, and it has no other home.

    Every one of `test_run_pipeline_workflow.py`'s 68 tests would ERROR rather than
    FAIL if this file were deleted -- an error in collection reads as a broken test
    environment, not as a deleted deployment path.
    """
    workflow = _workflow()
    assert workflow.get("name") == "run-pipeline", (
        f"the workflow's name is {workflow.get('name')!r}"
    )


def test_all_seven_pipeline_jobs_and_all_three_recorders_survive():
    """Seven jobs plus three recorders, by name.

    The recorders are the half most likely to be dropped as redundant, and they are
    not: a rejected Environment SKIPS its job rather than running it with a verdict,
    so nothing inside a gate job executes on a refusal and a branch in there could
    never record one. Hence three separate jobs.
    """
    jobs = _workflow()["jobs"]
    for name in _PIPELINE_JOBS:
        assert name in jobs, (
            f"the `{name}` job is gone from run-pipeline.yml. The seven jobs exist "
            f"because a GitHub Environment pauses a JOB and a job cannot pause in its "
            f"middle, so the pipeline is cut at the gate boundaries."
        )
    for name in _RECORDERS:
        assert name in jobs, (
            f"the `{name}` recorder is gone. A rejected Environment SKIPS its job, so "
            f"a human's refusal is recorded by nothing else -- and a run whose "
            f"refusal was never recorded looks identical to one nobody reviewed."
        )


def test_the_three_gates_are_still_github_environments():
    """The gates must remain Environment-backed, which is what makes them ungameable.

    An `if:` condition can be edited by whoever edits the workflow. An Environment's
    required reviewer is enforced by GitHub against a named person, and
    `scripts/preflight.py` check 4 verifies that live -- because an Environment with no
    required reviewer DOES NOT PAUSE, it runs.
    """
    jobs = _workflow()["jobs"]
    for gate in ("gate1", "gate2", "gate3"):
        assert jobs[gate].get("environment") == gate, (
            f"the `{gate}` job no longer declares `environment: {gate}`. Without it "
            f"the job does not pause and the run continues with nobody having "
            f"approved anything, while every job reports green."
        )


def test_the_workflow_still_invokes_run_stage_and_that_script_still_exists():
    """THE COUPLING N3 IS REALLY ABOUT.

    `scripts/run_stage.py` is invoked 15 times here AND once per stage by
    `queue/runner.py`. Deleting the workflow does not let anyone delete the script --
    and deleting the script breaks both paths at once, which is the failure this
    asserts against.
    """
    assert RUN_STAGE.is_file(), (
        f"{RUN_STAGE} IS MISSING, and it is the shared dependency of BOTH pipeline "
        f"paths: 15 invocations in run-pipeline.yml and one per stage from "
        f"agentorg/queue/runner.py, which shells out to it deliberately for process "
        f"isolation."
    )
    text = RUN_PIPELINE.read_text()
    invocations = len(re.findall(r"run_stage\.py", text))
    assert invocations >= 7, (
        f"run-pipeline.yml invokes run_stage.py only {invocations} time(s); every one "
        f"of the seven jobs plus the three recorders runs a stage through it"
    )


def test_the_queue_runner_invokes_the_same_script_as_a_subprocess():
    """The second half of the coupling, from the queue's side.

    ASSERTED BECAUSE THE PLAN SAID `run_stage.py` WOULD BE DELETED IN PHASE 3 AND THAT
    IS WRONG. The subprocess is not a stopgap: `llm.last_source()` and the scanner
    fan-out memo are module state, and an in-process call would inherit both and
    produce a false MEASUREMENT rather than a crash -- a stale cache hit looks exactly
    like a scan.
    """
    assert QUEUE_RUNNER.is_file(), f"{QUEUE_RUNNER} is missing"
    runner = QUEUE_RUNNER.read_text()
    assert "run_stage.py" in runner, (
        "queue/runner.py no longer names run_stage.py. If the queue was moved to an "
        "in-process call, the two pieces of module state it isolates -- "
        "llm.last_source() and the scanner memo -- are now inherited between stages, "
        "and both produce a wrong measurement rather than an error."
    )
    assert "subprocess" in runner, (
        "queue/runner.py no longer uses a subprocess. Per-stage process isolation is "
        "the reason it shells out; an in-process call also loses the guarantee that an "
        "os._exit or a segfault still arrives as an exit code."
    )


@pytest.mark.parametrize(("name", "code"), [
    ("EXIT_BLOCKED", 3),
    ("EXIT_REJECTED", 4),
])
def test_the_exit_codes_that_distinguish_a_block_from_a_crash_survive(name, code):
    """`3` is the block rule working; `4` is a human refusing. Neither may become `1`.

    Sharing an exit code with a crash would make the poisoned demo run
    indistinguishable from a broken workflow ON THE PROJECTOR, which is the one place
    the distinction is hardest to explain and most likely to be asked about.

    THE FIRST VERSION OF THIS TEST WAS NEARLY INERT and it is worth recording. It read:

        assert f"EXIT_BLOCKED = {code}" in source
            or f"EXIT_REFUSED = {code}" in source
            or re.search(<a loose regex for an equals sign and the number>, source)

    `EXIT_REFUSED` DOES NOT EXIST -- the constant is `EXIT_REJECTED` -- so the `4` case
    was passing entirely on that third clause, which matches any `= 4` anywhere in a
    900-line file. Found by grepping for the real names rather than trusting the green.

    A disjunction whose last term is a loose regex is a test that cannot fail: the
    specific clauses become decoration and the count still reads healthy. Both
    constants are now asserted BY NAME with the value parsed, and `EXIT_REJECTED`
    being spelled correctly is itself part of what is pinned.
    """
    source = RUN_STAGE.read_text()
    declaration = re.search(rf"^{name} = (\d+)$", source, re.MULTILINE)
    assert declaration, (
        f"run_stage.py no longer declares `{name}` at module level. Both pipeline "
        f"paths read these codes -- run-pipeline.yml through a job's exit status and "
        f"agentorg/queue/exit_codes.py by IMPORTING them from this file precisely so "
        f"a hardcoded table cannot drift."
    )
    assert int(declaration.group(1)) == code, (
        f"{name} is {declaration.group(1)}, not {code}. Exit code 3 is the block rule "
        f"WORKING and 4 is a human refusing; either sharing 1 with a crash makes the "
        f"poisoned demo indistinguishable from a broken workflow on a projector."
    )


def test_the_workflow_is_dispatch_only_and_has_no_push_trigger():
    """A `push:` trigger here would invoke five runtimes and open a pull request on
    somebody else's repository on every commit to this one.

    Restated from `test_run_pipeline_workflow.py` deliberately: it is the property most
    likely to be broken by somebody "modernising" this file while migrating to the
    queue, and this is the file arguing for the workflow's continued existence.
    """
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "no trigger block"
    assert "push" not in triggers, (
        f"run-pipeline.yml has a push trigger. Every commit to this repository would "
        f"invoke five AgentCore runtimes and open a PR on auth-service. "
        f"Triggers: {sorted(triggers)}"
    )
    assert "workflow_dispatch" in triggers, (
        "run-pipeline.yml is no longer dispatchable, so neither a human nor the "
        "EventBridge rule can start it -- and the rule dispatches this same event "
        "through the REST API"
    )
