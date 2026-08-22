"""Pins the EventBridge target that dispatches run-pipeline.yml.

Owner: Task 3 (cloud-native platform lane).

WHAT THIS COVERS AND WHY IT IS SEPARATE FROM tests/test_ingress_terraform.py
---------------------------------------------------------------------------
That file pins the ingress module's function, its HMAC path, its IAM and its bus.
This one pins the TARGET added on top: the connection, the API destination and the
`input_transformer` that turns a GitHub issue event into a `workflow_dispatch` of
run-pipeline.yml. Separate file because it is a separate lane's work on a shared
module, and because the properties are different in kind -- these are about what
the dispatched run is TOLD to do.

THE TWO VALUES THAT MATTER MOST, AND WHY THEY ARE WORTH A TEST FILE
------------------------------------------------------------------
The template hardcodes `"poisoned": "false"` and `"auto_approve": "false"`, and
both were measured to be unpinned before this file existed:

  * Flipping `poisoned` to `"true"` passed 70 tests. Every issue-triggered run
    would then execute the POISONED diff -- the one carrying a hardcoded AWS key.
  * Flipping `auto_approve` to `"true"` passed the same 70. Every issue-triggered
    run would then self-approve all three human gates, which are the entire point
    of the Environments and the thing the demo exists to show a human clicking.

Neither flip breaks anything visibly. The pipeline runs, jobs go green, and the
wrong thing happens quietly -- which is this project's signature defect.

WHY THIS PARSES THE RAW FILE FOR THE TEMPLATE, NOT THE STRIPPED HCL
------------------------------------------------------------------
MEASURED, not assumed, and it is the trap this file exists to avoid falling into.
`test_ingress_terraform._strip_comments` blanks HEREDOC BODIES wholesale -- which
is correct for its purpose, since heredocs there hold prose full of `#` and
quotes. But `input_template` IS a heredoc. Probed against the real file:

    'input_transformer' in stripped code: True
    'input_template'    in stripped code: True
    'poisoned'          in stripped code: False      <-- gone
    'auto_approve'      in stripped code: False      <-- gone

So a test written over `_code()` would search for `"poisoned": "false"` in text
from which the whole template had been erased, match nothing, and pass. Four bugs
were already found in that file's own helpers for the neighbouring reason, so this
file reads the RAW text for the template and asserts the extraction matched before
asserting anything about its contents.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_TF = REPO_ROOT / "infra" / "Terraform" / "modules" / "ingress" / "main.tf"

# The workflow this target dispatches. It lives in THIS repository and only here --
# nothing may be committed to the target repo, which is the entire point of the
# GitHub App ingress.
DISPATCHED_WORKFLOW = "run-pipeline.yml"

# The inputs run-pipeline.yml declares. Restated here rather than parsed out of
# the workflow: this file is asserting that the DISPATCHER and the WORKFLOW agree,
# and deriving both sides from one source would assert nothing.
#
# `trigger` added 2026-08-22. It is the only field that can say a run was started
# by an opened issue: EventBridge dispatches through the same REST API
# `gh workflow run` uses, so `github.event_name` reads `workflow_dispatch` for
# both. Its value is asserted by tests/test_trigger_provenance.py, including that
# it DIFFERS from the workflow's default -- identical values would prove nothing.
EXPECTED_INPUT_KEYS = {"ticket_id", "ticket_text", "poisoned", "auto_approve", "trigger"}

# The two values whose flip is silent. See the module docstring.
SAFE_DEFAULTS = {"poisoned": "false", "auto_approve": "false"}


def _raw() -> str:
    assert MAIN_TF.is_file(), f"{MAIN_TF} is missing -- this file cannot pin anything"
    return MAIN_TF.read_text()


def _input_template() -> str:
    """The `input_template` heredoc body, read from the RAW file.

    Raises rather than returning None on a miss: a helper that quietly returned
    nothing here would take every assertion below green while checking an empty
    string. See the module docstring for why the stripped-HCL path cannot be used.
    """
    match = re.search(
        r"input_template\s*=\s*<<-?([A-Za-z_][A-Za-z0-9_]*)\r?\n(.*?)\n\s*\1\s*(?:\n|$)",
        _raw(),
        re.DOTALL,
    )
    assert match, (
        "no `input_template = <<EOT ... EOT` heredoc found in the ingress module. "
        "Either the target was removed -- in which case an opened issue starts "
        "nothing -- or it was rewritten in a form this parser does not read, and "
        "every assertion in this file would otherwise have passed vacuously."
    )
    body = match.group(2)
    assert body.strip(), "the input_template heredoc is empty"
    return body


def _template_json() -> dict:
    """The template parsed as JSON, with EventBridge/Terraform placeholders stubbed.

    `<issue_number>` is EventBridge's substitution syntax and `${var.x}` is
    Terraform's; neither is valid JSON, so both are replaced with a marker before
    parsing. Parsing rather than substring-matching is deliberate: it is what makes
    "the key is absent" and "the key holds the wrong value" different failures.
    """
    text = _input_template()
    text = re.sub(r"\$\{[^}]*\}", "TERRAFORM_INTERPOLATION", text)
    text = re.sub(r"<([A-Za-z_][A-Za-z0-9_]*)>", r"EVENTBRIDGE_\1", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        pytest.fail(f"input_template is not valid JSON after stubbing: {exc}\n{text}")
    assert isinstance(parsed, dict), f"input_template parsed as {type(parsed).__name__}"
    return parsed


# --------------------------------------------------------------------------
# The parser must not be the weak link. Everything below depends on these.
# --------------------------------------------------------------------------


def test_the_template_extractor_finds_the_real_heredoc_and_its_keys():
    """Proves the extraction works before anything asserts over its result.

    This is the anti-vacuity guard for the whole file. If the heredoc regex stops
    matching -- a rewrite to a quoted string, a different tag -- this fails here
    with that diagnosis, rather than every test below passing against "".
    """
    body = _input_template()
    for probe in ("ref", "inputs", "ticket_id", "poisoned", "auto_approve"):
        assert probe in body, (
            f"the extracted template does not contain {probe!r}, so the extraction "
            f"is reading the wrong text:\n{body}"
        )


def test_the_stripped_hcl_path_would_not_have_worked():
    """Pins the MEASURED trap, so nobody 'tidies' this file onto _code().

    tests/test_ingress_terraform._strip_comments blanks heredoc bodies wholesale.
    That is right for its purpose and wrong for this one, and the difference is
    invisible: a test over the stripped text finds nothing and passes. This asserts
    the erasure is real, so the reason this file reads raw text is documented by a
    failing-if-wrong check rather than by a comment alone.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ingress_tf_helpers", REPO_ROOT / "tests" / "test_ingress_terraform.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    stripped = module._code(MAIN_TF)
    raw = _raw()

    # The keys really are in the file...
    assert "poisoned" in raw and "auto_approve" in raw
    # ...and really are absent from the stripped form.
    assert "poisoned" not in stripped, (
        "the comment stripper no longer erases heredoc bodies. That is a change in "
        "tests/test_ingress_terraform.py, not here -- but this file's raw-text "
        "approach was chosen because of the erasure, so re-check that reasoning."
    )
    assert "auto_approve" not in stripped


# --------------------------------------------------------------------------
# What the dispatched run is TOLD to do. The reason this file exists.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "expected"), sorted(SAFE_DEFAULTS.items()))
def test_the_dangerous_inputs_are_dispatched_false(key, expected):
    """`poisoned` and `auto_approve` must both be the string "false".

    MEASURED: flipping either to "true" passed 70 tests before this existed.

    `poisoned=true` makes every issue-triggered run build the diff with the
    hardcoded AWS key. `auto_approve=true` makes every issue-triggered run
    self-approve all three gates -- the gates a human is supposed to click, on the
    surface the demo is judged on.

    Compared as a STRING, not a boolean, and that is not pedantry: the REST
    dispatch API rejects real JSON booleans inside `inputs`, so `false` unquoted
    would be a 422 at dispatch time. `scripts/run_stage.py:flag` parses exactly
    these two strings and refuses anything else.
    """
    inputs = _template_json().get("inputs")
    assert isinstance(inputs, dict), f"the template has no `inputs` object: {inputs!r}"
    assert key in inputs, (
        f"the dispatch template does not send {key!r} at all. run-pipeline.yml "
        f"declares it with a safe default, but an absent input means the workflow's "
        f"default decides -- so this must be explicit, not inherited."
    )
    assert inputs[key] == expected, (
        f"the dispatch template sends {key}={inputs[key]!r}, expected {expected!r}. "
        f"Every run started by an opened issue would carry it."
    )


def test_no_dispatched_input_is_an_unquoted_json_boolean():
    """The REST dispatch API rejects real booleans in `inputs`; all values are strings.

    Asserted over the RAW template text rather than the parsed JSON, because
    `json.loads` maps `true` and `"true"` to different Python types but a test
    reading only the parsed value would need to know which it was looking at. The
    raw form is unambiguous.
    """
    body = _input_template()
    for literal in (": true", ": false", ":true", ":false"):
        assert literal not in body, (
            f"the template contains an unquoted boolean ({literal.strip()!r}). The "
            f"workflow_dispatch REST API requires every value in `inputs` to be a "
            f"STRING and answers 422 otherwise, so the dispatch would fail for "
            f"every issue -- and the rule would look healthy while nothing ran."
        )


def test_the_dispatched_inputs_are_exactly_the_ones_the_workflow_declares():
    """Set equality against the workflow's four inputs.

    An input the workflow does not declare is a 422; an input it declares and this
    does not send falls back to the workflow's default, which is a decision made
    somewhere nobody is looking.
    """
    inputs = _template_json().get("inputs")
    assert isinstance(inputs, dict), f"no `inputs` object: {inputs!r}"
    assert set(inputs) == EXPECTED_INPUT_KEYS, (
        f"the template sends {sorted(inputs)}, run-pipeline.yml declares "
        f"{sorted(EXPECTED_INPUT_KEYS)}"
    )


def test_the_ticket_fields_come_from_the_issue_and_not_from_a_literal():
    """`ticket_id` and `ticket_text` must be EventBridge substitutions.

    A literal here would dispatch every issue as the same ticket, which on a demo
    reads as the pipeline ignoring the issue that triggered it. The substitutions
    must also be declared in `input_paths`, or EventBridge sends the placeholder
    text verbatim -- a run whose ticket id is the string `<issue_number>`.
    """
    body = _input_template()
    inputs = _template_json()["inputs"]

    assert inputs["ticket_id"] == "EVENTBRIDGE_issue_number", (
        f"ticket_id is {inputs['ticket_id']!r}; expected the <issue_number> substitution"
    )
    assert inputs["ticket_text"] == "EVENTBRIDGE_issue_title", (
        f"ticket_text is {inputs['ticket_text']!r}; expected the <issue_title> substitution"
    )

    # Every <placeholder> used must be declared in input_paths.
    used = set(re.findall(r"<([A-Za-z_][A-Za-z0-9_]*)>", body))
    assert used, "the template uses no substitutions at all"

    paths_block = re.search(r"input_paths\s*=\s*\{(.*?)\}", _raw(), re.DOTALL)
    assert paths_block, "no `input_paths` block found in the target"
    declared = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", paths_block.group(1)))
    assert declared, "the input_paths block declares nothing"

    undeclared = used - declared
    assert not undeclared, (
        f"the template substitutes {sorted(undeclared)}, which input_paths does not "
        f"declare. EventBridge sends the placeholder text verbatim, so the run "
        f"would receive the literal string '<{min(undeclared)}>'."
    )


def test_the_issue_fields_are_read_from_the_events_detail():
    """`input_paths` must select out of `$.detail`, where the handler puts the payload.

    `infra/ingress/handler.py` forwards GitHub's RAW body as the event's `Detail`,
    so the issue lives at `$.detail.issue`. A path rooted anywhere else resolves to
    nothing and EventBridge dispatches empty strings -- a run with no ticket, which
    still starts and still goes green.
    """
    paths_block = re.search(r"input_paths\s*=\s*\{(.*?)\}", _raw(), re.DOTALL)
    assert paths_block, "no `input_paths` block found"
    pairs = re.findall(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([^\"]+)\"", paths_block.group(1)
    )
    assert pairs, "input_paths declares no name = \"path\" pairs"

    for name, path in pairs:
        assert path.startswith("$.detail."), (
            f"input path {name}={path!r} does not start with `$.detail.`; the "
            f"handler puts GitHub's payload there, so this resolves to nothing"
        )


def test_the_target_dispatches_run_pipeline_in_this_repository():
    """The endpoint must name run-pipeline.yml, and the ref must be explicit.

    A dispatch with no `ref` is a 422. A dispatch naming a different workflow --
    or, worse, a workflow in the TARGET repo -- would violate the constraint that
    nothing is committed to the target repository.
    """
    raw = _raw()
    endpoint = re.search(r"dispatch_endpoint\s*=\s*\"([^\"]+)\"", raw)
    assert endpoint, "no `dispatch_endpoint` local found in the ingress module"
    url = endpoint.group(1)

    assert DISPATCHED_WORKFLOW in url or "dispatch_workflow_file" in url, (
        f"the dispatch endpoint {url!r} does not reference {DISPATCHED_WORKFLOW}"
    )
    assert "/actions/workflows/" in url and url.endswith("/dispatches"), (
        f"the dispatch endpoint {url!r} is not the workflow_dispatch REST endpoint"
    )

    template = _template_json()
    assert "ref" in template, (
        "the dispatch template sends no `ref`; the REST API requires one and "
        "answers 422 without it, so no issue would ever start a run"
    )
    assert template["ref"], "the dispatch template's `ref` is empty"
