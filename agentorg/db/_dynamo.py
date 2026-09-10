"""Key construction for the single tenancy table. PURE FUNCTIONS, NO AWS CALL.

`docs/design/dynamodb-migration.md` is the design; this is step 2 of it. Nothing
here opens a client, so the hermetic suite drives all of it -- the same split
`infra/cognito/spec.py` and `infra/amplify/spec.py` use, and for the same reason:
everything worth pinning about a key layout is a property of the STRINGS, and a
module that builds a boto3 client cannot be imported by a suite with no
credentials.

THE PARTITION KEY IS THE TENANT. Every scoped row lives at
`TENANT#<tenant_id>`, and the row TYPE is the sort key's leading token:

    TENANT#<tenant_id>    ORG                   organisation
    TENANT#<tenant_id>    MEMBER#<user_id>      membership
    TENANT#<tenant_id>    REPO#<full_name>      repository
    TENANT#<tenant_id>    RUN#<run_id>          run
    TENANT#<tenant_id>    SECRET#<name>         secret
    TENANT#<tenant_id>    BUDGET                budget
    TENANT#<tenant_id>    JOB#<job_id>          queue_jobs
    USER#<user_id>        PROFILE               app_user  -- NOT tenant-scoped

That layout is not a convenience. `dynamodb:LeadingKeys` compares against the
partition key of the request, so this is the only shape IAM can defend; a table
keyed on anything else pushes isolation back into application code.
"""

from __future__ import annotations

# The separator. `#` is conventional in single-table DynamoDB and is deliberately
# a character that CANNOT APPEAR IN A TENANT ID -- see `refuse_unusable_tenant`,
# which is where that rule is enforced rather than assumed.
SEP = "#"

TENANT_PREFIX = "TENANT"
USER_PREFIX = "USER"

# Sort-key tokens. Two of these are BARE LITERALS rather than prefixes, because
# their row is a singleton within the tenant: an organisation has one identity
# row and a tenant has one budget. Writing them as `ORG#` with an empty tail
# would make `begins_with(sk, "ORG")` also match a future `ORGUNIT#`, which is
# the kind of thing that reads as correct and returns the wrong set.
SK_ORG = "ORG"
SK_BUDGET = "BUDGET"
SK_PROFILE = "PROFILE"

SK_MEMBER = "MEMBER"
SK_REPO = "REPO"
SK_RUN = "RUN"
SK_SECRET = "SECRET"
SK_JOB = "JOB"

# ── the constraint that comes from IAM, not from DynamoDB ────────────────────
#
# **A TENANT ID MUST BE A LEGAL IAM TAG VALUE, AND THAT IS STRICTER THAN
# DYNAMODB'S RULE.** The policy in `infra/Terraform/modules/tenancy/iam.tf`
# compares the partition key against `TENANT#${aws:PrincipalTag/tenant}`, so the
# tenant id travels as an IAM session tag -- and IAM tag values admit only
# letters, digits, spaces and `_ . : / = + - @`. `#` is NOT among them.
#
# So a tenant id containing `#` cannot be carried as a tag at all: the
# `AssumeRole` call fails at STS, long before DynamoDB is reached, and the error
# names the tag rather than the id. Refusing here makes the failure arrive at the
# layer that can explain it.
#
# The empty string is refused for a different and sharper reason. An unset tag
# makes the policy compare against `TENANT#`, which is a REAL PARTITION -- so if
# this module were willing to build `TENANT#` for an empty tenant id, a session
# with no tag would be authorised for exactly the rows an empty tenant id writes.
# The IAM side already fails closed on that; refusing here means the two layers
# agree rather than one depending on the other.
_TAG_SAFE_PUNCTUATION = frozenset("_.:/=+-@ ")


def refuse_unusable_tenant(tenant_id: str) -> None:
    """Raise unless `tenant_id` can survive the whole path to DynamoDB.

    Called by every key builder below rather than trusted to callers, because
    the cost of a bad id is not a crash: it is a row written into a partition
    nobody can read back, or a session tag that silently authorises the wrong
    set.
    """
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError(
            "tenant_id is empty. An empty id builds the partition `TENANT#`, "
            "which is the SAME partition an untagged IAM session is authorised "
            "for -- so an unscoped caller and an unnamed tenant would share "
            "rows. `agentorg/tenancy/tenant_zero.py` translates a blank "
            "RunState.tenant_id to tenant zero; nothing may reach this layer "
            "still blank."
        )
    bad = {
        c for c in tenant_id
        if not (c.isalnum() or c in _TAG_SAFE_PUNCTUATION)
    }
    if bad:
        raise ValueError(
            f"tenant_id {tenant_id!r} contains {sorted(bad)!r}, which IAM "
            "refuses in a session tag value (letters, digits, spaces and "
            "_ . : / = + - @ only). The tenant travels to DynamoDB AS a tag, so "
            "this id could never be scoped -- AssumeRole would fail at STS with "
            "a message about the tag rather than about the tenant."
        )


def tenant_pk(tenant_id: str) -> str:
    """`TENANT#<tenant_id>` -- the partition every scoped row lives in."""
    refuse_unusable_tenant(tenant_id)
    return f"{TENANT_PREFIX}{SEP}{tenant_id}"


def tenant_from_pk(pk: str) -> str:
    """The tenant id back out of a partition key, or raise.

    THE INVERSE IS DECLARED BECAUSE GSI1 NEEDS IT. That index is keyed on a run
    id with no tenant in it, so a caller who queries it gets back a `pk` and has
    to compare it against their own scope -- and a comparison written inline at
    each call site is a comparison that will eventually be written differently at
    one of them. `split` on the FIRST separator only, so a tenant id is returned
    whole even if the rule above is ever relaxed.
    """
    prefix = f"{TENANT_PREFIX}{SEP}"
    if not pk.startswith(prefix):
        raise ValueError(
            f"{pk!r} is not a tenant partition key. `USER#` partitions are "
            "deliberately outside tenant scope and have no tenant to return."
        )
    return pk[len(prefix):]


def user_pk(user_id: str) -> str:
    """`USER#<user_id>` -- app_user, which is NOT tenant-scoped.

    `schema.py` states the reason in the table's own `unscoped_reason`: one
    person may hold memberships in several organisations, so no single tenant
    owns the row, and it is unreachable from tenant scope because `membership`
    is the only route in and that table IS scoped.

    `LeadingKeys` does not defend this partition. Nothing about that is new --
    Postgres RLS did not defend it either, because it carries no `tenant_id`
    column to compare.
    """
    if not user_id:
        raise ValueError("user_id is empty; `USER#` is not a row anyone owns.")
    return f"{USER_PREFIX}{SEP}{user_id}"


def sk(token: str, identifier: str = "") -> str:
    """A sort key: a bare token, or `TOKEN#<identifier>`.

    The identifier is NOT validated the way a tenant id is, and that asymmetry
    is deliberate. A tenant id must survive an IAM tag; a repository's
    `full_name` or a secret's name only has to be a DynamoDB string, and those
    admit almost anything. Constraining them here would refuse legitimate values
    -- `owner/repo` carries a `/` by construction -- for a safety property that
    is not at stake, since the sort key is never compared against a tag.
    """
    if not token:
        raise ValueError("a sort key needs a token")
    if not identifier:
        # A bare token is only correct for the three SINGLETON rows. Anything
        # else silently collapses every row of that type in the tenant onto one
        # key, so the LAST write wins and the others vanish with nothing raised
        # -- PutItem replaces at a (pk, sk) pair, the same hazard
        # `modules/state` documents for its own sort key.
        if token not in {SK_ORG, SK_BUDGET, SK_PROFILE}:
            raise ValueError(
                f"{token!r} needs an identifier. Only {sorted((SK_ORG, SK_BUDGET, SK_PROFILE))} "
                "are singletons within their partition; a bare key for anything "
                "else makes every row of that type overwrite the last one."
            )
        return token
    return f"{token}{SEP}{identifier}"
