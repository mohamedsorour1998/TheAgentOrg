"""The runtime role must be able to invoke the model the code actually asks for.

MEASURED 2026-08-22, against the live account, before this file existed:

    aws iam simulate-principal-policy \
      --policy-source-arn arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role \
      --action-names bedrock:InvokeModel \
      --resource-arns "arn:aws:bedrock:us-east-1:339712964409:inference-profile/us.amazon.nova-2-lite-v1:0"
    implicitDeny

    ... --resource-arns "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-lite-v1:0"
    allowed

`config.BEDROCK_MODEL` defaults to `us.amazon.nova-2-lite-v1:0`, whose `us.`
prefix makes it a cross-region INFERENCE PROFILE -- ARN `…:inference-profile/…`,
not `…::foundation-model/…`. The role granted only foundation-model, so
`bedrock:InvokeModel` was `implicitDeny`, `llm.text()` caught it by design,
`structured()` returned None, and every model-calling agent served its fixture
while every job reported green. The deployed plan comment on the target repo
matched `fixtures/plan_result.json` byte for byte.

That is this project's signature defect -- a check that cannot distinguish "did
not run" from "passed" -- landing on the model seam instead of the scanner seam.

This file pins the grant so it cannot recur silently. It parses the HCL as TEXT:
there is no `terraform` call and no AWS call, so it runs in CI with no
credentials, for the same reason `tests/test_ingress_terraform.py` is written
that way. A static test cannot prove the module APPLIES -- the `plan` job in
`.github/workflows/terraform.yml` is the first real check, and
`scripts/preflight.py` check 1 is the one that re-runs the simulation above.
"""

import json
import re
from pathlib import Path

import yaml

from agentorg.common import config

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTCORE_TF = REPO_ROOT / "infra" / "Terraform" / "modules" / "agentcore" / "main.tf"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
PLAN_FIXTURE = REPO_ROOT / "fixtures" / "plan_result.json"

# The prefixes AWS uses for cross-region inference profiles. A model id carrying
# one of these is a PROFILE, not a foundation model, and lives at a different ARN.
_PROFILE_PREFIXES = ("us.", "eu.", "apac.", "global.")


def _code() -> str:
    """The module's HCL with `#` comments stripped.

    Stripped rather than read raw because this module's comments now DISCUSS the
    ARN shapes -- the implementation note added with the grant quotes both
    literal ARNs in prose. A substring test over the raw file would be satisfied
    by the commentary while the policy underneath it granted anything at all,
    which is the trap `tests/test_ingress_terraform.py` documents at length.
    """
    assert AGENTCORE_TF.is_file(), f"{AGENTCORE_TF} is missing; this test pins nothing"
    text = AGENTCORE_TF.read_text()
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_the_comment_stripper_actually_removes_the_prose_arns():
    """The anti-vacuity guard for every assertion below.

    The grant's own comment block quotes both ARN shapes as examples. If
    `_code()` stopped stripping comments, `test_the_runtime_role_may_invoke_an_
    inference_profile` would pass on the PROSE while the `Resource` list said
    foundation-model only -- green, and wrong, which is the exact failure mode
    this file exists to prevent one layer down.
    """
    raw = AGENTCORE_TF.read_text()
    stripped = _code()
    assert "#" in raw, (
        f"{AGENTCORE_TF} contains no `#` comments at all, so this guard is "
        f"checking nothing and _code()'s stripping is untested"
    )
    assert "#" not in stripped, (
        "_code() left a `#` in its output; comments are no longer being stripped, "
        "so every assertion below may be matching prose rather than policy"
    )


def test_the_default_model_is_a_cross_region_inference_profile():
    """The premise. If this stops holding, the grant below may be over-broad.

    Asserted rather than assumed: a future default of a bare foundation-model id
    would make the inference-profile grant unnecessary, and an unnecessary IAM
    grant should be removed rather than left as decoration.
    """
    assert config.BEDROCK_MODEL.startswith(_PROFILE_PREFIXES), (
        f"BEDROCK_MODEL={config.BEDROCK_MODEL!r} no longer looks like an inference "
        f"profile id. If it is now a bare foundation-model id, the "
        f"inference-profile statement in the agentcore module is dead weight and "
        f"should be removed rather than left granting more than the code uses."
    )


def test_the_runtime_role_may_invoke_an_inference_profile():
    """Without this the model call is implicitly denied and every agent serves a fixture."""
    code = _code()
    assert "inference-profile" in code, (
        "the agentcore runtime role does not grant bedrock:InvokeModel on any "
        "inference-profile ARN. config.BEDROCK_MODEL is an inference profile "
        f"({config.BEDROCK_MODEL!r}), so InvokeModel is implicitDeny, llm.text() "
        "catches the denial, and every model-calling agent falls back to its "
        "fixture -- silently, with every job green. MEASURED 2026-08-22."
    )


def test_the_inference_profile_grant_is_scoped_to_this_account_and_region():
    """A wildcard here would grant every profile in every region."""
    code = _code()
    profile_arns = re.findall(r'"(arn:aws:bedrock:[^"]*inference-profile/[^"]*)"', code)
    assert profile_arns, (
        "no inference-profile ARN literal found, so this test cannot check its "
        "scope -- either the grant is missing or it was written in a form this "
        "matcher cannot see"
    )
    for arn in profile_arns:
        assert "${data.aws_region.current.region}" in arn or "us-east-1" in arn, (
            f"inference-profile ARN {arn!r} is not scoped to a region"
        )
        assert "${var.account_id}" in arn or "339712964409" in arn, (
            f"inference-profile ARN {arn!r} is not scoped to this account; an "
            f"empty account field would grant profiles this account does not own"
        )


def test_the_foundation_model_grant_is_still_present():
    """Both are needed: a profile fans out TO foundation models.

    Invoking a cross-region profile requires InvokeModel on the profile AND on
    the underlying foundation models it routes to. Removing either one restores
    the silent-fixture failure, so this test exists to stop a future reader
    "tidying" the pair down to one.
    """
    code = _code()
    assert "foundation-model" in code, (
        "the foundation-model grant is gone. An inference profile routes TO "
        "foundation models, so both grants are required; with only the profile "
        "ARN the call is still denied."
    )


def test_both_arn_shapes_sit_on_one_invokemodel_statement():
    """Two grants, one statement, and the same two actions apply to both.

    Split across two statements the pair still works -- but a statement granting
    the profile without `InvokeModelWithResponseStream` would deny a streaming
    call while the non-streaming one succeeded, which is a partial failure that
    reads as a model quirk rather than as IAM. Asserting they share a statement
    is the cheap way to keep the two actions and the two resources in step.
    """
    code = _code()
    match = re.search(
        r'Sid\s*=\s*"BedrockInvoke"(?P<body>.*?)\n\s*\},',
        code,
        re.DOTALL,
    )
    assert match, (
        "no `Sid = \"BedrockInvoke\"` statement found in the agentcore module. "
        "Either it was renamed -- in which case this test pins nothing -- or the "
        "Bedrock grant is gone entirely and every agent is back on its fixture."
    )
    body = match.group("body")
    assert "inference-profile" in body and "foundation-model" in body, (
        f"the BedrockInvoke statement does not carry both ARN shapes. A profile "
        f"is the thing CALLED and the foundation models are the things that "
        f"ANSWER; either grant alone is still a denial. Statement body:\n{body}"
    )
    for action in ("bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"):
        assert action in body, (
            f"the BedrockInvoke statement does not grant {action}; a streaming "
            f"call would be denied while the non-streaming one succeeded, which "
            f"reads as a model quirk rather than as an IAM gap"
        )


# --------------------------------------------------------------------------
# The check that was supposed to catch the denial, and could not.
#
# deploy.yml's smoke step asserted `grep -q '"tasks"'` on the invoke response --
# and fixtures/plan_result.json BEGINS with "tasks", on line 2. So the assertion
# passed identically for a fixture and a real completion, in the step whose own
# comment claimed to assert on content to avoid "the reassuring non-answer".
# That is how the IAM denial above shipped green for a week.
# --------------------------------------------------------------------------


def _smoke_step() -> str:
    """The `run` body of deploy.yml's planner-invoke step.

    Parsed out of the YAML rather than grepped over the whole file, because
    deploy.yml is heavily commented and this step's own comments now quote the
    strings under assertion. Asserts it found exactly one such step: a matcher
    keyed on a step that was renamed would otherwise take every assertion below
    green while checking nothing.
    """
    assert DEPLOY_YML.is_file(), f"{DEPLOY_YML} is missing; this test pins nothing"
    doc = yaml.safe_load(DEPLOY_YML.read_text())
    steps = [
        step
        for step in doc["jobs"]["deploy"]["steps"]
        if "invoke-agent-runtime" in (step.get("run") or "")
    ]
    assert len(steps) == 1, (
        f"expected exactly one step in deploy.yml's `deploy` job that invokes a "
        f"runtime, found {len(steps)}. Zero means nothing smoke-tests the deploy "
        f"-- READY is not the same as working -- and more than one means this "
        f"matcher is reading the wrong step."
    )
    return steps[0]["run"]


def _smoke_code() -> str:
    """The smoke step with its `#` comments stripped.

    MEASURED, and this helper exists because of it: a first version of
    `test_the_deploy_smoke_test_can_tell_a_fixture_from_a_model_answer` asserted
    the fixture's `notes` literal appeared anywhere in the step, and the step's
    own comment block QUOTES that literal as the measured command output. So
    changing the shell assignment to a different sentence -- which breaks the
    discriminator completely -- left all ten tests green, satisfied by the prose
    explaining the assignment.

    Same trap `tests/test_ingress_terraform.py` documents for HCL, reproduced
    here inside a single YAML step. Assertions about what the step DOES read this
    stripped form; assertions about what it EXPLAINS may read the raw text.
    """
    return "\n".join(line.split("#", 1)[0] for line in _smoke_step().splitlines())


def _fixture_note_assignment() -> str:
    """The value the step actually assigns to `fixture_note`.

    Extracted rather than substring-matched, so "the literal is mentioned
    somewhere in this step" and "the literal is the value the grep uses" are
    different failures. The first was satisfied by a comment.
    """
    code = _smoke_code()
    match = re.search(r"fixture_note=(?P<q>['\"])(?P<value>.*?)(?P=q)", code)
    assert match, (
        f"no `fixture_note=<literal>` assignment found in deploy.yml's smoke "
        f"step (comments stripped). Either the discriminator is gone -- in which "
        f"case the step passes on a fixture again -- or it was rewritten in a "
        f"form this matcher cannot see. Stripped step body:\n{code}"
    )
    value = match.group("value")
    assert value.strip(), "fixture_note is assigned an empty string; it would match nothing"
    return value


def _fixture_notes() -> str:
    """The plan fixture's `notes` value, read from the file."""
    assert PLAN_FIXTURE.is_file(), f"{PLAN_FIXTURE} is missing"
    notes = json.loads(PLAN_FIXTURE.read_text())["notes"]
    assert notes and notes.strip(), (
        "fixtures/plan_result.json has an empty `notes`; the deploy smoke test "
        "uses that literal as its fixture-vs-model discriminator, and an empty "
        "string would match every response"
    )
    return notes


def test_the_smoke_step_comment_stripper_erases_the_prose_that_quotes_the_literal():
    """The anti-vacuity guard for the smoke-step assertions, and it was EARNED.

    MEASURED while writing this file: the first version of
    `test_the_deploy_smoke_test_can_tell_a_fixture_from_a_model_answer` asserted
    the fixture's `notes` literal appeared anywhere in the step's `run` body. The
    step's own comment block quotes that literal as the measured output of the
    command that reads it -- so changing the shell assignment to a different
    sentence, which destroys the discriminator entirely, left all ten tests
    GREEN, satisfied by the prose explaining the assignment.

    That is the exact pattern CLAUDE.md records seven instances of: a matcher
    that cannot express the failing case. Caught only by running the mutation.
    """
    raw = _smoke_step()
    stripped = _smoke_code()
    notes = _fixture_notes()

    assert notes in raw, (
        f"the smoke step no longer mentions {notes!r} in its comments, so this "
        f"guard is checking nothing. The prose recording where the literal came "
        f"from is worth keeping; re-read _smoke_code's docstring if it went."
    )
    assert stripped.count(notes) == 1, (
        f"the fixture literal appears {stripped.count(notes)} times in the "
        f"comment-stripped smoke step, expected exactly 1 (the shell "
        f"assignment). More than one means the stripper stopped working and "
        f"assertions may match prose; zero means the assignment is gone."
    )


def test_the_old_tasks_grep_could_not_have_distinguished_fixture_from_model():
    """The MEASUREMENT, pinned so the reasoning below cannot go stale.

    `"tasks"` is in fixtures/plan_result.json. A response carrying the fixture
    verbatim satisfies `grep -q '"tasks"'` exactly as a real plan does, so that
    assertion alone could never fail on the fixture path -- which is what makes
    it the wrong discriminator, not merely a weak one.
    """
    fixture_text = PLAN_FIXTURE.read_text()
    assert '"tasks"' in fixture_text, (
        "fixtures/plan_result.json no longer contains the literal '\"tasks\"'. "
        "If the fixture's shape changed, re-derive the deploy smoke test's "
        "discriminator rather than trusting the reasoning recorded here."
    )


def test_the_deploy_smoke_test_can_tell_a_fixture_from_a_model_answer():
    """The fix: the value the step greps for must BE the fixture's `notes` literal.

    Compared against the extracted shell assignment, not against the step text,
    because the step's comments quote the same literal -- see `_smoke_code`. And
    compared for EQUALITY with the fixture file, so if the fixture is regenerated
    with different prose this fails: the discriminator would be stale and the
    step would silently start passing on a fixture again.
    """
    assigned = _fixture_note_assignment()
    notes = _fixture_notes()
    assert assigned == notes, (
        f"deploy.yml's smoke step greps for {assigned!r} but the plan fixture's "
        f"`notes` is {notes!r}. The step therefore cannot recognise the fixture, "
        f"and `grep -q '\"tasks\"'` alone cannot either: the fixture BEGINS with "
        f"\"tasks\". That is how bedrock:InvokeModel being implicitDeny on the "
        f"inference profile shipped green for a week."
    )


def test_the_smoke_test_fails_rather_than_passes_when_it_sees_the_fixture():
    """Detecting the fixture must be a RED job, not a notice.

    Recognising the fixture and then exiting 0 would be strictly worse than the
    old check: it would prove the code knows the difference and chose to ignore
    it. The negated grep in the pass condition is the other half -- without it a
    response containing BOTH "tasks" and the fixture note would pass on the first
    branch before the failure branch was ever reached.
    """
    code = _smoke_code()

    assert re.search(r"!\s*grep\s+-qF", code), (
        "the smoke step has no negated fixed-string grep. The pass condition must "
        "require the fixture's note to be ABSENT; a response carrying both "
        "'\"tasks\"' and the fixture note would otherwise pass before any "
        "fixture check ran."
    )

    # The failure branch must exist, and must exit non-zero.
    fixture_branch = re.search(
        r"if grep -qF [^\n]*\n(?P<body>(?:.*\n)*?)\s*fi", code
    )
    assert fixture_branch, (
        f"no `if grep -qF ...` branch found in the smoke step, so nothing acts on "
        f"recognising the fixture. Stripped step body:\n{code}"
    )
    body = fixture_branch.group("body")
    assert "exit 1" in body, (
        f"the fixture-detection branch does not `exit 1`. Recognising the fixture "
        f"and continuing is worse than not recognising it: the job would prove it "
        f"knew the difference and reported green anyway. Branch body:\n{body}"
    )
    assert "::error::" in body, (
        "the fixture-detection branch emits no ::error:: annotation, so the cause "
        "is not visible in the Actions UI where somebody is looking for it"
    )

    # And the message must point at the actual cause, not just say "fixture".
    assert "inference-profile" in body or "InvokeModel" in body, (
        f"the smoke step's failure message does not name the IAM grant to check. "
        f"'The planner answered with its fixture' without naming "
        f"bedrock:InvokeModel on the inference-profile ARN sends the next reader "
        f"to the container logs instead of to the policy. Branch body:\n{body}"
    )


def test_the_fixed_string_grep_is_F_because_the_note_contains_a_regex_metachar():
    """`grep -qF`, not `grep -q`, and the reason is in the literal itself.

    The fixture's note ends in `.`, which a basic regex reads as "any character".
    A non-fixed match would accept a response differing in that position, which
    weakens the one discriminator this step has. Cheap to get right, invisible
    when wrong.
    """
    notes = _fixture_notes()
    assert re.search(r"[.*+?\[\]^$\\]", notes), (
        f"the fixture's notes literal {notes!r} no longer contains a regex "
        f"metacharacter, so the -F requirement below is no longer load-bearing. "
        f"Re-read the reasoning before relaxing it."
    )
    code = _smoke_code()
    greps = re.findall(r"grep\s+(-[a-zA-Z]+)\s+\"\$fixture_note\"", code)
    assert greps, (
        f"no `grep <flags> \"$fixture_note\"` found in the smoke step, so this "
        f"test cannot check the flags. Stripped step body:\n{code}"
    )
    for flags in greps:
        assert "F" in flags, (
            f"the fixture-note grep uses flags {flags!r} without F. The note "
            f"contains a regex metacharacter, so an unanchored basic-regex match "
            f"would accept responses the fixed-string form correctly rejects."
        )




def _bedrock_invoke_statement() -> str:
    """The BedrockInvoke statement body, or a loud failure.

    Shared by the action tests below so a rename of the Sid fails once, visibly,
    rather than making several assertions match nothing each.
    """
    match = re.search(
        r'Sid\s*=\s*"BedrockInvoke"(?P<body>.*?)\n\s*\},', _code(), re.DOTALL
    )
    assert match, (
        "no `Sid = \"BedrockInvoke\"` statement in the agentcore module -- either "
        "renamed, in which case these tests pin nothing, or gone, in which case "
        "every agent is back on its fixture."
    )
    return match.group("body")


# ── the action name, which is a SEPARATE mistake from the ARN shape ────────────
#
# MEASURED 2026-08-22, after the inference-profile grant above had already made
# `bedrock:InvokeModel` read `allowed`: the runtimes STILL served fixtures, and the
# container log named the operation nobody had granted --
#
#   botocore.errorfactory.AccessDeniedException: An error occurred
#   (AccessDeniedException) when calling the ConverseStream operation:
#   User: .../theagentorg-shared-agentcore-runtime-role/BedrockA...
#   └ Model id: us.amazon.nova-2-lite-v1:0
#
# Simulated against the same profile ARN, all four actions at once:
#
#   bedrock:InvokeModel                      allowed
#   bedrock:InvokeModelWithResponseStream    allowed
#   bedrock:Converse                         implicitDeny
#   bedrock:ConverseStream                   implicitDeny
#
# TWO INDEPENDENT THINGS WERE WRONG -- the ARN shape and the action name -- and
# fixing the first is what made the second visible, because until then every call
# failed at the earlier check. That is the shape worth remembering: a fix that
# turns one silent failure into a different silent failure looks like no progress
# at all unless something reads the log.


def test_the_grant_names_the_action_strands_actually_calls():
    """`strands.Agent` streams through Converse, not InvokeModel.

    Converse is a separate IAM action, not an alias for InvokeModel and not
    implied by it. Granting only the Invoke pair grants two actions this codebase
    never calls, while denying the one it does -- and the denial surfaces as every
    agent quietly serving its fixture with every job green.
    """
    body = _bedrock_invoke_statement()
    for action in ("bedrock:Converse", "bedrock:ConverseStream"):
        assert action in body, (
            f"the BedrockInvoke statement does not grant {action!r}. `strands.Agent`"
            f" calls the Converse API, so without this every model call is "
            f"AccessDeniedException, llm.text() catches it, and all four "
            f"model-calling agents fall back to their fixtures -- silently, with "
            f"every job green. MEASURED: this exact denial survived the "
            f"inference-profile fix. Statement body:\n{body}"
        )


def test_both_converse_forms_are_granted_not_just_the_streaming_one():
    """Which form the SDK picks is its choice, not ours.

    `ConverseStream` is what strands uses today. An SDK upgrade that switched to
    the non-streaming `Converse` would reintroduce the identical silent fixture
    fallback, so both are granted rather than the one currently observed.
    """
    body = _bedrock_invoke_statement()
    assert "bedrock:Converse\"" in body or "bedrock:Converse'" in body, (
        f"only the streaming form is granted. `bedrock:Converse` must be there "
        f"too: an SDK that stopped streaming would fall straight back to fixtures "
        f"with nothing turning red. Statement body:\n{body}"
    )


def test_the_foundation_model_grant_spans_every_region_the_profile_routes_to():
    """A cross-region profile routes across regions. The grant must follow it.

    MEASURED 2026-08-22 with `get-inference-profile`, the profile named by
    `config.BEDROCK_MODEL` fans out to THREE foundation models:

        arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-lite-v1:0
        arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-2-lite-v1:0
        arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-2-lite-v1:0

    Scoped to one region, with the Converse actions already granted:

        foundation-model in us-east-1   allowed
        foundation-model in us-east-2   implicitDeny
        foundation-model in us-west-2   implicitDeny

    So the call succeeded or failed on which region the profile happened to pick,
    and the failure was indistinguishable from every other denial in this
    sequence: the agent served its fixture and the job went green.

    THE ACCOUNT SCOPE IS ASSERTED SEPARATELY, on the inference-profile ARN, by
    `test_the_inference_profile_grant_is_scoped_to_this_account_and_region`. That
    is the resource that must be ours; a foundation model is AWS's and carries no
    account field at all.
    """
    body = _bedrock_invoke_statement()
    region_scoped = re.findall(
        r'"arn:aws:bedrock:(?!\*)[a-z0-9-]+::foundation-model/', body
    )
    assert not region_scoped, (
        f"the foundation-model grant is pinned to a single region "
        f"({region_scoped}). A `us.` inference profile routes to models in "
        f"several regions and Bedrock checks permission on whichever it picks, so "
        f"a single-region grant denies the call whenever the profile chooses "
        f"another one -- and the agent then serves its fixture with the job green. "
        f"Statement body:\n{body}"
    )
    assert '"arn:aws:bedrock:*::foundation-model/' in body, (
        f"no cross-region foundation-model ARN found. Enumerating today's three "
        f"regions would break silently the day AWS adds a fourth to the profile, "
        f"which is the same defect rediscovered. Statement body:\n{body}"
    )
