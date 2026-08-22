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

import re
from pathlib import Path

from agentorg.common import config

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTCORE_TF = REPO_ROOT / "infra" / "Terraform" / "modules" / "agentcore" / "main.tf"

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
