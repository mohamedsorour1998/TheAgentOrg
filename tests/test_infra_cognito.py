"""The Cognito pool and app client, as data. Owner: Lane Q.

WHAT THIS FILE DEFENDS. `custom:tenant` is what dissolved the RLS circularity — the
tenant arrives on a signature-verified ID token rather than from a database read that
could not scope itself. That is only worth having if **nobody but an administrator can
set it**, and this pool is the only thing enforcing that. `web/lib/tenant.ts` says so in
as many words: "This file relies on both and can enforce neither."

So the assertions below are about the *pool spec*, not about TypeScript. Every one of
them is a setting whose wrong value produces a working-looking deployment:

  * a claim missing from `ReadAttributes` never appears in the token, and the app reads
    `tenant_id: null` for every user — indistinguishable from "nobody has been assigned";
  * a claim present in `WriteAttributes` can be set at sign-up, so a caller chooses their
    own tenant and the whole verification argument collapses;
  * `WriteAttributes` OMITTED grants everything, which is the shape that reads as
    "restrictive by default" and is the opposite.

NO AWS CALL IS MADE HERE. The specs are dicts, deliberately, so this runs in the hermetic
suite alongside everything else — the same reason `infra/provision_cognito.py` in the
reference project keeps `pool_spec()` a pure function.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.cognito import spec

# ── the two guards that make the tenant claim trustworthy ─────────────────────

def test_both_claims_are_declared_on_the_pool():
    """A custom attribute cannot be added to an existing pool. This is the one
    irreversible decision in the lane.

    Named individually rather than counted, because a pool with `role` and without
    `tenant` would sign people in perfectly and answer `tenant_id: null` forever —
    a working sign-in and an application nobody can use.
    """
    declared = {a["Name"] for a in spec.pool_spec()["Schema"]}

    assert "role" in declared, "custom:role is not declared; authorizeSession refuses all"
    assert "tenant" in declared, (
        "custom:tenant is not declared, so the claim never appears in a token and every "
        "session resolves to no tenant. It CANNOT be added to this pool later."
    )


@pytest.mark.parametrize("attribute", ["role", "tenant"])
def test_neither_claim_is_mutable(attribute):
    """`Mutable: False` stops `UpdateUserAttributes` — a signed-in user rewriting the
    claim that authorises them.

    This is one half of a pair and it is NOT the half that stops sign-up; see the next
    test. Both are needed and they guard different verbs.
    """
    declared = {a["Name"]: a for a in spec.pool_spec()["Schema"]}

    assert declared[attribute]["Mutable"] is False, (
        f"custom:{attribute} is mutable, so a signed-in user can rewrite it with "
        f"UpdateUserAttributes — including rewriting which tenant's runs they may read"
    )


def test_the_write_attributes_list_is_PRESENT_and_excludes_both_claims():
    """The half that stops sign-up, and the one that fails in the flattering direction.

    THE REFERENCE DEPLOYMENT MEASURED THAT OMITTING `WriteAttributes` GRANTS
    EVERYTHING — an ungranted mutable custom attribute was written successfully by a
    signed-in user. So an absent list is not a restrictive default; it is the least
    restrictive setting available, wearing the appearance of one.

    `Mutable: False` does not cover this case. An immutable attribute set AT CREATION is
    set forever, so a self-signup that could name its own tenant would lock the wrong
    answer in permanently — worse than a mutable one an administrator could correct.
    """
    writable = spec.CLIENT_SPEC["WriteAttributes"]

    assert writable, (
        "WriteAttributes is empty or absent. Absent GRANTS EVERY ATTRIBUTE — measured in "
        "the reference deployment — so the list must be present and explicit."
    )
    assert "custom:tenant" not in writable, (
        "custom:tenant is writable by the app client, so SignUp can set it and a caller "
        "chooses their own tenant. That is a client-supplied tenant_id wearing a JWT, "
        "which every other layer in this repository refuses."
    )
    assert "custom:role" not in writable, "custom:role is writable; a signup could self-promote"


def test_both_claims_are_readable_or_they_never_reach_the_token():
    """`ReadAttributes` and `WriteAttributes` are not symmetric, and omitting the first
    is the silent failure.

    When `ReadAttributes` is omitted the client reads a default set that does NOT include
    custom attributes, so the pool holds a correct `custom:tenant` and the token carries
    nothing. Every session then resolves to no tenant, and the fault looks like an
    unassigned account rather than a misconfigured client.
    """
    readable = spec.CLIENT_SPEC["ReadAttributes"]

    for claim in spec.CLAIMS:
        assert claim in readable, (
            f"{claim} is not in ReadAttributes, so it never appears in an ID token no "
            f"matter what the pool holds"
        )


# ── requirement 9, and what it costs ──────────────────────────────────────────

def test_self_registration_is_open():
    """Judge requirement 9 is "sign UP and in", so this is a requirement, not a default.

    The reference deployment sets `AllowAdminCreateUserOnly: True` and is right to — "a
    caseworker account is issued, not requested". We are judged on the opposite, which is
    exactly why the two guards above carry the weight: with sign-up open, the pool is the
    only thing standing between a caller and a tenant of their choosing.
    """
    config = spec.pool_spec()["AdminCreateUserConfig"]

    assert config["AllowAdminCreateUserOnly"] is False, (
        "self-registration is closed, which fails requirement 9"
    )


def test_a_new_account_has_no_tenant_and_that_is_the_point():
    """The consequence of open sign-up, asserted as data rather than left implied.

    Nothing in the pool assigns a tenant at creation — no default, no pre-token Lambda,
    no `custom:tenant` in the writable list. So a fresh account carries no claim and
    every authenticated route refuses. Fail-closed, and `SignInPanel` renders "not
    assigned to an organisation" rather than an empty run list, because an empty list
    reads as "nothing has happened" when the truth is "you may not see what has".
    """
    schema = {a["Name"]: a for a in spec.pool_spec()["Schema"]}

    assert "DefaultValue" not in schema["tenant"], (
        "custom:tenant has a default, so every new account is silently placed in a "
        "tenant nobody chose for them"
    )
    assert "custom:tenant" not in spec.CLIENT_SPEC["WriteAttributes"], (
        "sign-up can set the tenant, so a new account is not tenant-less by construction"
    )


# ── the callback contract, shared with Lane P ─────────────────────────────────

def test_callback_and_logout_urls_are_derived_from_one_base():
    """`AUTH_URL` is one variable with two halves — the callback base AND the CSRF
    allow-list — and Lane P deliberately did not rename it.

    A second name would be two declarations of one origin, and the drift shows up as a
    legitimately-clicked button refused. So the pool's callback URL must be derived from
    the same base the application is told about, not typed separately.
    """
    base = "https://example.invalid"

    callbacks = spec.callback_urls(base)

    assert any(url.startswith(base) for url in callbacks), (
        f"no callback URL derives from {base}; {callbacks}"
    )
    assert any(url.endswith("/api/auth/callback") for url in callbacks), (
        f"no callback URL names the route Lane P serves; got {callbacks}"
    )
