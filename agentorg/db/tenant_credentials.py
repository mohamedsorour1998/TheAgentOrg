"""Credentials that can only read one tenant. Step 6/7 of the migration plan.

`docs/design/dynamodb-migration.md` §4 is the argument; this is the half that
runs. `infra/Terraform/modules/tenancy/iam.tf` writes the policy, and a policy
nothing assumes is a policy nothing enforces -- so this module is the seam where
the chain the design argues for either holds or quietly does not:

    Cognito custom:tenant (immutable)  ->  STS session tag  ->  dynamodb:LeadingKeys

**WHAT MAKES THIS STRONGER THAN WHAT IT REPLACES.** Under Postgres, isolation was
`SET agentorg.tenant_id` on a connection, and any code holding that connection
could set it again. Here the tenant is baked into a credential at mint time: the
returned keys are *unable* to read another partition, so the enforcement survives
a bug in every line of Python written after this one. That is the whole point of
moving it out of application code, and it is why this module refuses rather than
degrades in all three of the cases below.

THREE REFUSALS, AND EACH ONE HAS A FAIL-OPEN VERSION THAT READS AS CORRECT CODE:

  no tenant_id      ->  raise. `_dynamo.refuse_unusable_tenant` owns that rule,
                        because an empty id builds `TENANT#`, the same partition
                        an untagged session is authorised for.
  no role ARN       ->  raise. **NEVER fall back to ambient credentials.** The
                        fallback is one line, passes every test, and hands the
                        caller the SSR compute role -- which can reach the table
                        across every tenant. A leak that only appears when a
                        variable is unset is the worst shape available here.
  STS says no       ->  raise, with the tag named. The tempting fix for an STS
                        AccessDenied is to drop the `Tags` argument, which
                        succeeds and mints a session whose
                        `aws:PrincipalTag/tenant` is empty -- so LeadingKeys
                        compares against `TENANT#`, matches nothing, and the
                        symptom is "the database is broken" rather than "the
                        role is missing sts:TagSession".

**NO BOTO3 AT MODULE SCOPE.** `_dynamo.py` states the split this follows: the key
layout is pure functions the hermetic suite drives, and anything that opens a
client is imported inside the function. Here it also matters for a second reason
-- `tests/conftest.py`'s six autouse guards force the offline path, and a module
that built an STS client at import would make every test collecting this package
reach for credentials.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..common import config
from ._dynamo import refuse_unusable_tenant

# The tag key. ONE SPELLING, and it must equal the one the policy compares
# against -- `infra/Terraform/modules/tenancy/iam.tf` authorises
# `TENANT#${aws:PrincipalTag/tenant}`. A mismatch here does not raise anywhere:
# STS happily accepts a tag called `tenant_id`, the session carries it, and
# `aws:PrincipalTag/tenant` is then empty, so LeadingKeys matches nothing and
# every read comes back AccessDenied for a reason no error message mentions.
TAG_KEY = "tenant"

# Seconds of validity to discard before expiry. A credential that is valid when
# checked and expired when used is the classic shape here: the DynamoDB call
# happens some milliseconds later, and a retry inside botocore can land further
# out still. 120s is generous against a 3600s lifetime and costs one extra
# AssumeRole every hour per tenant.
_EXPIRY_MARGIN_SECONDS = 120

# The floor AWS enforces on `DurationSeconds` for a role-chaining call, and the
# value `modules/tenancy` sets as the role's `max_session_duration`. Asking for
# more than the role permits is an STS error, not a silent truncation.
_SESSION_SECONDS = 3600


@dataclass(frozen=True)
class TenantCredentials:
    """One tenant's keys, and when they stop working.

    FROZEN, so a caller cannot retag a credential by assignment. The tenant is
    carried alongside the keys rather than inferred, because the only other way
    to answer "whose credential is this?" is to call `sts:GetCallerIdentity`,
    which costs a round trip to learn something this process already knew.
    """

    tenant_id: str
    access_key_id: str
    secret_access_key: str
    session_token: str
    expires_at: float

    def is_fresh(self, now: float | None = None) -> bool:
        """True while these keys have more than the margin left.

        Takes `now` so a test can express expiry without sleeping, which is the
        only way a cache-eviction path gets exercised at all -- an hour-long
        credential is otherwise fresh for the whole suite.
        """
        now = time.time() if now is None else now
        return now < self.expires_at - _EXPIRY_MARGIN_SECONDS


# Keyed by tenant id. **THE KEY IS NOT OPTIONAL AND IT IS NOT A DETAIL.** A cache
# keyed on nothing -- a single module-level `_CREDENTIALS` -- is the leak this
# whole subsystem exists to prevent, arriving through the optimisation: the first
# request mints tenant A's keys, every later request reuses them, and tenant B
# reads A's rows with the correct policy applied to the wrong tag. Nothing raises,
# and the rows come back.
_CACHE: dict[str, TenantCredentials] = {}

# The readers are short-lived subprocesses, but `scripts/worker.py` is not, and
# under Amplify's SSR runtime one process serves concurrent requests for
# different tenants. Two threads missing the cache for the same tenant is merely
# a wasted AssumeRole; two threads interleaving a write is a dict left holding a
# credential under the wrong key.
_LOCK = threading.Lock()


def _assume(tenant_id: str, role_arn: str) -> TenantCredentials:
    """One real STS call. The only place this module talks to AWS.

    `RoleSessionName` carries the tenant so a CloudTrail reader can attribute a
    DynamoDB call without joining anything -- it is the one field of an assumed
    session that shows up in the caller identity. It is prefixed rather than bare
    so a session name can never be mistaken for a role name, and the tenant id is
    already known to be tag-safe, which is a strict subset of what a session name
    admits.
    """
    import boto3
    from botocore.exceptions import ClientError

    sts = boto3.client("sts", region_name=config.AWS_REGION)
    try:
        answer = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=f"tenant-{tenant_id}"[:64],
            DurationSeconds=_SESSION_SECONDS,
            # THE TAG IS THE AUTHORISATION. Without this argument the call still
            # succeeds and returns credentials that can read nothing -- see the
            # module docstring. It is not an optimisation and it is not optional.
            Tags=[{"Key": TAG_KEY, "Value": tenant_id}],
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise PermissionError(
            f"could not assume {role_arn} as tenant {tenant_id!r} ({code}). "
            f"If this is AccessDenied, the caller most likely holds "
            f"`sts:AssumeRole` and NOT `sts:TagSession` -- the tenancy module's "
            f"trust policy requires both. DO NOT FIX IT BY DROPPING THE TAG: "
            f"that call succeeds, and the session it returns has an empty "
            f"`aws:PrincipalTag/{TAG_KEY}`, so every DynamoDB read is refused "
            f"for a reason nothing reports."
        ) from exc

    creds = answer["Credentials"]
    return TenantCredentials(
        tenant_id=tenant_id,
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
        # `Expiration` is a timezone-aware datetime from botocore. `.timestamp()`
        # rather than arithmetic on naive datetimes, which is how a UTC offset
        # becomes a credential believed fresh for an extra hour.
        expires_at=creds["Expiration"].timestamp(),
    )


def credentials(tenant_id: str, role_arn: str | None = None) -> TenantCredentials:
    """Keys that can read `tenant_id`'s partition and no other.

    `role_arn` defaults to `config.TENANT_SCOPED_ROLE_ARN` and is a parameter so
    a caller can name a different role explicitly -- never so it can be omitted
    into ambient credentials. An unset ARN raises here.
    """
    refuse_unusable_tenant(tenant_id)

    role_arn = config.TENANT_SCOPED_ROLE_ARN if role_arn is None else role_arn
    if not role_arn:
        raise RuntimeError(
            "TENANT_SCOPED_ROLE_ARN is empty, so there is no role to assume and "
            "no tenant-scoped credential to mint. This REFUSES rather than "
            "falling back to the ambient credential on purpose: the fallback "
            "would hand back this process's own role, which can read every "
            "tenant's rows, and would do so only on the machines where the "
            "variable is unset. Set it from the tenancy module's "
            "`tenant_scoped_role_arn` output."
        )

    with _LOCK:
        cached = _CACHE.get(tenant_id)
        if cached is not None and cached.is_fresh():
            return cached

    # OUTSIDE THE LOCK. An STS round trip under a module-wide lock would
    # serialise every tenant's first request behind whichever one arrived first,
    # and the worst case of releasing it is two concurrent AssumeRole calls for
    # the same tenant -- which costs one extra call and returns two credentials
    # that are equally correct.
    minted = _assume(tenant_id, role_arn)

    with _LOCK:
        _CACHE[tenant_id] = minted
    return minted


def session(tenant_id: str, role_arn: str | None = None):
    """A boto3 `Session` bound to one tenant.

    Returned rather than a client, because a caller needing two clients (STS and
    DynamoDB, say) must get both from the same credential -- building a second
    client from the default chain beside a scoped one is how half a code path
    ends up unscoped while the other half looks right.
    """
    import boto3

    creds = credentials(tenant_id, role_arn)
    return boto3.Session(
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
        aws_session_token=creds.session_token,
        region_name=config.AWS_REGION,
    )


def table(tenant_id: str, role_arn: str | None = None):
    """The tenancy table, reachable only for `tenant_id`.

    The shape every accessor wants. Note it still takes `tenant_id` on each
    accessor call afterwards: this handle constrains what the CALL IS PERMITTED
    to touch, and the accessor's own `TENANT#<id>` key decides what it ASKS for.
    Two layers, and the design needs both -- IAM cannot make a caller ask the
    right question, and application code cannot stop it asking the wrong one.
    """
    return session(tenant_id, role_arn).resource("dynamodb").Table(config.TENANCY_TABLE)


def _reset_cache() -> None:
    """Drop every cached credential. FOR TESTS AND FOR `worker.py`.

    The worker runs one stage per job and jobs change tenant, so a long-lived
    process that never evicts holds one live credential per tenant it has ever
    served. Called between jobs, this keeps that set to the tenants currently in
    flight -- which matters less for memory than for the window on a credential
    that outlives the work it was minted for.
    """
    with _LOCK:
        _CACHE.clear()
