"""LANE N, N6 — the deploy-platform workflow's expression, EVALUATED not read.

THIS FILE EXISTS BECAUSE THIS REPOSITORY HAS A TEST THAT REQUIRED A BUG.

`run-pipeline.yml`'s per-run revision cap shipped broken and three tests read the
workflow TEXT and stayed green throughout -- one of them asserting that `== 'true'`
was PRESENT, so it passed on the defect and would have FAILED on the fix. The only
witness was a deployed run printing:

    POISONED: true
    MAX_REVISION_LOOPS: 3        <- the CLEAN branch, on a poisoned run

The cause is GitHub's type coercion, and it cuts both ways:

  * A `type: boolean` input dispatched from the UI or `gh workflow run` arrives as a
    REAL BOOLEAN in an expression context, so `inputs.x == 'true'` is ALWAYS FALSE.
  * A REST dispatch -- every `gh api` call, and how EventBridge triggers
    `run-pipeline.yml` -- sends inputs as JSON STRINGS, and the non-empty string
    `"false"` is TRUTHY to GitHub. So a bare `inputs.x` sends the false case down the
    true branch.

Both dispatch shapes are real, so the only correct form is a truthiness test that
ALSO excludes the string `"false"`, and the only honest test is one that EVALUATES
the expression the way the runner does.

THE EVALUATOR IS DELIBERATELY NARROW AND RAISES ON ANYTHING IT WAS NOT WRITTEN FOR.
An evaluator that silently mishandled an operator would be the same false confidence
one level up -- a double that cannot express the failing case, which is this
repository's most-repeated pattern. It is modelled on
`tests/test_run_pipeline_workflow.py`'s `_eval_cap`, deliberately not imported from
it: that one parses `<condition> && 'n' || 'm'` and this expression has no `||`
branch, so sharing would mean widening a working evaluator to admit a shape it does
not need. Two narrow evaluators that each raise are safer than one permissive one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-platform.yml"


def _workflow() -> dict:
    """The parsed workflow.

    Parsed rather than grepped, so a key that MOVED cannot keep satisfying a
    substring search from somewhere else in the file.
    """
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    return yaml.safe_load(WORKFLOW.read_text())


def _job(name: str) -> dict:
    jobs = _workflow()["jobs"]
    assert name in jobs, f"deploy-platform.yml has no `{name}` job; jobs: {sorted(jobs)}"
    return jobs[name]


def _github_truthy(value: object) -> bool:
    """GitHub's truthiness. ONLY the empty string is falsy; `"false"` is TRUE.

    CHECKED AGAINST THE PLATFORM DOCUMENTATION, NOT COPIED, and the first draft of
    this function was WRONG in a way that made the test below fail -- which is the
    only reason it was found. It read:

        return value not in ("", "false")        # WRONG

    docs.github.com/en/actions/reference/workflows-and-actions/expressions states the
    rule for conditionals: falsy values are `false`, `0`, `-0`, `""`, `''` and `null`;
    "truthy (`true` and other non-falsy values) are coerced to `true`". A non-empty
    string is therefore TRUE, and the string `"false"` is non-empty.

    THAT IS THE ENTIRE REASON `!= 'false'` IS NEEDED in the shipped condition, so a
    helper that treated `"false"` as falsy could not express the failure the condition
    guards against -- it would BLESS a bare `inputs.x`. This repository's named
    pattern, in the evaluator rather than in the code under test.

    The masking is worth understanding: against the SHIPPED expression both spellings
    give the same answer, because a wrong first term and a correct second term reach
    the same `all()` result. So no test of the shipped form can detect it. Only
    driving the evaluator with the BROKEN form does, which is what
    `test_the_broken_equality_form_is_shown_to_fail_by_this_evaluator` is for.

    `tests/test_run_pipeline_workflow.py:2294` carries the same wrong line with a
    docstring stating the correct rule. That file is not this lane's; reported rather
    than edited.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value != ""
    return bool(value)


def _eval_condition(expression: str, redeploy: object) -> bool:
    """Evaluate `${{ A && B }}` for one value of `inputs.redeploy_service`.

    RAISES on any shape it was not written for. That refusal is the point: a
    rewritten expression must break this test loudly rather than quietly stop being
    evaluated.
    """
    body = re.fullmatch(r"\$\{\{\s*(.+?)\s*\}\}", expression.strip())
    assert body, f"not a single GitHub expression: {expression!r}"
    inner = body.group(1).strip()

    def one(term: str) -> bool:
        term = term.strip().strip("()").strip()
        if term == "inputs.redeploy_service":
            return _github_truthy(redeploy)
        neq = re.fullmatch(r"inputs\.redeploy_service\s*!=\s*'([^']*)'", term)
        if neq:
            # GitHub compares a boolean against a string by casting the boolean to
            # its lowercase spelling, which is why `true != 'false'` holds.
            return str(redeploy).lower() != neq.group(1)
        eq = re.fullmatch(r"inputs\.redeploy_service\s*==\s*'([^']*)'", term)
        if eq:
            # A REAL BOOLEAN NEVER EQUALS A STRING in a GitHub expression. This branch
            # exists so the broken form can be evaluated and shown to fail, not
            # because the shipped expression uses it.
            return (not isinstance(redeploy, bool)) and str(redeploy) == eq.group(1)
        raise AssertionError(
            f"unrecognised term in the redeploy condition: {term!r}. Extend this "
            f"evaluator deliberately rather than deleting the test -- an evaluator "
            f"that silently mishandles an operator is the false confidence this file "
            f"exists to prevent."
        )

    if "||" in inner:
        raise AssertionError(
            f"the redeploy condition gained a `||` branch, which this evaluator was "
            f"not written for: {inner!r}"
        )
    return all(one(term) for term in inner.split("&&"))


# ── THE FOUR CASES: BOTH DISPATCH SHAPES, BOTH VALUES ────────────────────────


@pytest.mark.parametrize(("redeploy", "shape"), [
    (True, "boolean true -- a UI or `gh workflow run` dispatch"),
    ("true", "string 'true' -- a REST dispatch, which every `gh api` call uses"),
])
def test_asking_for_a_redeploy_evaluates_true_in_both_dispatch_shapes(redeploy, shape):
    """THE test the text-level ones could not be. Both shapes are real."""
    condition = _job("redeploy")["if"]
    assert _eval_condition(condition, redeploy) is True, (
        f"a redeploy requested as {shape} evaluates FALSE, so the job would be "
        f"skipped and the workflow would report success having redeployed nothing. "
        f"That is the defect measured on run 32585947588 in run-pipeline.yml. "
        f"Condition: {condition}"
    )


@pytest.mark.parametrize(("redeploy", "shape"), [
    (False, "boolean false -- the declared default"),
    ("false", "string 'false' -- what a REST dispatch sends, and TRUTHY to GitHub"),
])
def test_not_asking_evaluates_false_in_both_dispatch_shapes(redeploy, shape):
    """The other half, and the STRING case is the one a bare truthiness test breaks.

    A bare `inputs.redeploy_service` passes the two tests above and FAILS this one,
    which is exactly why both halves are needed: an API-triggered run that did not
    ask for a redeploy would replace a running service.
    """
    condition = _job("redeploy")["if"]
    assert _eval_condition(condition, redeploy) is False, (
        f"a run that did NOT ask for a redeploy ({shape}) evaluates TRUE, so it "
        f"would force a new deployment of a running worker service unasked. The "
        f"string \"false\" is truthy to GitHub, which is why the condition needs "
        f"`!= 'false'` as well as truthiness. Condition: {condition}"
    )


def test_the_broken_equality_form_is_shown_to_fail_by_this_evaluator():
    """THE ANTI-VACUITY CHECK, and it is the most important test in this file.

    The four tests above pass against the shipped expression. They would ALSO pass if
    this evaluator quietly agreed with everything -- so this drives the evaluator with
    the KNOWN-BROKEN form and asserts it produces the measured wrong answer.

    Without this, a bug in `_eval_condition` makes all four tests vacuous while the
    count still reads healthy. That is the twelfth instance of this repository's named
    pattern, and it is why the broken form is exercised rather than merely described.
    """
    broken = "${{ inputs.redeploy_service == 'true' }}"

    # A UI dispatch sends a real boolean, and `== 'true'` against a boolean is always
    # false -- so asking for a redeploy would silently not redeploy.
    assert _eval_condition(broken, True) is False, (
        "this evaluator does not reproduce GitHub's boolean/string coercion, so the "
        "four tests above prove nothing about the shipped expression"
    )
    # And the shipped form gets that same case right, which is the discriminator.
    assert _eval_condition(_job("redeploy")["if"], True) is True

    # A bare truthiness test is the OTHER broken form: it admits the string "false".
    bare = "${{ inputs.redeploy_service }}"
    assert _eval_condition(bare, "false") is True, (
        "the evaluator does not treat a non-empty string as truthy, so it cannot "
        "express the REST-dispatch failure this condition guards against"
    )
    assert _eval_condition(_job("redeploy")["if"], "false") is False


def test_the_condition_is_not_the_form_that_shipped_broken_once():
    """A text-level assertion, kept DELIBERATELY and openly as the weaker one.

    Reading the workflow is what defended the bug last time, so this is not the
    evidence -- the four evaluations are. It is here because a rewrite to
    `== 'true'` should fail with a message naming the incident rather than only as
    two evaluation mismatches, and because the reason must be findable from the test
    that forbids it.
    """
    condition = _job("redeploy")["if"]
    assert "== 'true'" not in condition, (
        f"the redeploy condition uses `== 'true'`, which is ALWAYS FALSE against the "
        f"real boolean a UI dispatch sends. Measured on run 32585947588: "
        f"`POISONED: true` beside `MAX_REVISION_LOOPS: 3`. Condition: {condition}"
    )
    assert "!= 'false'" in condition, (
        f"the redeploy condition does not exclude the STRING \"false\" that a REST "
        f"dispatch sends, and a non-empty string is TRUTHY to GitHub -- so an "
        f"API-triggered run that did not ask for a redeploy would get one. "
        f"Condition: {condition}"
    )


def test_the_input_is_declared_boolean_so_the_coercion_is_real():
    """THE PREMISE OF EVERY TEST ABOVE, asserted rather than assumed.

    If `redeploy_service` were declared `type: string`, `inputs.x` would never be a
    real boolean and half of this file would be testing a coercion that cannot
    happen. The tests would still pass -- they would just be about nothing.
    """
    # `on` IS PARSED AS THE BOOLEAN `True`, not the string "on". YAML 1.1 treats
    # `on`/`off`/`yes`/`no` as booleans, and `yaml.safe_load` honours that -- so
    # `workflow["on"]` raises KeyError while `workflow[True]` is the trigger block.
    # FOUND BY RUNNING THIS TEST, which is the whole argument of this file: reading
    # the YAML would never have revealed it. `actionlint` is happy either way because
    # GitHub's own parser does not do the coercion.
    triggers = _workflow()
    block = triggers.get("on", triggers.get(True))
    assert block is not None, (
        f"deploy-platform.yml has no trigger block under either `on` or the boolean "
        f"key YAML 1.1 coerces it to. Keys: {sorted(map(str, triggers))}"
    )
    inputs = block["workflow_dispatch"]["inputs"]
    assert "redeploy_service" in inputs, (
        f"deploy-platform.yml declares no `redeploy_service` input; this whole file "
        f"would pin nothing. Inputs: {sorted(inputs)}"
    )
    declared = inputs["redeploy_service"].get("type")
    assert declared == "boolean", (
        f"`redeploy_service` is declared `type: {declared}`, not boolean. The "
        f"boolean/string coercion these tests evaluate is a property of a BOOLEAN "
        f"input; against a string input they would pass while testing nothing."
    )
    assert inputs["redeploy_service"].get("default") is False, (
        "the default must be false: a workflow whose default action is to replace a "
        "running service is one wrong click from an unasked-for deployment"
    )
