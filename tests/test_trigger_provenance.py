"""How did this run start? No Actions context field can answer it.

THE MEASUREMENT THIS FILE EXISTS FOR. EventBridge does not have a special
dispatch mechanism -- it POSTs to the same
`/repos/{owner}/{repo}/actions/workflows/run-pipeline.yml/dispatches` endpoint
`gh workflow run` uses. So an auto-started run and a hand-typed one produce
byte-identical Actions metadata. MEASURED on run 32542152671, started by opening
issue #15 on the target repository:

    event: workflow_dispatch

which is exactly what a hand dispatch reads. CLAUDE.md records the workaround
that was in use before this field: "to tell them apart, read the plan job's
TICKET_ID" -- an inference from a value that happens to be a number nothing in
this repository knows, which is evidence but not a record.

So the provenance has to be SENT, and there is exactly one sender: the ingress
module's `input_transformer`. `run-pipeline.yml` declares the input with a
default of `manual`; the transformer sends `issue`.

THE DIFFERENT-VALUES ASSERTION IS THE POINT OF THIS FILE. If the workflow's
default and the Terraform template's value were the same string, a run recording
that value would be indistinguishable from a run whose trigger was never set --
the field would be present, populated, and worthless. That is the same shape as
`blocking=2` proving nothing about whether the scanners ran, and the same shape as
a check that cannot distinguish "did not run" from "passed".

The value is trustworthy in ONE DIRECTION and this file does not overclaim: a run
recording `issue` was sent by the rule, because nothing else sends that string. A
run recording `manual` may have been hand-dispatched or may have been sent by a
future caller that forgot to set it. Asymmetric evidence, stated rather than
dressed up.

WHY THE TERRAFORM HALF IS READ FROM THE RAW FILE. `input_template` is a HEREDOC,
and `tests/test_ingress_terraform._strip_comments` blanks heredoc bodies
wholesale -- correct for its purpose, and fatal here. A test written over the
stripped text would search for `"trigger": "issue"` in text from which the whole
template had been erased, match nothing, and pass.
`tests/test_ingress_dispatch_target.py` documents that measurement at length and
its `_input_template` helper is reused here rather than re-derived.
"""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_PIPELINE = REPO_ROOT / ".github" / "workflows" / "run-pipeline.yml"
INGRESS_TF = REPO_ROOT / "infra" / "Terraform" / "modules" / "ingress" / "main.tf"

# The field on the frozen contract that receives this value.
TRIGGER_FIELD = "trigger"

# What the ingress sends. Restated here rather than read from the Terraform,
# because this file's job is to assert the Terraform says it.
INGRESS_TRIGGER = "issue"


def _dispatch_inputs() -> dict:
    """`run-pipeline.yml`'s `workflow_dispatch.inputs` mapping, parsed as YAML.

    Parsed rather than grepped: the workflow is ~90 lines of comment before the
    first key, and those comments discuss the input names. Asserts the mapping
    was found so a restructured trigger block fails here rather than taking every
    assertion below green against an empty dict.
    """
    assert RUN_PIPELINE.is_file(), f"{RUN_PIPELINE} is missing; this test pins nothing"
    doc = yaml.safe_load(RUN_PIPELINE.read_text())
    # PyYAML parses the bare key `on` as the boolean True (YAML 1.1), so the
    # trigger block is reached by whichever spelling survived the load.
    triggers = doc.get("on", doc.get(True))
    assert isinstance(triggers, dict), (
        f"run-pipeline.yml's trigger block parsed as {type(triggers).__name__}, "
        f"not a mapping -- this file cannot read its inputs"
    )
    inputs = triggers.get("workflow_dispatch", {}).get("inputs")
    assert isinstance(inputs, dict) and inputs, (
        f"no workflow_dispatch inputs found in run-pipeline.yml: {inputs!r}"
    )
    return inputs


def _ingress_template_inputs() -> dict:
    """The `inputs` object the EventBridge target dispatches.

    Reuses `tests/test_ingress_dispatch_target.py`'s helpers rather than writing a
    second heredoc parser: that file measured why the stripped-HCL path cannot be
    used, and two parsers for one heredoc would be two things to keep correct.
    """
    spec = importlib.util.spec_from_file_location(
        "dispatch_target_helpers",
        REPO_ROOT / "tests" / "test_ingress_dispatch_target.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    inputs = module._template_json().get("inputs")
    assert isinstance(inputs, dict) and inputs, (
        f"the ingress input_template has no `inputs` object: {inputs!r}"
    )
    return inputs


# --------------------------------------------------------------------------
# The two halves, each asserted on its own.
# --------------------------------------------------------------------------


def test_the_workflow_declares_the_trigger_input_with_a_manual_default():
    """A string, optional, defaulting to `manual`.

    `type: string` and not a choice list on purpose: the value crosses the REST
    dispatch API as text and is recorded verbatim onto `RunState.trigger`. A
    closed enum would REJECT a future source rather than record it, and an
    unrecognised trigger name is better evidence than a silent `manual`.
    """
    inputs = _dispatch_inputs()
    assert TRIGGER_FIELD in inputs, (
        f"run-pipeline.yml declares no {TRIGGER_FIELD!r} input, so an ingress "
        f"dispatch sending it would be a 422 and every issue-triggered run would "
        f"fail to start -- while the EventBridge rule looked perfectly healthy"
    )
    spec = inputs[TRIGGER_FIELD]
    assert spec.get("type") == "string", (
        f"the {TRIGGER_FIELD} input is type {spec.get('type')!r}, expected string. "
        f"A boolean cannot carry a source name, and a choice list would reject an "
        f"unrecognised source rather than record it."
    )
    assert spec.get("default") == "manual", (
        f"the {TRIGGER_FIELD} input defaults to {spec.get('default')!r}, expected "
        f"'manual'. A hand dispatch must leave a value that is honestly not a "
        f"claim of automation."
    )
    assert spec.get("required") is not True, (
        "the trigger input is required:true, so every hand dispatch and every "
        "`gh workflow run` in the runbook would have to pass it or 422"
    )


def test_the_ingress_sends_the_trigger_as_a_quoted_string():
    """The only sender, and every value in `inputs` must be a string.

    The workflow_dispatch REST API rejects real JSON booleans inside `inputs` and
    answers 422, so a bare `issue` (or any unquoted token) would fail every
    delivery while the rule reported healthy.
    """
    inputs = _ingress_template_inputs()
    assert TRIGGER_FIELD in inputs, (
        f"the ingress input_template does not send {TRIGGER_FIELD!r}. An absent "
        f"input means the WORKFLOW's default decides, so every issue-triggered run "
        f"would record 'manual' -- and there would be no field anywhere saying an "
        f"issue started it, which is the state this whole field was added to end."
    )
    assert inputs[TRIGGER_FIELD] == INGRESS_TRIGGER, (
        f"the ingress sends trigger={inputs[TRIGGER_FIELD]!r}, expected "
        f"{INGRESS_TRIGGER!r}"
    )
    assert isinstance(inputs[TRIGGER_FIELD], str), (
        f"the ingress sends trigger as {type(inputs[TRIGGER_FIELD]).__name__}; the "
        f"dispatch API requires a string and answers 422 otherwise"
    )


# --------------------------------------------------------------------------
# THE ANTI-VACUITY ASSERTION. Without this, both halves above could hold and
# the field could still prove nothing.
# --------------------------------------------------------------------------


def test_the_ingress_value_and_the_workflow_default_are_DIFFERENT():
    """The assertion that makes the field evidence rather than decoration.

    If the ingress sent the same string the workflow defaults to, a run recording
    that string would be indistinguishable from a run whose trigger was never
    set. The field would be present, populated, and worthless -- the same shape as
    `blocking=2` proving nothing about whether the scanners actually ran.

    This is the only test in this file that fails if somebody "tidies" the two
    values into agreement, which is exactly the kind of change that looks like
    removing a duplicate.
    """
    default = _dispatch_inputs()[TRIGGER_FIELD].get("default")
    sent = _ingress_template_inputs()[TRIGGER_FIELD]
    assert sent != default, (
        f"the ingress sends trigger={sent!r} and run-pipeline.yml defaults to "
        f"{default!r} -- the SAME value. A run recording {sent!r} is then "
        f"indistinguishable from a run whose trigger was never set, so the field "
        f"records nothing. These two values must differ; that difference IS the "
        f"provenance."
    )


def test_the_contract_field_exists_and_defaults_to_the_workflow_default():
    """`RunState.trigger` and the workflow must agree on what "not automated" is.

    Read from the frozen contract rather than restated. If the model's default and
    the workflow's default diverged, a run started by a caller that passes no
    trigger at all would record a different value than a hand dispatch, and the
    two would be indistinguishable from each other for the wrong reason.
    """
    from agentorg.state import RunState

    state = RunState(ticket_id="T-1", ticket_text="x")
    assert hasattr(state, TRIGGER_FIELD), (
        "RunState has no `trigger` field, so the workflow input has nowhere to "
        "land and the value is discarded silently"
    )
    workflow_default = _dispatch_inputs()[TRIGGER_FIELD].get("default")
    assert state.trigger == workflow_default, (
        f"RunState.trigger defaults to {state.trigger!r} but run-pipeline.yml "
        f"defaults to {workflow_default!r}. Two spellings of 'nobody said' means a "
        f"reader cannot tell which layer failed to set it."
    )


def test_the_value_the_ingress_sends_is_not_the_contract_default_either():
    """The complement of the different-values test, one layer down.

    The workflow default and the contract default are asserted equal above, so
    this is implied -- but asserted directly because the implication runs through
    two other tests, and a future change to either one would break the chain
    silently rather than failing here.
    """
    from agentorg.state import RunState

    sent = _ingress_template_inputs()[TRIGGER_FIELD]
    assert sent != RunState(ticket_id="T-1", ticket_text="x").trigger, (
        f"the ingress sends trigger={sent!r}, which is also RunState's default. A "
        f"run that never received the input would carry the same value as one the "
        f"rule dispatched."
    )


# --------------------------------------------------------------------------
# The wiring. The workflow must actually pass the value to the stage.
# --------------------------------------------------------------------------


def _plan_step() -> dict:
    """The `plan` job's stage step, asserted to be exactly one."""
    doc = yaml.safe_load(RUN_PIPELINE.read_text())
    steps = [
        step
        for step in doc["jobs"]["plan"]["steps"]
        if "run_stage.py plan" in (step.get("run") or "")
    ]
    assert len(steps) == 1, (
        f"expected exactly one step in the plan job running `run_stage.py plan`, "
        f"found {len(steps)}. Zero means the workflow no longer runs the stage "
        f"this file is asserting about."
    )
    return steps[0]


def test_the_plan_step_passes_the_trigger_through_to_the_stage():
    """Declaring the input and never forwarding it records nothing.

    An input the workflow accepts and drops is worse than no input: the dispatch
    succeeds, the run goes green, and `RunState.trigger` silently holds its
    default -- which is this project's signature failure shape applied to its own
    provenance field.
    """
    step = _plan_step()
    run = step["run"]
    env = step.get("env") or {}

    assert "TRIGGER" in env, (
        f"the plan step's env does not carry TRIGGER, so `inputs.trigger` never "
        f"reaches the stage. env keys: {sorted(env)}"
    )
    assert "inputs.trigger" in str(env["TRIGGER"]), (
        f"the plan step sets TRIGGER={env['TRIGGER']!r}, which does not read "
        f"`inputs.trigger` -- a hardcoded value here would make every run claim "
        f"the same provenance"
    )
    assert re.search(r"--trigger\s+\"\$TRIGGER\"", run), (
        f"the plan step does not pass `--trigger \"$TRIGGER\"` to run_stage.py, so "
        f"the value stops at the shell environment and never reaches the "
        f"RunState. Step body:\n{run}"
    )


def test_the_trigger_is_quoted_in_the_shell_so_a_hostile_value_cannot_split():
    """`"$TRIGGER"`, never bare `$TRIGGER`.

    The value crosses the dispatch API as free text and, unlike `poisoned`, is
    NOT parsed by `run_stage.flag` -- it is recorded verbatim. Unquoted, a value
    containing whitespace would word-split into extra argv entries; argparse
    would then reject them, which is a loud failure rather than a quiet one, but
    the quoting is free and the loud failure would arrive mid-demo.
    """
    run = _plan_step()["run"]
    assert "--trigger $TRIGGER" not in run, (
        "the plan step passes `--trigger $TRIGGER` unquoted; a value with "
        "whitespace would word-split into extra arguments"
    )


@pytest.mark.xfail(
    reason=(
        "CROSS-LANE ORDERING, stated rather than hidden. This lane owns "
        "run-pipeline.yml and adds `--trigger`; scripts/run_stage.py belongs to "
        "another lane and its argparse addition lands separately. MEASURED with "
        "the workflow half in place and the stage half not: `run_stage: error: "
        "unrecognized arguments: --trigger issue`. This xfail is the record of "
        "the dependency -- it flips to XPASS the moment the flag is added, which "
        "is the signal that the chain is complete, and pytest reports XPASS "
        "distinctly from a pass so nobody has to remember to come back."
    ),
    strict=False,
)
def test_run_stage_accepts_the_trigger_flag():
    """The other half of the chain, owned by another lane.

    Asserted here anyway: the workflow passing a flag the script rejects is a
    crash in the FIRST job of the demo pipeline, and a lane boundary is not a
    reason for nothing to check it.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/run_stage.py", "plan", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "--trigger" in result.stdout, (
        f"scripts/run_stage.py does not accept --trigger, but "
        f".github/workflows/run-pipeline.yml passes it. The plan job -- the FIRST "
        f"job of the pipeline -- would die with `unrecognized arguments`. "
        f"argparse help:\n{result.stdout}"
    )
