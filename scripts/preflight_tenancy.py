"""Preflight check 8: does `dynamodb:LeadingKeys` actually refuse another tenant?

Step 4 of `docs/design/dynamodb-migration.md`, and the second of the two leak
attempts §6 requires. The first is hermetic and lives in
`tests/test_dynamo_store.py` (the GSI1 breach); this one cannot be, because the
thing under test is an IAM policy evaluated by AWS.

**WHY A SIMULATION AND NOT A GREEN APPLY.** `preflight.py` check 1 states the
rule this file follows: an apply proves the policy was WRITTEN, and only
`simulate-*` proves it PERMITS -- or here, REFUSES -- the call. That distinction
already cost this project a week, when three IAM defects on one statement each
hid the next and every agent served a fixture while every job stayed green.

**WHY A SIMULATION AND NOT A REAL `AssumeRole` + `GetItem`.** A live round trip
would need the tenant-scoped role to exist, which is count-gated off until
somebody is configured to assume it, and would mint real credentials to prove a
negative. `simulate-custom-policy` evaluates the deployed policy document against
a synthetic request, so it can ask the question that matters -- *what would
happen if a session tagged `t1` reached for `t2`* -- without creating a session
that could.

Kept OUT of the test suite deliberately. `tests/` is hermetic by construction:
six autouse guards force the offline path and put a loud raiser on every seam. A
test making live IAM calls would be the first exception, and the reason this
script exists at all is that some questions can only be answered against the
account.
"""

from __future__ import annotations

import json

from scripts.preflight import CheckFailed, _aws

# The policy whose whole purpose is this refusal.
POLICY_NAME = "theagentorg-shared-tenancy-scoped"
TABLE_NAME = "theagentorg-tenancy"

# The one that must be ALLOWED. Without it, a policy that refused every request
# would pass every assertion below -- the positive control this repository
# requires beside any zero, and the same argument as
# `test_the_attack_documents_are_actually_retrieved`.
_TENANT = "t1"
_OTHER = "t2"


def _policy_document(account: str) -> str:
    """The DEPLOYED policy, read back from IAM as a compact JSON string.

    Read from the ACCOUNT rather than rendered from the Terraform source, which
    is the entire point: a policy that exists in a `.tf` file and was never
    applied is precisely the failure this check exists to catch.

    Through `_aws`, not boto3, following that helper's own stated reason -- the
    command is the thing a reader re-runs by hand from this script's output, so
    the script runs the same command rather than a boto3 equivalent that happens
    to agree.
    """
    arn = f"arn:aws:iam::{account}:policy/{POLICY_NAME}"
    try:
        version = _aws(
            "iam", "get-policy", "--policy-arn", arn,
            "--query", "Policy.DefaultVersionId", "--output", "text",
        )
    except CheckFailed as exc:
        raise CheckFailed(
            f"{POLICY_NAME} was not found in {account}. The tenancy module has "
            f"not been applied, so NOTHING is constraining the web path to its "
            f"own tenant.\n{exc}"
        ) from exc

    raw = _aws(
        "iam", "get-policy-version", "--policy-arn", arn,
        "--version-id", version, "--query", "PolicyVersion.Document",
        "--output", "json",
    )
    # The CLI decodes the url-encoded document into JSON; `simulate-custom-policy`
    # wants it back as a single string, so it is re-serialised compactly here.
    return json.dumps(json.loads(raw))


def _decide(policy: str, table_arn: str, tag: str | None, leading_key: str,
            action: str = "dynamodb:GetItem", resource: str | None = None) -> str:
    """One simulated request, returning AWS's own verdict word.

    The three verdicts that matter are `allowed`, `implicitDeny` (nothing grants
    it) and `explicitDeny` (something forbids it). This function does not
    collapse the last two -- see the index row in the caller for why the
    difference is load-bearing.
    """
    context = [
        (
            f"ContextKeyName=dynamodb:LeadingKeys,ContextKeyType=stringList,"
            f"ContextKeyValues={leading_key}"
        )
    ]
    if tag is not None:
        context.append(
            f"ContextKeyName=aws:PrincipalTag/tenant,ContextKeyType=string,"
            f"ContextKeyValues={tag}"
        )

    return _aws(
        "iam", "simulate-custom-policy",
        "--policy-input-list", policy,
        "--action-names", action,
        "--resource-arns", resource or table_arn,
        "--context-entries", *context,
        "--query", "EvaluationResults[0].EvalDecision", "--output", "text",
    )


def check_leading_keys_refuses_another_tenant(account: str, region: str = "us-east-1") -> str:
    """Four questions, and the FIRST one must be answered `allowed`.

    | request                          | required        | why it matters |
    |----------------------------------|-----------------|----------------|
    | tag=t1 reaching for TENANT#t1    | allowed         | the control    |
    | tag=t1 reaching for TENANT#t2    | denied          | THE BREACH     |
    | NO TAG reaching for TENANT#t1    | denied          | fails closed   |
    | tag=t1 querying gsi1             | EXPLICIT deny   | index gap      |

    The last row is the subtle one. `LeadingKeys` compares against the BASE
    table's partition key, so it does not constrain an index query at all --
    `gsi1` is keyed on a run id with no tenant in it. The module answers that
    with an explicit `Deny`, and this check requires the verdict to be
    `explicitDeny` rather than merely `implicitDeny`: an implicit deny would mean
    the request is refused only because nothing allows it, which stops being true
    the moment somebody widens a resource list while fixing a permissions error.
    """
    policy = _policy_document(account)
    table_arn = f"arn:aws:dynamodb:{region}:{account}:table/{TABLE_NAME}"
    lines: list[str] = []
    problems: list[str] = []

    control = _decide(policy, table_arn, _TENANT, f"TENANT#{_TENANT}")
    lines.append(f"tag={_TENANT:3} -> TENANT#{_TENANT:16} {control}")
    if control != "allowed":
        problems.append(
            f"the POSITIVE CONTROL failed: a session tagged {_TENANT!r} may not read "
            f"its own partition ({control}). Every refusal below is then satisfied "
            f"by a policy that refuses everything, and the web app has no database "
            f"access at all."
        )

    breach = _decide(policy, table_arn, _TENANT, f"TENANT#{_OTHER}")
    lines.append(f"tag={_TENANT:3} -> TENANT#{_OTHER:16} {breach}")
    if breach == "allowed":
        problems.append(
            f"A SESSION TAGGED {_TENANT!r} CAN READ TENANT {_OTHER!r}. The "
            f"LeadingKeys condition is absent or does not bind; tenant isolation "
            f"for the web path is not enforced."
        )

    untagged = _decide(policy, table_arn, None, f"TENANT#{_TENANT}")
    lines.append(f"no tag   -> TENANT#{_TENANT:16} {untagged}")
    if untagged == "allowed":
        problems.append(
            "AN UNTAGGED SESSION IS ALLOWED. `sts:TagSession` is easy to omit, and "
            "the tempting fix for the resulting AccessDenied is to drop the tag -- "
            "which must fail closed, not open."
        )

    index = _decide(
        policy, table_arn, _TENANT, f"TENANT#{_TENANT}",
        action="dynamodb:Query", resource=f"{table_arn}/index/gsi1",
    )
    lines.append(f"tag={_TENANT:3} -> Query gsi1{'':10} {index}")
    if index != "explicitDeny":
        problems.append(
            f"index access is {index!r}, not 'explicitDeny'. LeadingKeys does not "
            f"defend an index -- gsi1 is keyed on a run id with no tenant in it -- "
            f"so only an explicit Deny keeps it shut. An implicit deny disappears "
            f"the moment a resource list is widened."
        )

    report = "\n".join(f"  {line}" for line in lines)
    if problems:
        raise CheckFailed(report + "\n  " + "\n  ".join(problems))
    return report
