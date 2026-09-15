"""What `db/tenant_credentials.py` REFUSES, and the tag it must send.

The module mints credentials that can read one tenant. Almost everything worth
pinning here is a refusal, because every one of them has a fail-open version that
reads as correct code -- and because the success path is a boto3 call, which a
test can only ever observe through a double.

**THE CROSS-FILE TEST IS THE ONE THAT EARNS ITS KEEP.** `TAG_KEY` here and the
`aws:PrincipalTag/...` in `infra/Terraform/modules/tenancy/iam.tf` are two
declarations of one fact, and a mismatch raises NOWHERE: STS accepts any tag key,
the session carries it, the policy's variable is then empty, LeadingKeys compares
against `TENANT#`, and every read is refused with no error naming the tag. A test
per file would pass on both sides of that.
"""

from __future__ import annotations

import dataclasses
import re
import sys
import time
import types
from pathlib import Path

import pytest

from agentorg.db import tenant_credentials as tc

REPO_ROOT = Path(__file__).resolve().parents[1]
IAM_POLICY = REPO_ROOT / "infra" / "Terraform" / "modules" / "tenancy" / "iam.tf"

_ROLE = "arn:aws:iam::339712964409:role/theagentorg-shared-tenancy-scoped"


class _FakeSTS:
    """An STS that records what it was asked and hands back fake keys.

    Records EVERY call rather than only the last, because the cache tests turn on
    how many times this was reached -- a double that keeps one call cannot
    express "was not called again".
    """

    def __init__(self, expires_in: float = 3600.0, error: Exception | None = None):
        self.calls: list[dict] = []
        self._expires_in = expires_in
        self._error = error

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error

        class _Expiry:
            def __init__(self, when: float):
                self._when = when

            def timestamp(self) -> float:
                return self._when

        tenant = kwargs["Tags"][0]["Value"] if kwargs.get("Tags") else "<untagged>"
        return {
            "Credentials": {
                # FAKE LITERALS, and they carry the tenant so a test can prove
                # WHICH tenant's credential came back rather than merely that one
                # did -- the cache-keying test turns entirely on that.
                "AccessKeyId": f"ASIAFAKE{tenant}",
                "SecretAccessKey": f"fake-secret-{tenant}",
                "SessionToken": f"fake-token-{tenant}",
                "Expiration": _Expiry(time.time() + self._expires_in),
            }
        }


@pytest.fixture(autouse=True)
def _clear_cache():
    """The cache is module state, so it leaks between tests in both directions.

    Cleared on BOTH sides, following `conftest.py`'s scanner-cache guard: a stale
    hit arriving from the previous test looks exactly like a cache working.
    """
    tc._reset_cache()
    yield
    tc._reset_cache()


@pytest.fixture
def sts(monkeypatch):
    """Install a fake boto3 whose `client('sts', ...)` is the recorder above."""
    fake = _FakeSTS()
    module = types.ModuleType("boto3")
    module.client = lambda service, **kw: fake
    monkeypatch.setitem(sys.modules, "boto3", module)
    return fake


# ── the three refusals ───────────────────────────────────────────────────────


@pytest.mark.parametrize("tenant_id", ["", "tenant#one", "a\tb"])
def test_an_id_that_cannot_be_a_session_tag_is_refused_before_any_call(tenant_id, sts):
    """No STS call is made for an id that could never carry the tag.

    Asserting `sts.calls == []` and not merely that it raised: a module that
    called first and validated afterwards would pass a bare `pytest.raises`, and
    would have spent a round trip announcing the tenant to CloudTrail.
    """
    with pytest.raises(ValueError):
        tc.credentials(tenant_id, role_arn=_ROLE)
    assert sts.calls == [], "the id was refused only AFTER reaching STS"


def test_an_unset_role_arn_REFUSES_instead_of_using_ambient_credentials(sts, monkeypatch):
    """The fail-open version of this is one line and passes every other test.

    With no role to assume, returning the process's own credentials would work
    perfectly -- and would read every tenant's rows, on exactly the machines
    where the variable happens to be unset.
    """
    monkeypatch.setattr(tc.config, "TENANT_SCOPED_ROLE_ARN", "")
    with pytest.raises(RuntimeError) as exc:
        tc.credentials("t1")
    assert sts.calls == []
    assert "falling back" in str(exc.value), (
        "the refusal must say WHY it refuses rather than reading as a missing "
        "setting; the next person's fix is the fallback."
    )


def test_an_STS_refusal_names_the_tag_and_forbids_dropping_it(monkeypatch):
    """The tempting fix for this AccessDenied is the one that breaks silently."""
    from botocore.exceptions import ClientError

    denied = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "not authorized to perform: sts:TagSession"}},
        "AssumeRole",
    )
    fake = _FakeSTS(error=denied)
    module = types.ModuleType("boto3")
    module.client = lambda service, **kw: fake
    monkeypatch.setitem(sys.modules, "boto3", module)

    with pytest.raises(PermissionError) as exc:
        tc.credentials("t1", role_arn=_ROLE)

    message = str(exc.value)
    assert "sts:TagSession" in message
    assert "DO NOT FIX IT BY DROPPING THE TAG" in message, (
        "an AccessDenied here has a fix that succeeds and authorises nothing; "
        "the message is the only place that is said."
    )


# ── the tag itself ───────────────────────────────────────────────────────────


def test_the_tenant_travels_as_a_session_tag(sts):
    """Without `Tags`, AssumeRole succeeds and the credential can read nothing."""
    tc.credentials("t1", role_arn=_ROLE)

    assert len(sts.calls) == 1
    call = sts.calls[0]
    assert call["RoleArn"] == _ROLE
    assert call["Tags"] == [{"Key": tc.TAG_KEY, "Value": "t1"}]


def test_the_tag_key_is_the_one_the_IAM_POLICY_COMPARES_AGAINST():
    """TWO FILES, ONE FACT -- and a mismatch raises nowhere.

    The policy authorises `TENANT#${aws:PrincipalTag/tenant}`. If this module
    sent `tenant_id` instead, STS would accept it, the session would carry it,
    and `aws:PrincipalTag/tenant` would be empty -- so LeadingKeys would compare
    against `TENANT#` and match nothing. Every read fails with AccessDenied and
    no error anywhere names the tag.

    Read off the Terraform source rather than the deployed policy on purpose:
    this is a hermetic test, and the deployed answer is `preflight_tenancy.py`'s
    job.
    """
    source = IAM_POLICY.read_text()
    tags = set(re.findall(r"aws:PrincipalTag/([A-Za-z0-9_.:/=+\-@]+)", source))

    assert tags, (
        f"no `aws:PrincipalTag/...` found in {IAM_POLICY.name}; this test would "
        f"pin nothing. Either the condition was removed -- in which case tenant "
        f"isolation is gone -- or it was renamed."
    )
    assert tags == {tc.TAG_KEY}, (
        f"{IAM_POLICY.name} compares against {sorted(tags)} and this module "
        f"sends {tc.TAG_KEY!r}. STS accepts any key, so this mismatch is silent: "
        f"the policy's variable resolves empty and LeadingKeys matches nothing."
    )


# ── the cache, which is where a leak would arrive as an optimisation ─────────


def test_two_tenants_do_not_share_one_credential(sts):
    """The single-slot cache is the leak this subsystem exists to prevent.

    A module-level `_CREDENTIALS` with no key passes every other test in this
    file: the first mint is correct, and every later caller silently reads the
    first tenant's partition with the right policy applied to the wrong tag.
    """
    first = tc.credentials("t1", role_arn=_ROLE)
    second = tc.credentials("t2", role_arn=_ROLE)

    assert first.tenant_id == "t1"
    assert second.tenant_id == "t2"
    assert first.access_key_id != second.access_key_id
    assert [c["Tags"][0]["Value"] for c in sts.calls] == ["t1", "t2"]


def test_the_same_tenant_is_not_re_assumed_while_its_keys_are_fresh(sts):
    tc.credentials("t1", role_arn=_ROLE)
    tc.credentials("t1", role_arn=_ROLE)
    assert len(sts.calls) == 1, "a fresh credential was re-minted"


def test_a_credential_near_expiry_is_replaced_rather_than_handed_back(monkeypatch):
    """Freshness is checked against a MARGIN, not against expiry.

    A credential valid at the moment it is checked and expired by the time
    DynamoDB is reached fails in the caller, some frames away from here.
    """
    fake = _FakeSTS(expires_in=tc._EXPIRY_MARGIN_SECONDS / 2)
    module = types.ModuleType("boto3")
    module.client = lambda service, **kw: fake
    monkeypatch.setitem(sys.modules, "boto3", module)

    tc.credentials("t1", role_arn=_ROLE)
    tc.credentials("t1", role_arn=_ROLE)

    assert len(fake.calls) == 2, (
        "a credential inside the expiry margin was treated as fresh; it would "
        "expire between this check and the DynamoDB call it is used for."
    )


def test_is_fresh_separates_valid_from_within_the_margin():
    """The predicate directly, because the cache test cannot reach both edges."""
    now = 1_000_000.0
    creds = tc.TenantCredentials("t1", "k", "s", "t", expires_at=now + 3600)

    assert creds.is_fresh(now) is True
    assert creds.is_fresh(now + 3600 - tc._EXPIRY_MARGIN_SECONDS + 1) is False
    assert creds.is_fresh(now + 4000) is False


def test_a_credential_cannot_be_retagged_after_it_is_minted():
    """Frozen: the tenant on a credential is not a field a later caller adjusts.

    `FrozenInstanceError` by name rather than a blind `Exception`, which ruff's
    B017 refuses and is right to: a bare `Exception` here would also be satisfied
    by an `AttributeError` from a typo'd field name, so the test would pass while
    pinning nothing about frozenness.
    """
    creds = tc.TenantCredentials("t1", "k", "s", "t", expires_at=time.time() + 3600)
    with pytest.raises(dataclasses.FrozenInstanceError):
        creds.tenant_id = "t2"  # type: ignore[misc]
