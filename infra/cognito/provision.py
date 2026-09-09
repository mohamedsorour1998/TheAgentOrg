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


def _find_client(client, pool_id: str) -> str | None:
    """The app client's id, or None."""
    for existing in _paginate(
        client.list_user_pool_clients,
        "UserPoolClients",
        UserPoolId=pool_id,
        MaxResults=60,
    ):
        if existing.get("ClientName") == spec.CLIENT_NAME:
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
    }


if __name__ == "__main__":
    for key, value in provision().items():
        print(f"{key}: {value}")
