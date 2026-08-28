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
import os
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


def test_run_stage_accepts_the_trigger_flag():
    """The other half of the chain, landed by another lane.

    This was a non-strict xfail while the two halves were in flight: this lane
    owns run-pipeline.yml and added `--trigger`; scripts/run_stage.py belongs to
    another lane. MEASURED with only the workflow half in place:
    `run_stage: error: unrecognized arguments: --trigger issue`, which would have
    crashed the FIRST job of the demo pipeline.

    The flag has since landed, so this is now a plain assertion. A lane boundary
    was never a reason for nothing to check it -- the workflow passing an argument
    the script rejects is a crash, not a style disagreement.
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


def test_the_stage_records_the_trigger_onto_the_run_state():
    """Accepting the flag and dropping it would pass the test above and prove nothing.

    The end of the chain: workflow input -> shell env -> argparse -> RunState. Every
    other link is asserted elsewhere in this file; this is the last one. Without it,
    `--trigger` could be accepted and discarded while `RunState.trigger` held its
    default, which is this project's signature failure shape applied to its own
    provenance field.

    ASSERTED BY RUNNING THE STAGE, NOT BY MATCHING ITS SOURCE, and that correction
    is worth recording. A first version searched the source for
    `trigger=args.trigger`. It passed, then broke when the other lane changed the
    spelling to `getattr(args, "trigger", "")` -- which is BETTER code, because the
    hand-built argparse.Namespace objects in several test files have no `trigger`
    attribute and `args.trigger` raised AttributeError across 20 of them. So the
    regex was pinning one spelling of a correct implementation rather than the
    behaviour, and it failed on an improvement. A matcher that breaks when the code
    gets better is testing the wrong thing.
    """
    import json
    import subprocess
    import sys
    import tempfile

    help_text = subprocess.run(
        [sys.executable, "scripts/run_stage.py", "plan", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    ).stdout
    assert "--trigger" in help_text, "the flag is not accepted; see the test above"

    with tempfile.TemporaryDirectory() as workspace:
        env = {
            **os.environ,
            # Force the fully offline path: no model call, no GitHub write, and a
            # scratch workspace so this does not touch the real runs/ directory.
            "LLM_DISABLED": "true",
            "OFFLINE": "true",
            "OFFLINE_REPO": f"{workspace}/repo",
            "OFFLINE_NOTES": f"{workspace}/NOTES.md",
            "RUNS_DIR": workspace,
            # PYTHONPATH IS WHAT MAKES THIS TEST PASS IN A GIT WORKTREE, and without it
            # this was the only failing test in every one of the final phase's fourteen
            # lane worktrees -- read as a lane regression by three separate lanes before
            # the cause was found.
            #
            # The subprocess imports `agentorg` through the EDITABLE INSTALL's finder,
            # which resolves to the main checkout rather than to the tree this test file
            # lives in. `gates._STATE_DIR` is then
            # `Path(agentorg.gates.__file__).parent.parent / "runs"` -- the MAIN
            # checkout's runs/ -- so the stage wrote its state there while the assertion
            # below globbed the worktree's. Both halves were doing exactly what they say.
            #
            # MEASURED in a pristine worktree at 9b2b1ee with no other changes:
            #   pytest -q tests/test_trigger_provenance.py            -> 1 failed
            #   PYTHONPATH=$PWD pytest -q tests/...                   -> 21 passed
            #
            # Note `RUNS_DIR` above is INERT: `grep -rn RUNS_DIR agentorg/ scripts/`
            # returns nothing. It is left in place because it documents the intent and
            # costs nothing, but it is not what redirects the state -- believing it did
            # is what made this look impossible.
            "PYTHONPATH": str(REPO_ROOT),
        }
        completed = subprocess.run(
            [
                sys.executable, "scripts/run_stage.py", "plan",
                "--ticket-id", "TRIGGER-1",
                "--ticket-text", "Add a per-IP login rate limit.",
                "--poisoned", "false",
                "--trigger", INGRESS_TRIGGER,
            ],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False,
        )
        assert completed.returncode == 0, (
            f"the plan stage exited {completed.returncode} while being handed "
            f"--trigger {INGRESS_TRIGGER!r}.\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )

        run_id = next(
            (
                line.split("=", 1)[1].strip()
                for line in completed.stdout.splitlines()
                if line.startswith("run_id=")
            ),
            None,
        )
        assert run_id, (
            f"the plan stage printed no `run_id=` line, so its state cannot be "
            f"found:\n{completed.stdout}"
        )

        state_files = list(Path(workspace).glob(f"{run_id}.state.json"))
        if not state_files:
            state_files = list((REPO_ROOT / "runs").glob(f"{run_id}.state.json"))
        assert state_files, (
            f"no state file for run {run_id}; this test cannot read what the stage "
            f"recorded"
        )

        recorded = json.loads(state_files[0].read_text())

    assert recorded.get("trigger") == INGRESS_TRIGGER, (
        f"the plan stage was handed --trigger {INGRESS_TRIGGER!r} and recorded "
        f"trigger={recorded.get('trigger')!r} on the RunState. The flag is parsed "
        f"and DISCARDED: the run would carry the default, every job would stay "
        f"green, and nothing would say an issue started it."
    )


# --------------------------------------------------------------------------
# THE OTHER KNOB THAT DECIDES A VERDICT: SECURITY_BLOCK_THRESHOLD.
#
# Not about the trigger, but the same defect class and the same file
# (agentorg/common/config.py), so it lives here rather than in a module of its
# own: a malformed knob that fails LATE instead of at import.
#
# MEASURED before the fix:
#   SECURITY_BLOCK_THRESHOLD=HIGH python -c \
#     "from agentorg.state import compute_security_verdict; \
#      compute_security_verdict([], threshold='HIGH')"
#   KeyError: 'HIGH'
#
# raised from `cutoff = SEVERITY_ORDER[threshold]` -- which is reached from
# agentorg/agents/security.py:187, INSIDE the security agent, halfway through a
# run. So a typo took down the one stage whose entire purpose is to produce a
# verdict, and the traceback named a dict lookup rather than a misconfigured knob.
# Every other malformed knob in config.py already fails at import: STATE_BACKEND
# raises ValueError, MAX_REVISION_LOOPS and SCANNER_TIMEOUT_SECONDS raise from
# int().
# --------------------------------------------------------------------------


def _import_config_with(env: dict[str, str]) -> "tuple[int, str, str]":
    """Import `agentorg.common.config` in a FRESH interpreter with `env` applied.

    A subprocess, not importlib.reload: the raise this pins happens AT IMPORT, and
    the module is already imported by the time any test runs. Reloading in-process
    would also leave a half-initialised module object bound in sys.modules for
    every later test in the session -- the failure mode would be another test
    file's, which is the worst place for it to surface.
    """
    import os
    import subprocess
    import sys

    return_env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, "-c", "import agentorg.common.config"],
        cwd=REPO_ROOT,
        env=return_env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def test_a_legal_threshold_imports_cleanly():
    """The control. Without it, a check that ALWAYS raised would pass every test below."""
    code, _, stderr = _import_config_with({"SECURITY_BLOCK_THRESHOLD": "critical"})
    assert code == 0, (
        f"SECURITY_BLOCK_THRESHOLD=critical failed to import (exit {code}). The "
        f"validation is rejecting a legal severity, which would break every run "
        f"on a correctly configured machine.\n{stderr}"
    )


def test_the_default_imports_cleanly():
    """The value every machine actually runs with."""
    code, _, stderr = _import_config_with({})
    assert code == 0, f"config.py does not import with no overrides:\n{stderr}"


def test_an_unknown_threshold_is_refused_AT_IMPORT_not_mid_run():
    """The measured defect: `KeyError: 'HIGH'` from inside the security agent.

    Case matters, and `HIGH` is the realistic typo -- SEVERITY_ORDER's keys are
    lowercase, every other severity in this project's logs is uppercase in prose,
    and the workflow inputs are lowercase strings. So the wrong spelling looks
    right.
    """
    code, _, stderr = _import_config_with({"SECURITY_BLOCK_THRESHOLD": "HIGH"})
    assert code != 0, (
        "SECURITY_BLOCK_THRESHOLD=HIGH imported successfully. It then reaches "
        "compute_security_verdict and raises `KeyError: 'HIGH'` from inside the "
        "security agent, mid-run -- killing the one stage whose purpose is to "
        "produce a verdict, with a traceback naming a dict lookup rather than a "
        "misconfigured knob."
    )
    assert "SECURITY_BLOCK_THRESHOLD" in stderr, (
        f"the import failed but the error does not name the knob, so an operator "
        f"cannot tell which setting is wrong:\n{stderr}"
    )


def test_the_refusal_names_the_legal_values():
    """An error that says "invalid" without saying what IS valid costs a round trip.

    Same standard STATE_BACKEND's message already meets.
    """
    _, _, stderr = _import_config_with({"SECURITY_BLOCK_THRESHOLD": "HIGH"})
    from agentorg.state import SEVERITY_ORDER

    for severity in SEVERITY_ORDER:
        assert severity in stderr, (
            f"the refusal message does not mention the legal severity "
            f"{severity!r}, so it tells an operator what is wrong without telling "
            f"them what to write instead:\n{stderr}"
        )


@pytest.mark.parametrize(
    "bad",
    [
        "HIGH",       # the measured case: right word, wrong case
        "hgih",       # a transposition
        "",           # explicitly set to empty, which is not the same as unset
        "none",       # a plausible "turn it off" that would silently pass nothing
        "0",          # the severity's ORDER rather than its name
        "urgent",     # a severity vocabulary from somewhere else
    ],
)
def test_every_malformed_threshold_is_refused(bad):
    """Parametrised because one rejected value proves only that one value is checked.

    `"none"` and `"0"` are the interesting ones. Both look like deliberate
    configuration rather than typos, and both would previously have raised
    `KeyError` from inside the security agent -- so an operator trying to turn the
    gate off would have crashed the run rather than been told the knob does not
    work that way.
    """
    code, _, stderr = _import_config_with({"SECURITY_BLOCK_THRESHOLD": bad})
    assert code != 0, (
        f"SECURITY_BLOCK_THRESHOLD={bad!r} imported successfully; it would reach "
        f"compute_security_verdict and raise KeyError inside the security agent"
    )
    assert "SECURITY_BLOCK_THRESHOLD" in stderr


def test_the_validation_reads_the_contract_rather_than_restating_the_severities():
    """One declaration of "the legal severities", not two.

    There is NO import cycle -- measured: agentorg/state.py imports only
    __future__, datetime, typing, uuid and pydantic, so config can import
    SEVERITY_ORDER from it directly. The plan allowed a second declaration in
    config plus a tripwire if a cycle existed; it does not, so the tripwire is
    unnecessary and a second declaration would be strictly worse.

    ASSERTED OVER THE AST, NOT THE SOURCE TEXT, and that is not fussiness -- it
    was measured. A first version of this test asserted `"SEVERITY_ORDER" in
    source`, and the validation's own comment block explains at length that
    SEVERITY_ORDER is imported rather than restated. So replacing the import with
    a hardcoded `("low", "medium", "high", "critical")` tuple left all 19 tests
    GREEN, satisfied by the comment saying it had not been done. The second
    instance of that trap found in this lane's work.
    """
    import ast

    from agentorg.state import SEVERITY_ORDER

    source = (REPO_ROOT / "agentorg" / "common" / "config.py").read_text()
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "SEVERITY_ORDER" in imported, (
        f"config.py does not IMPORT SEVERITY_ORDER (imported names: "
        f"{sorted(imported)}). Either the validation is gone, or it restates the "
        f"severity names -- two declarations of one fact, which is the thing that "
        f"drifts. Note the module's comments discuss the import, so a substring "
        f"check over the source would be satisfied by prose; this reads the AST."
    )

    # And no hardcoded severity tuple/list/set anywhere in the module, which is
    # the specific shape a "tidy" would introduce.
    literal_severity_groups = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set))
        and {
            elt.value
            for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
        >= set(SEVERITY_ORDER)
    ]
    assert not literal_severity_groups, (
        f"config.py contains a literal collection of every severity name "
        f"({len(literal_severity_groups)} found), which is a second declaration "
        f"of SEVERITY_ORDER. Import it instead -- there is no cycle."
    )

    # And the value it validates against really is the contract's.
    import agentorg.common.config as config_module

    assert config_module.SECURITY_BLOCK_THRESHOLD in SEVERITY_ORDER, (
        f"the ambient SECURITY_BLOCK_THRESHOLD "
        f"({config_module.SECURITY_BLOCK_THRESHOLD!r}) is not in SEVERITY_ORDER, "
        f"which means the import-time check did not run"
    )


def test_the_validated_threshold_actually_reaches_the_block_rule():
    """The knob and the rule must agree, or validating it proves nothing.

    A threshold validated in config and then ignored by the security agent would
    pass every test above while the gate used a different cutoff.
    """
    from agentorg.common import config as config_module
    from agentorg.state import SEVERITY_ORDER, Finding, compute_security_verdict

    threshold = config_module.SECURITY_BLOCK_THRESHOLD
    cutoff = SEVERITY_ORDER[threshold]

    # A finding exactly AT the threshold must block; one below it must not.
    at_threshold = Finding(
        tool="gitleaks", severity=threshold, rule="r",
        file="app/auth.py", line=1, description="d",
    )
    verdict, blocking = compute_security_verdict([at_threshold], threshold=threshold)
    assert verdict == "block" and len(blocking) == 1, (
        f"a finding at severity {threshold!r} did not block against threshold "
        f"{threshold!r}; the knob and the rule disagree"
    )

    below = [s for s, order in SEVERITY_ORDER.items() if order < cutoff]
    if below:
        quiet = Finding(
            tool="gitleaks", severity=below[0], rule="r",
            file="app/auth.py", line=1, description="d",
        )
        verdict, blocking = compute_security_verdict([quiet], threshold=threshold)
        assert verdict == "pass" and not blocking, (
            f"a finding at severity {below[0]!r} blocked against threshold "
            f"{threshold!r}, which is above it"
        )
