"""The reviewer user pool, as DATA. No boto3, no I/O, no network.

Split from `provision.py` for the reason `web/lib/origins.ts` is split from
`web/lib/auth.ts`: a module that builds an AWS client cannot be imported by a
hermetic suite, and every claim worth pinning here -- which claims reach the ID
token, which attributes a signed-in user may set, which URLs the client will
redirect to -- is a property of the CONFIGURATION rather than of the API call.
`tests/test_infra_cognito.py` reads this module with no credentials at all.

WHY COGNITO. `web/lib/auth.ts` carried a GitHub OAuth app plus a Postgres
session table, and CLAUDE.md open item 1 records the consequence:
`membershipsFor` reads the RLS-scoped `membership` table to discover the tenant
that RLS needs bound, which is circular, so `/api/session` answered
`signed_in: true` with `tenant_id: null` and every authenticated route 401'd. A
**verified claim in the token** dissolves that: the tenant arrives already
decided, signed by the pool, and no scoped read is needed to learn it. That is
why `custom:tenant` exists here.

WHY IT IS NOT TERRAFORM. `infra/Terraform/` is applied by `terraform.yml` from
CI and nothing in this repository is created by hand in the console -- so a
Cognito pool arguably belongs there. It is here instead for one measured reason:
**a custom attribute's shape cannot be changed once it exists**, and Terraform's
answer to a changed `schema` block on `aws_cognito_user_pool` is to destroy and
recreate the pool, which changes the pool id, changes the issuer, and invalidates
every token and every `custom:tenant` already assigned. A find-or-create script
that CONVERGES and refuses to recreate is the safer shape for exactly this
resource. Said plainly rather than left to be discovered.
"""

from __future__ import annotations

import os

# CLAUDE.md fixes both: account 339712964409, region us-east-1. Read from the
# environment so a test can move it, defaulted so nothing depends on a variable
# nobody sets.
REGION = os.getenv("AWS_REGION", "us-east-1")

# `theagentorg-shared-` is this account's prefix for everything Terraform owns
# (five ECR repositories, the ingress Lambda, the event bus). The pool is not
# Terraform's -- see the header -- but it shares an account with other projects'
# pools, so the prefix is what keeps a `ListUserPools` match unambiguous.
POOL_NAME = "theagentorg-shared-reviewers"
CLIENT_NAME = "theagentorg-web"
DOMAIN_PREFIX = "theagentorg-shared-reviewers"

# THE TWO CLAIMS. `custom:role` gates the surface; `custom:tenant` is CLAUDE.md
# open item 1 dissolved. `web/lib/cognito.ts:65-66` declares both spellings on
# the reading side and `tests/test_infra_cognito.py` asserts the two files agree,
# because a rename in either place is silent otherwise: `claim()` returns "" for
# an absent claim and the session is simply refused.
ROLE_CLAIM = "custom:role"
ROLE_VALUE = "reviewer"
TENANT_CLAIM = "custom:tenant"

# `tenant-zero` is `agentorg/db/schema.py`'s `TENANT_ZERO_ID`, restated here ON
# PURPOSE. Importing it would make this module depend on the package, and
# `infra/` is deliberately outside `agentorg/` because
# `tests/test_agentcore_deploy_assets.py` AST-walks `agentorg/**/*.py` and fails
# on a third-party import absent from the agents' requirements -- `provision.py`
# imports boto3. A second declaration is the price; the test reads the literal
# out of `schema.py` and asserts the two agree, which is the only thing that can
# detect a change in the first.
DEFAULT_TENANT_VALUE = "tenant-zero"

# A seeded reviewer for the demo. Opaque on purpose: Cognito puts `sub` (a UUID)
# in the token and a username that looked like a person invites somebody to read
# it as one.
SEED_USERNAME = "reviewer-01"

# ── THE CUSTOM ATTRIBUTES, AND THE ONE IRREVERSIBLE DECISION IN THIS LANE ─────
#
# **A MISSING ATTRIBUTE CAN BE ADDED TO A LIVE POOL. A WRONGLY-SHAPED ONE CANNOT
# BE FIXED.** This corrects a claim in this lane's own brief. `AddCustomAttributes`
# is PRESENT in botocore 1.43.75's service model -- verified by reading the model
# rather than the documentation site, `input members: ['UserPoolId',
# 'CustomAttributes']` -- and `provision.py` calls it on the converge path for any
# declared attribute the live schema lacks. What AWS documents as impossible is
# DELETING a custom attribute or CHANGING its definition. So a pool whose
# `custom:tenant` was created `Mutable: True` stays mutable forever and needs a
# NEW POOL: a new pool id, a new issuer, every token invalid, and every assigned
# tenant re-assigned. Getting the SHAPE right the first time is what matters;
# getting the SET right is recoverable.
#
# **`Mutable: False` AND THE `WriteAttributes` EXCLUSION ARE A PAIR, AND EACH
# CLOSES A DOOR THE OTHER LEAVES OPEN.** They are not belt-and-braces:
#
#   `Mutable: False`            stops a SIGNED-IN user calling
#                               `UpdateUserAttributes` to rewrite the claim that
#                               decides whose runs they can see.
#   excluded from               stops `SignUp` setting it AT CREATION. `Mutable:
#   `WriteAttributes`           False` does not help here at all -- an immutable
#                               attribute set once is set forever, so a
#                               self-registration naming its own tenant would
#                               lock the wrong answer in permanently.
#
# And the exclusion only means anything because the list is PRESENT: the
# reference deployment measured that with `WriteAttributes` OMITTED, a signed-in
# user's `UpdateUserAttributes` against an ungranted MUTABLE custom attribute
# SUCCEEDED -- omission grants every attribute, exactly as the AWS docs say. That
# deployment shipped one guard while its comment claimed two.
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

# The claim names as they appear in the ID token, DERIVED from the schema rather
# than typed a second time -- `custom:` + the declared name is Cognito's own
# rule, stated in the `SchemaAttributeType.Name` model documentation, and two
# spellings of one fact keep agreeing while one moves.
CLAIMS: tuple[str, ...] = tuple(f"custom:{a['Name']}" for a in CUSTOM_ATTRIBUTES)

# The one attribute a client may write. NOT an empty list: a client whose
# `WriteAttributes` is empty cannot be widened later without a full replace, and
# `email` is the standard attribute a self-registration must be able to supply.
# Nothing in `web/` writes it. **Neither claim may ever appear here.**
WRITABLE_ATTRIBUTES: tuple[str, ...] = ("email",)

# ── THE SIGN-UP CLIENT, AND WHY A SECOND ONE EXISTS AT ALL ────────────────────
#
# **THE PROBLEM THIS SOLVES WAS A DEAD END, MEASURED AND RECORDED.** `custom:tenant`
# is `Mutable: False`, and an immutable attribute cannot be set after creation EVEN
# WHEN IT WAS NEVER GIVEN A VALUE -- probed against the live pool on 2026-09-09:
# `InvalidParameterException: user.custom:tenant: Attribute cannot be updated.` So a
# self-registered account could sign up, sign in, and be permanently unable to see
# anything, with `/api/session` answering `tenant_id: null` forever. Fail-closed, and
# still a dead end. CLAUDE.md carried it as open item 11.
#
# The only remaining moment to set the claim is CREATION -- and the browser client
# deliberately cannot, because `custom:tenant` is excluded from its
# `WriteAttributes` precisely so a self-registration cannot name its own tenant.
#
# **SO THE SERVER SIGNS THE USER UP, NOT THE BROWSER.** This client may write the
# claim; the browser's may not. What stops a browser simply using THIS client id is
# that it is CONFIDENTIAL: `GenerateSecret: True` means every `SignUp` and
# `ConfirmSignUp` must carry a `SECRET_HASH` computed from a secret that lives in
# Secrets Manager and is readable only by the Amplify compute role. A client id is
# public by nature; the secret is the part that is not.
#
# THE GUARD IS THEREFORE UNCHANGED IN SUBSTANCE: a self-registering user still
# cannot choose the claim that authorises them. The server chooses it, from the
# sign-up route, and the user never sees the value travel.
#
# **IT GETS ITS OWN TENANT, NOT TENANT ZERO.** Handing a new signup
# `DEFAULT_TENANT_VALUE` would show them the original deployment's runs -- the exact
# thing `pool_spec`'s comment above refuses, and CLAUDE.md's "would work in a demo".
# A fresh tenant means an empty run list with `indexed: true`, which is the honest
# answer and the one that demonstrates the isolation rather than bypassing it.
#
# NO OAUTH FLOWS AND NO AUTH FLOWS. This client exists to call two unauthenticated
# Cognito APIs from our server. It cannot be used to sign in, so a leaked secret
# does not become a session.
SIGNUP_CLIENT_NAME = "theagentorg-signup"

# Where the secret lives. NOT an Amplify environment variable: `amplify.yml` records
# that those are visible in the console, in build logs, and inside a downloadable
# build artifact -- three places at once, which is the shape of the `github_pat_`
# this repository already leaked into a Terraform plan artifact.
SIGNUP_SECRET_NAME = "theagentorg-shared-cognito-signup-client"

SIGNUP_CLIENT_SPEC: dict = {
    "ClientName": SIGNUP_CLIENT_NAME,
    "GenerateSecret": True,
    # Absent deliberately: no `AllowedOAuthFlows`, no `AllowedOAuthFlowsUserPoolClient`,
    # no `SupportedIdentityProviders`, no `ExplicitAuthFlows`. This client cannot
    # start a session by any route; it can only create an unconfirmed user and
    # confirm one.
    "ReadAttributes": [*CLAIMS],
    # THE ONE DIFFERENCE FROM THE BROWSER CLIENT, and the reason this file needed a
    # second spec rather than a flag. `email` so the address can be supplied, and
    # `TENANT_CLAIM` so the server can set it at the only moment it can ever be set.
    # `ROLE_CLAIM` is NOT here: a self-registration does not become a reviewer.
    "WriteAttributes": [*WRITABLE_ATTRIBUTES, TENANT_CLAIM],
}

# ── EMAIL VERIFICATION ────────────────────────────────────────────────────────
#
# **WITHOUT THIS, `SignUp` SENDS NOTHING AND THE USER IS STUCK UNCONFIRMED.**
# Measured on the live pool before it was set: `AutoVerifiedAttributes: None`, so
# Cognito created the user and emailed no code -- a sign-up form that appears to
# work and produces an account nobody can confirm.
#
# `email` alone. `phone_number` would make Cognito attempt SMS, which needs an SNS
# role this pool does not have and fails at sign-up time.
AUTO_VERIFIED_ATTRIBUTES: tuple[str, ...] = ("email",)


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
        # **SELF-REGISTRATION IS OPEN, AND THAT IS A REQUIREMENT RATHER THAN A
        # DEFAULT.** The reference deployment sets `AllowAdminCreateUserOnly:
        # True` and comments "a caseworker account is issued, not requested",
        # which is right for a benefits system and wrong here: judge requirement
        # 9 is "sign UP and in". The tension it creates -- a self-registering
        # user must not be able to choose the claim that authorises them -- is
        # resolved by `WRITABLE_ATTRIBUTES` above and not by closing signup. A
        # newly registered user therefore holds NO tenant claim, and every
        # authenticated route refuses until an operator assigns one
        # (`provision.assign_tenant`). Fail-closed, and the same shape the app
        # already had: CLAUDE.md records a fresh login resolving `tenant_id:
        # null` and 401ing, which "is correct as a default -- the alternative,
        # returning tenant zero when the lookup finds nothing, would work in a
        # demo and hand every new signup the original deployment's runs".
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": False},
        # See `AUTO_VERIFIED_ATTRIBUTES`. Without it `SignUp` emails no code and
        # the account can never be confirmed.
        "AutoVerifiedAttributes": [*AUTO_VERIFIED_ATTRIBUTES],
        "Schema": [dict(a) for a in CUSTOM_ATTRIBUTES],
        "UserPoolTags": {"Project": "TheAgentOrg", "Environment": "shared"},
    }


CLIENT_SPEC: dict = {
    "ClientName": CLIENT_NAME,
    # **A PUBLIC CLIENT, AND THAT DECISION REMOVES A LINK RATHER THAN TRUSTING
    # IT.** `web/lib/cognito.ts:296` reads `COGNITO_CLIENT_SECRET` as optional
    # and its own comment names this "the one link in the chain a missing
    # variable makes silent": with a secret on the client and the variable
    # unset, the token exchange answers `invalid_client`, `exchangeCode` returns
    # `null`, and the reviewer sees "sign-in did not complete" with nothing
    # naming the cause. A secret would also have to travel as an Amplify
    # environment variable -- console, build log and downloadable artifact, three
    # places at once. The code exchange happens server-side in a route handler,
    # so a secret buys nothing here. `amplify.yml` therefore carries no
    # `COGNITO_CLIENT_SECRET`, and that absence is correct only because of this
    # line: the two are asserted together in `tests/test_infra_cognito.py`.
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
    # sufficient: without this line `custom:tenant` sits on the user, absent from
    # the token, `verifySession` reads `""` and refuses, and the failure reads as
    # "auth is broken" rather than as one unreadable attribute.
    "ReadAttributes": [*CLAIMS],
    # See `WRITABLE_ATTRIBUTES`. Present and explicit, and naming neither claim.
    "WriteAttributes": [*WRITABLE_ATTRIBUTES],
    # An hour, matching `web/lib/cognito.ts`'s `SESSION_MAX_AGE_SECONDS`. Long
    # enough for a review session, short enough that a leaked token expires
    # before it is useful.
    "IdTokenValidity": 60,
    "AccessTokenValidity": 60,
    "TokenValidityUnits": {"IdToken": "minutes", "AccessToken": "minutes"},
}

# The two paths `web/` sends Cognito back to, read off the routes rather than
# invented: `web/app/api/auth/signin/route.ts` builds
# `${base}/api/auth/callback`, and `web/app/api/auth/logout/route.ts` builds
# `${base}/signin`. A mismatch is not a soft failure -- Cognito refuses the
# redirect and the reviewer never reaches the pool at all.
CALLBACK_PATH = "/api/auth/callback"
LOGOUT_PATH = "/signin"


def _urls(path: str, bases: tuple[str, ...]) -> list[str]:
    """`<base><path>` for every non-blank base, deduplicated, order preserved."""
    seen: dict[str, None] = {}
    for base in bases:
        if base and base.strip():
            seen.setdefault(f"{base.strip().rstrip('/')}{path}", None)
    return list(seen)


def origins(*dashboard_urls: str) -> tuple[str, ...]:
    """Every origin the pool will redirect to, plus localhost.

    `localhost` and NOT `127.0.0.1`: CLAUDE.md measured that they are the same
    machine and different origins to an OAuth provider, that the callback is
    compared as a string, and that mixing them answers `redirect_uri_mismatch` --
    which reads as a broken app rather than as a typo.
    `infra/selfhost/docker-compose.yml` sets `AUTH_URL` to `localhost`, so this
    matches it. Local development is how every remaining verification step in
    this repository is done, which is why it is never dropped.
    """
    return (*dashboard_urls, "http://localhost:3000")


def callback_urls(*dashboard_urls: str) -> list[str]:
    """`CallbackURLs`. `UpdateUserPoolClient` is a FULL REPLACE, so this must
    carry every origin at once -- passing one silently breaks the others."""
    return _urls(CALLBACK_PATH, origins(*dashboard_urls))


def logout_urls(*dashboard_urls: str) -> list[str]:
    """`LogoutURLs`. Same full-replace rule, and an unregistered `logout_uri` is
    refused by Cognito rather than ignored -- so a reviewer who signs out lands
    on an error page instead of the sign-in screen."""
    return _urls(LOGOUT_PATH, origins(*dashboard_urls))


def issuer(pool_id: str) -> str:
    """The `iss` claim, which is the pool's API host and NOT a sign-in domain.

    Worth stating because the two are easy to conflate: the JWKS `verifySession`
    fetches lives under this URL, so a token minted through any sign-in domain --
    the prefix one, a custom one added later -- verifies against exactly the same
    key set. Changing the sign-in page does not change `COGNITO_ISSUER`;
    changing the POOL does.
    """
    return f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}"


def hosted_domain() -> str:
    """The prefix hosted-UI domain, which is what `COGNITO_DOMAIN` must be.

    `web/lib/cognito.ts` builds `${domain()}/oauth2/token` and `${domain()}/logout`
    from it, so it is an ORIGIN with a scheme and no trailing slash.
    """
    return f"https://{DOMAIN_PREFIX}.auth.{REGION}.amazoncognito.com"
