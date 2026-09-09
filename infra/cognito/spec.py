"""The reviewer user pool, as DATA. No boto3, no I/O, no network.

Split from `provision.py` for the reason `web/lib/origins.ts` is split from
`web/lib/auth.ts`: a module that builds an AWS client cannot be imported by a
hermetic suite, and every claim worth pinning here -- which claims reach the ID
token, which attributes a signed-in user may rewrite, which URLs the client will
redirect to -- is a property of the configuration rather than of the API call.
`tests/test_infra_cognito.py` reads this module with no credentials at all.

WHY COGNITO AND NOT THE AUTH.JS PATH THAT IS HERE TODAY. `web/lib/auth.ts`
carries a GitHub OAuth app plus a Postgres session table, and CLAUDE.md open
item 1 records the consequence: `membershipsFor` reads the RLS-scoped
`membership` table to discover the tenant that RLS needs bound, which is
circular, so `/api/session` answers `signed_in: true` with `tenant_id: null`
and every authenticated route 401s. A **verified claim in the token** dissolves
that: the tenant arrives already decided, signed by the pool, and no scoped read
is needed to learn it. That is why `custom:tenant` exists here and why it is the
one design note this lane was given.
"""

from __future__ import annotations

import os

# CLAUDE.md fixes both: account 339712964409, region us-east-1. Read from the
# environment so a test can move it, defaulted so nothing depends on a variable
# nobody sets -- the shape `naming.py` uses on the reference deployment.
REGION = os.getenv("AWS_REGION", "us-east-1")

# `theagentorg-shared-` is this account's prefix for everything Terraform owns
# (five ECR repositories, the ingress Lambda, the event bus). The pool is not
# Terraform's -- see `provision.py`'s header -- but it shares the account with
# other projects' pools, so the prefix is what keeps a `ListUserPools` match
# unambiguous.
POOL_NAME = "theagentorg-shared-reviewers"
CLIENT_NAME = "theagentorg-web"
DOMAIN_PREFIX = "theagentorg-shared-reviewers"

# THE TWO CLAIMS, AND THE SECOND ONE IS THE POINT OF THIS LANE.
#
# `custom:role` gates the surface: `POST /api/approvals` is the first route in
# this repository that can open a human gate over a network.
#
# `custom:tenant` is CLAUDE.md open item 1, dissolved. `web/lib/tenant.ts:126`
# records three options for the circular lookup and why two are wrong; this is
# the third -- the tenant is not discovered by a scoped read, it is asserted by
# the identity provider and verified by signature. Nothing in the request can
# supply it, which is the same property `ApprovalRequest` has by carrying no
# `by` and no `tenant_id`.
ROLE_CLAIM = "custom:role"
ROLE_VALUE = "reviewer"
TENANT_CLAIM = "custom:tenant"

# `tenant-zero` is `agentorg/db/schema.py:89`'s `TENANT_ZERO_ID`, and it is
# restated here rather than imported ON PURPOSE. Importing it would make this
# module depend on the package -- and `infra/` is deliberately outside
# `agentorg/`, because `tests/test_agentcore_deploy_assets.py` AST-walks
# `agentorg/**/*.py` and fails on a third-party import absent from the agents'
# requirements. `provision.py` imports boto3. A second declaration is the price;
# `tests/test_infra_cognito.py` reads the literal out of `schema.py` and asserts
# the two agree, which is the only thing that detects a change in the first.
DEFAULT_TENANT_VALUE = "tenant-zero"

# A seeded reviewer for the demo. Opaque on purpose: Cognito puts `sub` (a UUID)
# in the token and that is what would reach a `HumanDecision.by`, but a username
# that looked like a person invites somebody to read it as one.
SEED_USERNAME = "reviewer-01"

# **CUSTOM ATTRIBUTES ARE DECLARED AT CREATION, AND GETTING THE SHAPE RIGHT THE
# FIRST TIME IS WHAT MATTERS -- NOT GETTING THE SET RIGHT.** `AddCustomAttributes`
# exists (verified against botocore 1.43.75's own service model, not the docs:
# the operation is PRESENT and takes `UserPoolId` + `CustomAttributes`), so a
# MISSING attribute can be added to a live pool and `provision.py` does exactly
# that on the converge path. What cannot be undone is an attribute that already
# exists with the wrong shape: AWS documents that a custom attribute cannot be
# deleted or its definition changed, so a `custom:tenant` created `Mutable: True`
# stays mutable forever and needs a NEW POOL -- a new pool id, a new issuer, and
# every existing token invalid.
#
# **`Mutable: False` ON BOTH, AND IT IS THE WHOLE GUARANTEE.** The reference
# deployment measured the trap: with `WriteAttributes` omitted, a signed-in
# user's `UpdateUserAttributes` against an ungranted MUTABLE custom attribute
# SUCCEEDED -- omission grants every attribute, exactly as the AWS docs say. So
# immutability is one guard and `WriteAttributes` below is the other, and the
# reference shipped one while its comment claimed two. A mutable `custom:tenant`
# would let a reviewer rewrite the claim that decides whose runs they can see,
# which is the cross-tenant read `tests/test_tenancy_leak.py` exists to refuse,
# arriving through the identity layer where no accessor can see it.
CUSTOM_ATTRIBUTES: tuple[dict, ...] = (
    {
        "Name": "role",
        "AttributeDataType": "String",
        "Mutable": False,
        "Required": False,
        "StringAttributeConstraints": {"MinLength": "1", "MaxLength": "32"},
    },
    {
        "Name": "tenant",
        "AttributeDataType": "String",
        "Mutable": False,
        "Required": False,
        # A tenant id is a slug: `tenant-zero` is 11 characters and Lane B's
        # column is TEXT, so 64 is headroom rather than a measured bound.
        "StringAttributeConstraints": {"MinLength": "1", "MaxLength": "64"},
    },
)

# The claim names as they appear in the ID token, derived from the schema above
# rather than typed a second time -- `custom:` + the declared name is Cognito's
# own rule, and two spellings of one fact keep agreeing while one moves.
CLAIMS: tuple[str, ...] = tuple(f"custom:{a['Name']}" for a in CUSTOM_ATTRIBUTES)


def pool_spec() -> dict:
    """`CreateUserPool` arguments."""
    return {
        "PoolName": POOL_NAME,
        "Policies": {
            "PasswordPolicy": {
                # Cognito's default is 8 with no symbol requirement. An account
                # here can approve a security gate.
                "MinimumLength": 12,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        },
        # Admin-create-only. Anyone who could sign themselves up would reach
        # `POST /api/approvals`; a reviewer account is issued, not requested.
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        "Schema": [dict(a) for a in CUSTOM_ATTRIBUTES],
        "UserPoolTags": {"Project": "TheAgentOrg", "Environment": "shared"},
    }


CLIENT_SPEC: dict = {
    "ClientName": CLIENT_NAME,
    # A public client. The code exchange happens server-side in a route handler,
    # so a secret buys nothing -- and a client secret in an Amplify build
    # environment is one `echo` away from a build log, which is the shape of the
    # `github_pat_` this repository already leaked into a Terraform plan.
    "GenerateSecret": False,
    # The authorization-code flow, never implicit: implicit returns the token in
    # the URL fragment, which lands in browser history and any referrer header.
    "AllowedOAuthFlows": ["code"],
    "AllowedOAuthFlowsUserPoolClient": True,
    # `openid` alone. `profile` and `email` would carry a name into a token that
    # CloudTrail logs, and nothing in `web/` needs either to decide anything.
    "AllowedOAuthScopes": ["openid"],
    "SupportedIdentityProviders": ["COGNITO"],
    "ExplicitAuthFlows": ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    # **`ReadAttributes` MUST NAME BOTH CLAIMS OR NEITHER APPEARS IN THE TOKEN.**
    # When `ReadAttributes` is omitted the client reads `email_verified`,
    # `phone_number_verified` and the pool's STANDARD attributes -- a custom
    # attribute is not among them. So declaring the schema is necessary and not
    # sufficient: without this line `custom:tenant` is on the user, absent from
    # the token, and every sign-in fails closed reading as "auth is broken".
    "ReadAttributes": [*CLAIMS],
    # **`WriteAttributes` MUST BE SET, AND MUST CONTAIN NEITHER CLAIM.** Omitting
    # it is not capability absence -- it grants write on every attribute. Setting
    # it is what makes the refusal an AUTHORISATION refusal
    # (`NotAuthorizedException`) rather than the immutability one
    # (`InvalidParameterException`), which is a different guard that would still
    # fire if somebody later made an attribute mutable. `email` is the only
    # writable attribute, and nothing in `web/` writes it either; an empty list
    # cannot be widened later without a full replace.
    "WriteAttributes": ["email"],
    # An hour. Long enough for a review session, short enough that a leaked token
    # expires before it is useful.
    "IdTokenValidity": 60,
    "AccessTokenValidity": 60,
    "TokenValidityUnits": {"IdToken": "minutes", "AccessToken": "minutes"},
}


def callback_urls(*dashboard_urls: str) -> list[str]:
    """Every deployed origin's callback, plus localhost.

    `UpdateUserPoolClient` is a FULL REPLACE, so passing one URL silently breaks
    the others -- and local development is how every remaining verification step
    in this repository is done. Duplicates are collapsed while order is kept,
    because the same URL reaching Cognito twice is a validation error rather than
    a no-op.

    `localhost` and NOT `127.0.0.1`: CLAUDE.md measured that they are the same
    machine and different origins to an OAuth provider, that the callback is
    compared as a string, and that mixing them answers `redirect_uri_mismatch` --
    which reads as a broken app rather than as a typo. `infra/selfhost/
    docker-compose.yml` sets `AUTH_URL` to `localhost`, so this matches it.
    """
    seen: dict[str, None] = {}
    for base in (*dashboard_urls, "http://localhost:3000"):
        if base and base.strip():
            seen.setdefault(f"{base.strip().rstrip('/')}/api/auth/callback", None)
    return list(seen)


def issuer(pool_id: str) -> str:
    """The `iss` claim, which is the pool's API host and NOT a sign-in domain.

    Worth stating because the two are easy to conflate: the JWKS a verifier
    fetches lives under this URL, so a token minted through any sign-in domain --
    the prefix one, a custom one added later -- verifies against exactly the same
    key set. Changing the sign-in page does not change what `COGNITO_ISSUER` must
    be; changing the POOL does.
    """
    return f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}"


def hosted_domain() -> str:
    """The prefix hosted-UI domain, which is what `COGNITO_DOMAIN` should be."""
    return f"https://{DOMAIN_PREFIX}.auth.{REGION}.amazoncognito.com"
