"""Key construction for the tenancy table. Step 2 of the DynamoDB plan.

WHAT THIS FILE DEFENDS. `agentorg/db/_dynamo.py` builds the strings that
`dynamodb:LeadingKeys` compares against. That IAM condition is the whole
isolation guarantee for the web path, and it is a STRING COMPARISON between a
policy written in Terraform and a key built in Python -- two declarations of one
fact, in two languages, in two files nobody diffs against each other.

So the load-bearing test here is not any single refusal; it is
`test_the_partition_prefix_matches_the_iam_policy`, which reads the policy and
asserts the two agree. Everything else guards a way the key could be built such
that the comparison still succeeds and means the wrong thing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentorg.db import _dynamo

IAM_POLICY = REPO_ROOT / "infra" / "Terraform" / "modules" / "tenancy" / "iam.tf"


def _hcl_without_comments(source: str) -> str:
    """HCL with every `#` comment removed.

    NOT OPTIONAL, AND THE REASON IS THIS REPOSITORY'S MOST-REPEATED DEFECT. The
    policy file explains the key layout at length, so the literal `TENANT#`
    appears in its prose several times over. A test grepping the raw text would
    be satisfied by the COMMENTARY EXPLAINING THE RULE while the rule itself had
    changed -- found fifteen times here already, twice in a single lane.

    `#` also opens a comment in HCL, which is the same character the key
    separator uses, so the stripper has to work line-wise from the first `#`
    that is not inside a string. Crude, and the anti-vacuity assertion below is
    what keeps it honest.
    """
    out = []
    for line in source.splitlines():
        in_string = False
        cut = len(line)
        for i, char in enumerate(line):
            if char == '"':
                in_string = not in_string
            elif char == "#" and not in_string:
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def test_the_comment_stripper_still_leaves_the_policy_behind():
    """The anti-vacuity check for the test below.

    If `_hcl_without_comments` ever over-matches and blanks the file, every
    assertion built on it passes against nothing -- which is exactly how a
    stripped-source test becomes a test that cannot fail.
    """
    stripped = _hcl_without_comments(IAM_POLICY.read_text(encoding="utf-8"))

    assert "aws_iam_policy_document" in stripped, "the stripper erased the policy"
    assert "dynamodb:LeadingKeys" in stripped, (
        "the condition variable is gone from the stripped source, so the test "
        "below would be asserting against text that no longer describes IAM"
    )


def test_the_partition_prefix_matches_the_iam_policy():
    """THE LOAD-BEARING TEST. Two declarations of one prefix, in two languages.

    The policy authorises `TENANT#${aws:PrincipalTag/tenant}`. This module builds
    `TENANT#<tenant_id>`. If either side's prefix changes alone, the condition
    still evaluates -- it just never matches, so every read returns nothing and
    every write is denied. That reads as "the database is broken", not as "two
    files disagree", and no green apply or green suite would say otherwise.
    """
    stripped = _hcl_without_comments(IAM_POLICY.read_text(encoding="utf-8"))

    # The policy's own escaping: Terraform needs `$${` to emit a literal `${`.
    match = re.search(r'"([A-Z]+)#\$\$\{aws:PrincipalTag/tenant\}"', stripped)

    assert match is not None, (
        "no LeadingKeys value of the form `<PREFIX>#${aws:PrincipalTag/tenant}` "
        "in the policy. Either the condition was removed -- in which case the "
        "web path is no longer constrained to its own tenant at all -- or its "
        "shape changed and this test can no longer see it."
    )
    assert match.group(1) == _dynamo.TENANT_PREFIX, (
        f"the IAM policy authorises {match.group(1)!r}#... and _dynamo builds "
        f"{_dynamo.TENANT_PREFIX!r}#... . Every request would be denied."
    )


# ── the two ways an id can be wrong, and they fail differently ────────────────

def test_an_empty_tenant_shares_a_partition_with_an_UNTAGGED_SESSION():
    """The sharpest refusal in the module.

    An unset session tag makes the policy compare against `TENANT#` -- a real
    partition. So if this module would build `TENANT#` for an empty id, a caller
    with NO tenant tag would be authorised for precisely the rows an unnamed
    tenant writes. Two different kinds of "nobody" would share data.

    IAM already fails closed on this. Refusing here means the two layers agree
    rather than one silently depending on the other.
    """
    with pytest.raises(ValueError, match="empty"):
        _dynamo.tenant_pk("")


@pytest.mark.parametrize("tenant_id", ["a#b", "a\tb", "a,b", "tenant*"])
def test_an_id_iam_cannot_carry_as_a_tag_is_refused_HERE(tenant_id):
    """The constraint comes from IAM, not from DynamoDB, and it is stricter.

    DynamoDB would happily store any of these as a partition key. But the tenant
    travels to DynamoDB *as a session tag*, and IAM tag values admit only
    letters, digits, spaces and `_ . : / = + - @`. So an id like this can never
    be scoped: `AssumeRole` fails at STS with a message about the TAG, several
    layers away from anything that mentions tenants.
    """
    with pytest.raises(ValueError, match="IAM|tag"):
        _dynamo.tenant_pk(tenant_id)


def test_the_ids_this_project_actually_uses_are_accepted():
    """The positive control, without which every refusal above is satisfied by a
    function that refuses everything."""
    assert _dynamo.tenant_pk("tenant-zero") == "TENANT#tenant-zero"
    assert _dynamo.tenant_pk("t_1") == "TENANT#t_1"
    assert _dynamo.tenant_from_pk(_dynamo.tenant_pk("tenant-zero")) == "tenant-zero"


# ── the sort key, and the silent-overwrite hazard ─────────────────────────────

@pytest.mark.parametrize("token", ["RUN", "MEMBER", "REPO", "SECRET", "JOB"])
def test_a_bare_key_for_a_NON_singleton_row_is_refused(token):
    """`PutItem` REPLACES at a (pk, sk) pair; it does not append.

    So a bare `RUN` key would put every run in a tenant at one sort key, and each
    write would silently erase the last. Nothing raises, the write succeeds, and
    the table comes back one row deep. `modules/state` documents the identical
    hazard for its own sort key, which is why that table's key carries an event
    id and not just a timestamp.
    """
    with pytest.raises(ValueError, match="identifier|singleton"):
        _dynamo.sk(token)


@pytest.mark.parametrize("token", ["ORG", "BUDGET", "PROFILE"])
def test_the_three_SINGLETON_rows_are_bare_by_design(token):
    """A tenant has one budget and one identity row, so a bare token is correct
    for exactly these three -- and writing them as `ORG#` with an empty tail
    would make `begins_with(sk, "ORG")` also match a future `ORGUNIT#`."""
    assert _dynamo.sk(token) == token
    assert "#" not in _dynamo.sk(token)


def test_a_sort_key_identifier_is_NOT_constrained_like_a_tenant_id():
    """The asymmetry is deliberate: a sort key is never compared against a tag.

    `owner/repo` carries a slash by construction, and a secret's name is
    arbitrary. Constraining these the way a tenant id is constrained would refuse
    legitimate values for a property that is not at stake.
    """
    assert _dynamo.sk(_dynamo.SK_REPO, "owner/repo") == "REPO#owner/repo"
    assert _dynamo.sk(_dynamo.SK_SECRET, "a b,c") == "SECRET#a b,c"


def test_the_user_partition_is_not_a_tenant_partition():
    """`app_user` is global by design, and `LeadingKeys` does not defend it.

    Nothing about that is new -- Postgres RLS did not defend it either, because
    the table carries no `tenant_id` column to compare. What matters is that the
    two partitions cannot be confused for one another.
    """
    assert _dynamo.user_pk("u-1").startswith("USER#")

    with pytest.raises(ValueError, match="not a tenant partition"):
        _dynamo.tenant_from_pk(_dynamo.user_pk("u-1"))
