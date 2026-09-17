"""Create or converge the reviewer user pool. Idempotent: re-running is the
recovery path, and nothing here is safe only the first time.

Run it from the repository root, as a module, so the namespace package resolves:

    .venv-main/bin/python -m infra.cognito.provision

**RAISING IS CORRECT IN THIS FILE, WHICH INVERTS THE RULE THE REST OF THE
REPOSITORY FOLLOWS.** `agentorg/` degrades to a fixture rather than failing, and
`llm.text()` returning `None` on every failure is why four agents need no
`try/except`. This is a provisioning script, not the request path: a loud failure
blocks a deploy and the operator re-runs, which is exactly what idempotence is
for. A script that swallows a "not ready yet" error reports success while the
control is absent -- the reference deployment measured point-in-time recovery
coming out `ENABLED, ENABLED, DISABLED` across three runs of a script that
exited 0 every time.

**IT DOES NOT APPLY ANYTHING ON IMPORT.** Every function takes its client as a
parameter and constructs one only when the caller passes none, so
`tests/test_infra_cognito.py` drives the whole module against fakes with no
credentials, no network and no pool.
"""

from __future__ import annotations

import json
import secrets

import boto3
from botocore.exceptions import ClientError

from infra.cognito import spec


def _paginate(call, key: str, **kwargs):
    """Yield every item across every page of a `NextToken` API.

    **PAGINATION HERE IS NOT TIDINESS.** A missed page does not fail -- it
    creates a SECOND pool or a SECOND app client with the same name. Two clients
    means two client ids, `verifySession` checks `aud` against the one in the
    environment, and a token minted by the other client is then refused **with a
    valid signature**, which reads as "auth is broken" rather than as "there are
    two clients". The reference deployment hit the single-page version of this
    three separate times. Cheaper to page than to diagnose.
    """
    token: str | None = None
    while True:
        page = call(**kwargs, **({"NextToken": token} if token else {}))
        yield from page.get(key, [])
        token = page.get("NextToken")
        if not token:
            return


def _find_pool(client) -> str | None:
    """The pool's id, or None. Scans every page; the first match wins."""
    for pool in _paginate(client.list_user_pools, "UserPools", MaxResults=60):
        if pool.get("Name") == spec.POOL_NAME:
            return str(pool["Id"])
    return None


def _find_client(client, pool_id: str, name: str = "") -> str | None:
    """The named app client's id, or None. Defaults to the browser client.

    `name` was added when the SIGN-UP client arrived: two clients now live in this
    pool and they differ in exactly one thing that matters -- whether they may write
    `custom:tenant`. Matching on name rather than position is what keeps a converge
    from updating one with the other's spec, which would either strip the browser
    client's OAuth flows or grant it the claim the whole design withholds.
    """
    wanted = name or spec.CLIENT_NAME
    for existing in _paginate(
        client.list_user_pool_clients,
        "UserPoolClients",
        UserPoolId=pool_id,
        MaxResults=60,
    ):
        if existing.get("ClientName") == wanted:
            return str(existing["ClientId"])
    return None


def converge_attributes(client, pool_id: str) -> list[str]:
    """Add any declared custom attribute the live pool lacks. Returns the names.

    **A MISSING ATTRIBUTE IS RECOVERABLE; A WRONGLY-SHAPED ONE IS NOT**, and this
    function has to tell them apart rather than treating both as "converged".
    `AddCustomAttributes` is a real operation -- verified against botocore
    1.43.75's service model rather than a documentation page -- so an attribute
    nobody declared can be added to a pool that is already serving traffic. What
    AWS documents as impossible is deleting a custom attribute or changing its
    definition, so an attribute that exists with the wrong `Mutable` flag is a
    NEW POOL: new pool id, new issuer, every token invalid, every assigned tenant
    re-assigned.

    So this **RAISES** on a shape mismatch instead of silently leaving it. A
    converge that reported success against a `custom:tenant` created
    `Mutable: True` would leave the deployment with one guard where the comments
    claim two, and nothing would ever say so -- which is the exact defect the
    reference deployment shipped.
    """
    described = client.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    # Cognito returns custom attributes under their PREFIXED name in a describe
    # (`custom:role`), and takes them UNPREFIXED in a create (`role`). The
    # `SchemaAttributeType.Name` model documentation states both halves. Keying
    # on the prefixed form is what makes the comparison correct.
    live = {str(a.get("Name", "")): a for a in described.get("SchemaAttributes", [])}

    missing = []
    for declared in spec.CUSTOM_ATTRIBUTES:
        prefixed = f"custom:{declared['Name']}"
        found = live.get(prefixed)
        if found is None:
            missing.append(declared)
            continue
        if bool(found.get("Mutable")) != bool(declared["Mutable"]):
            raise RuntimeError(
                f"{prefixed} exists on {pool_id} with Mutable="
                f"{found.get('Mutable')}, and this spec declares "
                f"Mutable={declared['Mutable']}. A custom attribute's definition "
                "cannot be changed or deleted, so this pool cannot be converged "
                "onto the intended shape -- it needs a NEW pool, which changes "
                "the issuer and invalidates every token and every assigned "
                "tenant. Nothing was changed."
            )

    if missing:
        client.add_custom_attributes(
            UserPoolId=pool_id, CustomAttributes=[dict(a) for a in missing]
        )
    return [f"custom:{a['Name']}" for a in missing]


def _store_signup_secret(client_id: str, client_secret: str, secrets_client=None) -> bool:
    """Put the sign-up client's id and secret in Secrets Manager. True if written.

    **THE SECRET NEVER BECOMES AN AMPLIFY ENVIRONMENT VARIABLE**, and `amplify.yml`
    records why in its own header: those are visible in the console, in build logs,
    and inside a build artifact anyone who can call `get-job` may download -- three
    places at once, which is the shape of the `github_pat_` this repository already
    leaked into a Terraform plan artifact. The SSR runtime reads this at request
    time with the compute role instead.

    **IT IS NEVER RETURNED, LOGGED OR PRINTED**, which is why this answers a bool.
    `provision()` prints its whole result dictionary to stdout, and a CI log is one
    more place a credential does not belong.

    A BLANK SECRET IS A REFUSAL, not a write. Cognito returns `ClientSecret` only
    for a confidential client; a blank one means `GenerateSecret` did not take, and
    storing `""` would leave the sign-up route computing a SECRET_HASH from an empty
    key -- which Cognito rejects with `NotAuthorizedException`, a message about the
    client rather than about the secret.
    """
    if not client_secret:
        raise RuntimeError(
            f"the sign-up client {client_id} returned no ClientSecret. It must be "
            "confidential -- `GenerateSecret: True` is create-only, so a client "
            "created without it cannot be converged into one and must be deleted "
            "and remade. Nothing was stored."
        )

    secrets_client = secrets_client or boto3.client(
        "secretsmanager", region_name=spec.REGION
    )
    payload = json.dumps({"client_id": client_id, "client_secret": client_secret})
    try:
        secrets_client.create_secret(
            Name=spec.SIGNUP_SECRET_NAME,
            SecretString=payload,
            Description="Cognito sign-up client for server-side SignUp/ConfirmSignUp",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceExistsException":
            raise
        # CONVERGED, not skipped. A client that was deleted and remade has a new
        # secret, and a stale stored one fails every sign-up with a message about
        # the client id.
        secrets_client.put_secret_value(
            SecretId=spec.SIGNUP_SECRET_NAME, SecretString=payload
        )
    return True


def assign_tenant(username: str, tenant_id: str, client=None, pool_id: str = "") -> None:
    """Set one reviewer's `custom:tenant`, and PROVE it landed.

    **ASSIGNING A TENANT IS A COGNITO OPERATION AND NOT A SQL ONE**, and that is
    the whole point of the write lock: `web/` must never be able to write this
    attribute, so there is no route that can. It lives here.

    **THE READ-BACK IS THE ARBITER, NOT THE API CALL.** "The call returned" and
    "the attribute is set" are different claims and only the second one matters --
    the reference deployment's point-in-time-recovery finding, applied to the one
    write in this lane that a person's access depends on.

    It matters more than usual here because of a fact this lane could NOT
    measure: `custom:tenant` is `Mutable: False`, and whether
    `AdminUpdateUserAttributes` may set an immutable attribute that was never
    given a value is not answerable without creating a pool, which this lane was
    forbidden from doing. AWS's own model documentation says only "if an
    attribute is immutable, Amazon Cognito throws an error when it attempts to
    update the attribute", which does not distinguish the never-set case. If it
    turns out an immutable attribute cannot be set after creation, this function
    raises here rather than reporting success, and the fallback is to set the
    claim at `AdminCreateUser` time (which `provision` already does for the
    seeded reviewer, and which is known to work) or to declare `tenant`
    `Mutable: True` and rely on the `WriteAttributes` exclusion alone -- one
    guard rather than two, and a decision an operator should make knowingly.
    """
    client = client or boto3.client("cognito-idp", region_name=spec.REGION)
    pool_id = pool_id or (_find_pool(client) or "")
    if not pool_id:
        raise RuntimeError(f"no user pool named {spec.POOL_NAME}; nothing to assign.")

    client.admin_update_user_attributes(
        UserPoolId=pool_id,
        Username=username,
        UserAttributes=[{"Name": spec.TENANT_CLAIM, "Value": tenant_id}],
    )
    read_back = {
        str(a.get("Name")): a.get("Value")
        for a in client.admin_get_user(UserPoolId=pool_id, Username=username).get(
            "UserAttributes", []
        )
    }
    if read_back.get(spec.TENANT_CLAIM) != tenant_id:
        raise RuntimeError(
            f"{spec.TENANT_CLAIM} on {username} reads "
            f"{read_back.get(spec.TENANT_CLAIM)!r} after being set to "
            f"{tenant_id!r}. The call returned and the attribute did not change; "
            "see this function's docstring for the immutability fallback."
        )


def provision(client=None, dashboard_urls: tuple[str, ...] = ()) -> dict:
    """Create or converge the pool, the client, the domain and one reviewer.

    Returns exactly the three values `amplify.yml` writes into
    `.env.production`, plus the pool id an operator needs to assign a tenant.
    """
    client = client or boto3.client("cognito-idp", region_name=spec.REGION)

    pool_id = _find_pool(client)
    if pool_id is None:
        pool_id = str(client.create_user_pool(**spec.pool_spec())["UserPool"]["Id"])
        added: list[str] = []
    else:
        added = converge_attributes(client, pool_id)

    wanted = {
        **spec.CLIENT_SPEC,
        "UserPoolId": pool_id,
        "CallbackURLs": spec.callback_urls(*dashboard_urls),
        "LogoutURLs": spec.logout_urls(*dashboard_urls),
    }
    client_id = _find_client(client, pool_id)
    if client_id is None:
        client_id = str(
            client.create_user_pool_client(**wanted)["UserPoolClient"]["ClientId"]
        )
    else:
        # **`UpdateUserPoolClient` IS A FULL REPLACE, NOT A PATCH** -- measured on
        # the reference deployment: an update naming only `ClientName` left
        # `ReadAttributes`, `CallbackURLs` and `AllowedOAuthFlows` all ABSENT
        # afterwards. So this resends the whole spec. If somebody later "tidies"
        # it into a two-key delta, the deployed client silently loses its OAuth
        # flows and both `custom:` read permissions, every sign-in starts failing
        # closed, and nothing fails at provisioning time.
        #
        # `GenerateSecret` must be stripped: it is create-only and botocore
        # raises `ParamValidationError`, which is NOT a `ClientError`, so no
        # `except ClientError` anywhere would catch it.
        client.update_user_pool_client(
            **{k: v for k, v in wanted.items() if k != "GenerateSecret"},
            ClientId=client_id,
        )

    # ── EMAIL VERIFICATION ON AN EXISTING POOL ────────────────────────────────
    #
    # **`UpdateUserPool` IS A FULL REPLACE TOO**, and more dangerous than the client
    # one because its blast radius is the password policy. Measured on the live pool
    # before this existed: `AutoVerifiedAttributes: None`, so `SignUp` created a user
    # and emailed no code -- a sign-up form that appears to work and produces an
    # account nobody can confirm.
    #
    # So the wanted value is merged over a FRESH READ, exactly as
    # `infra/amplify/provision.converge_environment` does for the same reason.
    # Sending `AutoVerifiedAttributes` alone would reset `Policies` to Cognito's
    # default -- 8 characters, no symbol -- on a pool whose accounts approve
    # security gates, and nothing would report it.
    live = client.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    if sorted(live.get("AutoVerifiedAttributes") or []) != sorted(spec.AUTO_VERIFIED_ATTRIBUTES):
        client.update_user_pool(
            UserPoolId=pool_id,
            AutoVerifiedAttributes=[*spec.AUTO_VERIFIED_ATTRIBUTES],
            # Carried forward from the read, not retyped. `Schema` is absent
            # because it is not an `UpdateUserPool` parameter at all -- attributes
            # are added through `AddCustomAttributes`, which `converge_attributes`
            # already does.
            Policies=live["Policies"],
            AdminCreateUserConfig={
                k: v
                for k, v in live.get("AdminCreateUserConfig", {}).items()
                # Read-only on the way back out; sending it is a validation error.
                if k != "UnusedAccountValidityDays"
            },
            UserPoolTags=live.get("UserPoolTags", {}),
        )

    # ── THE SIGN-UP CLIENT ────────────────────────────────────────────────────
    #
    # See `spec.SIGNUP_CLIENT_SPEC`. It exists because `custom:tenant` is
    # `Mutable: False` and therefore settable only at CREATION, while the browser
    # client deliberately cannot write it -- so the server signs the user up.
    #
    # ITS SECRET IS READ BACK AND STORED, NEVER PRINTED. `create_user_pool_client`
    # is the only moment the secret is returned in full; after that it is
    # retrievable through `describe_user_pool_client`, so this converges rather than
    # depending on having caught it once.
    signup_id = _find_client(client, pool_id, name=spec.SIGNUP_CLIENT_NAME)
    signup_wanted = {**spec.SIGNUP_CLIENT_SPEC, "UserPoolId": pool_id}
    if signup_id is None:
        made = client.create_user_pool_client(**signup_wanted)["UserPoolClient"]
        signup_id = str(made["ClientId"])
        signup_secret = str(made.get("ClientSecret", ""))
    else:
        client.update_user_pool_client(
            **{k: v for k, v in signup_wanted.items() if k != "GenerateSecret"},
            ClientId=signup_id,
        )
        described = client.describe_user_pool_client(
            UserPoolId=pool_id, ClientId=signup_id
        )["UserPoolClient"]
        signup_secret = str(described.get("ClientSecret", ""))

    stored = _store_signup_secret(signup_id, signup_secret)

    # The prefix hosted-UI domain. One call, and it saves building a sign-in form
    # -- which is the option worth naming as rejected: a hand-built page would
    # post the reviewer's password to a route handler in the same process that
    # can approve a security gate, turning a page into a password-guessing
    # surface. Prefer the option that does not enlarge the credential surface.
    try:
        client.create_user_pool_domain(Domain=spec.DOMAIN_PREFIX, UserPoolId=pool_id)
    except ClientError as exc:
        # Only "it is already there". Anything else -- a domain taken by another
        # account, a throttle, an AccessDenied -- propagates, because reporting
        # success with no sign-in page is the shape this file exists to refuse.
        if exc.response["Error"]["Code"] not in {
            "InvalidParameterException",
            "AliasExistsException",
        }:
            raise

    # One seeded reviewer, with BOTH claims set at creation. `admin_create_user`
    # is an admin API and `WriteAttributes` does not bind it, which is what makes
    # this the one assignment path that is certainly available for an immutable
    # attribute. A generated password, printed once.
    try:
        password = f"Ag{secrets.token_urlsafe(16)}!7"
        client.admin_create_user(
            UserPoolId=pool_id,
            Username=spec.SEED_USERNAME,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": spec.ROLE_CLAIM, "Value": spec.ROLE_VALUE},
                {"Name": spec.TENANT_CLAIM, "Value": spec.DEFAULT_TENANT_VALUE},
            ],
            TemporaryPassword=password,
        )
        client.admin_set_user_password(
            UserPoolId=pool_id,
            Username=spec.SEED_USERNAME,
            Password=password,
            Permanent=True,
        )
        print(f"seeded {spec.SEED_USERNAME} with password: {password}")
        print("record it now -- it is not recoverable")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise

    return {
        "pool_id": pool_id,
        "COGNITO_CLIENT_ID": client_id,
        "COGNITO_ISSUER": spec.issuer(pool_id),
        "COGNITO_DOMAIN": spec.hosted_domain(),
        # Reported so a converge says out loud what it changed. An empty list
        # means the schema already matched; a non-empty one means this run added
        # a claim that was missing, which is worth seeing rather than inferring.
        "attributes_added": added,
        # The sign-up client's ID is not a secret and is reported; its SECRET is
        # written to Secrets Manager and never returned, logged or printed. The
        # boolean is what a converge can honestly say about it.
        "signup_client_id": signup_id,
        "signup_secret_stored": stored,
    }


if __name__ == "__main__":
    import os

    # **A BARE RUN USED TO SILENTLY NARROW THE CALLBACK LIST, AND IT BROKE SIGN-IN.**
    # Measured 2026-09-15: `python -m infra.cognito.provision` with no argument
    # called `provision()` with `dashboard_urls=()`, and because
    # `UpdateUserPoolClient` is a FULL REPLACE the deployed client came back with
    # only `http://localhost:3000/api/auth/callback`. The custom domain was gone, and
    # every sign-in then failed at the hosted UI with
    #
    #     /error?error=redirect_mismatch
    #
    # -- which reads as "auth is broken" rather than as "a list was replaced".
    # CLAUDE.md records this exact hazard for the callback list and it still
    # happened, because the DEFAULT was the dangerous value.
    #
    # So the entrypoint now supplies the deployed origin rather than relying on the
    # caller to remember. `AUTH_URL` is the same name `amplify.yml` writes into
    # `.env.production` and the same origin the app actually serves on, so the two
    # cannot disagree; the literal is the fallback for a machine that has not set it.
    origin = os.getenv("AUTH_URL", "https://theagentorg.rosettacloud.app").strip()
    for key, value in provision(dashboard_urls=(origin,)).items():
        print(f"{key}: {value}")


def ensure_branding(client, pool_id: str, client_id: str) -> str:
    """Apply managed login v2's branding for one app client. Idempotent.

    Returns the branding id.

    **`Assets` GOES ON BOTH PATHS.** The reference deployment measured that an
    update sending only `Settings` leaves the page asking for images that were never
    uploaded — worse than the flat page it replaced, because the switches are on and
    there is nothing behind them. So create and converge send the same two keys.

    **`UseCognitoProvidedValues` IS ABSENT, NOT `False`.** The API refuses it
    alongside `Settings`/`Assets` (they are alternatives, not a default plus an
    override), and sending it `False` "to be explicit" is the reading that fails.

    `Bytes` is raw UTF-8 and boto3 base64-encodes blob members itself. Encoding here
    as well produces an asset that uploads successfully and renders as nothing — the
    same two-interfaces-to-one-API trap as `invoke_agent_runtime`, where the CLI
    wants base64 and boto3 wants raw bytes.
    """
    assets = spec.branding_assets()
    settings = spec.branding_settings()

    try:
        existing = client.describe_managed_login_branding_by_client(
            UserPoolId=pool_id, ClientId=client_id
        )
        branding_id = existing["ManagedLoginBranding"]["ManagedLoginBrandingId"]
    except client.exceptions.ResourceNotFoundException:
        created = client.create_managed_login_branding(
            UserPoolId=pool_id,
            ClientId=client_id,
            Settings=settings,
            Assets=assets,
        )
        return created["ManagedLoginBranding"]["ManagedLoginBrandingId"]

    client.update_managed_login_branding(
        UserPoolId=pool_id,
        ManagedLoginBrandingId=branding_id,
        Settings=settings,
        Assets=assets,
    )
    return branding_id


def ensure_managed_login_version(client, pool_id: str, domain: str) -> int:
    """Move the domain onto managed login v2, and READ IT BACK.

    Returns the version the domain reports AFTERWARDS, never the one that was asked
    for. `assign_tenant` is the precedent and the reason: its read-back is the only
    thing that found the `Mutable: False` dead end, and a provisioner that reports
    what it SENT cannot discover that the account disagreed.

    **THIS IS SAFE ONLY BECAUSE `hostedUiUrl` BUILDS `/oauth2/authorize`.** `/login`
    is version-specific; the standard OAuth endpoint is served by both. Verified
    against this pool at v1 before the upgrade: 302 -> /login -> 200. Flipping the
    version with a deployed bundle pointing at `/login` is the failure this ordering
    exists to avoid.
    """
    current = client.describe_user_pool_domain(Domain=domain)["DomainDescription"]
    if current.get("ManagedLoginVersion") == spec.MANAGED_LOGIN_VERSION:
        return spec.MANAGED_LOGIN_VERSION

    client.update_user_pool_domain(
        Domain=domain,
        UserPoolId=pool_id,
        ManagedLoginVersion=spec.MANAGED_LOGIN_VERSION,
    )
    after = client.describe_user_pool_domain(Domain=domain)["DomainDescription"]
    return int(after.get("ManagedLoginVersion", 0))
