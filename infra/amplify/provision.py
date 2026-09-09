"""Create or converge the Amplify app that hosts `web/`. Idempotent.

Run it from the repository root, as a module, so the namespace package resolves:

    .venv-main/bin/python -m infra.amplify.provision

**RAISING IS CORRECT HERE, INVERTING THE RULE `agentorg/` FOLLOWS**, for the
reason `infra/cognito/provision.py` states at length: this is a provisioning
script, not the request path, and a script that swallows a "not ready yet" error
reports success while the control is absent.

**THE ORDER IN `provision()` IS THE LOAD-BEARING PART, AND IT IS FORCED BY A
CIRCULARITY.** `AUTH_URL` is one of the four variables `amplify.yml` writes into
`.env.production`, and its value is the app's own branch URL -- which does not
exist until the app does. So the app is created FIRST with only the variables
that are knowable, its `defaultDomain` is read back, and only then is the
environment converged with the real `AUTH_URL`. Writing a placeholder on the
first pass and "fixing it later" is the shape `spec.environment_variables`
refuses outright: an interrupted run pins the sign-in redirect blank forever
while the console shows a key that is present.

**IT DOES NOT APPLY ANYTHING ON IMPORT.** Every function takes its client as a
parameter and constructs one only when the caller passes none, so the tests
drive the whole module against fakes with no credentials and no app.
"""

from __future__ import annotations

import boto3

from infra.amplify import spec


def _paginate(call, key: str, **kwargs):
    """Yield every item across every page of a `nextToken` API.

    **THE TOKEN KEY IS `nextToken`, LOWERCASE, AND COGNITO'S IS `NextToken`.**
    Verified against botocore 1.43.75's service model rather than assumed --
    `ListApps` declares members `['maxResults', 'nextToken']`. The two
    provisioners in `infra/` therefore cannot share this helper, and the
    copy-paste that reads correctly is the one that silently stops paging:
    boto3 ignores an unmodelled kwarg on some paths and raises on others, and
    the failure that matters is neither. A missed page here creates a SECOND
    app with the same name, and the second app has a different `defaultDomain`
    -- so `AUTH_URL`, the Cognito callback list and the app people actually
    reach are three different origins, and every sign-in fails the state check
    with a valid session behind it.
    """
    token: str | None = None
    while True:
        page = call(**kwargs, **({"nextToken": token} if token else {}))
        yield from page.get(key, [])
        token = page.get("nextToken")
        if not token:
            return


def find_app(client, name: str = spec.APP_NAME) -> dict | None:
    """The app record, or None. Scans every page; the first match wins."""
    for app in _paginate(client.list_apps, "apps", maxResults=100):
        if app.get("name") == name:
            return dict(app)
    return None


def branch_url(app: dict, branch: str = spec.BRANCH) -> str:
    """The origin a reviewer visits: `https://<branch>.<defaultDomain>`.

    **DERIVED, NEVER TYPED**, and this is the value `AUTH_URL` takes. Amplify
    returns `defaultDomain` as a bare host (`d1234abcd.amplifyapp.com`) with no
    scheme and no branch, so the full origin is assembled here in one place --
    the same argument `web/lib/origins.ts` makes for deriving the CSRF
    allow-list from `AUTH_URL` rather than declaring a second list: when two
    spellings of one origin drift, the symptom is a legitimately-clicked button
    being refused, which reads as a broken app.
    """
    domain = str(app.get("defaultDomain", "")).strip()
    if not domain:
        raise RuntimeError(
            f"app {app.get('appId')!r} reports no defaultDomain, so the origin "
            "AUTH_URL and the Cognito callback list must both name cannot be "
            "derived. Nothing was changed."
        )
    return f"https://{branch}.{domain}"


def converge_environment(client, app_id: str, **values: str) -> set[str]:
    """Merge this module's variables over a FRESH read. Returns what it carried.

    **THE FRESH READ IS THE WHOLE FUNCTION.** `update_app(environmentVariables=)`
    is a full replace of the map, and the Amplify console writes its own keys
    into that same map -- so a converge built from anything but a just-read copy
    deletes them. Measured on the reference deployment: a rebuilt map dropped
    `AMPLIFY_MONOREPO_APP_ROOT`, `AMPLIFY_DIFF_DEPLOY` and `_LIVE_UPDATES`, and
    the next build died 59 seconds in at CLONE time reporting `Cannot read
    'next' version in package.json` -- a deleted variable wearing the costume of
    a packaging fault.

    The carried keys are RETURNED rather than logged and forgotten: a key nobody
    can enumerate is a key nobody reviews, and this is the map whose accidental
    truncation cost the reference a build.
    """
    live = client.get_app(appId=app_id)["app"].get("environmentVariables", {})
    carried = spec.unowned_keys(live)
    client.update_app(
        appId=app_id,
        environmentVariables=spec.merged_environment(live, **values),
    )
    return carried


def converge_branch(client, app_id: str, branch: str = spec.BRANCH) -> bool:
    """Create the branch if absent, otherwise resend its settings. True if made.

    `framework` is stored per BRANCH, not per app, and Amplify accepts any
    string for it -- the reference measured `Nonsense - NotAFramework` stored
    verbatim -- so nothing validates this value for us and a wrong one changes
    how the artifact is hosted after a build that succeeded.
    """
    settings = {
        "framework": spec.FRAMEWORK,
        "stage": "PRODUCTION",
        "enableAutoBuild": True,
    }
    existing = {
        b.get("branchName")
        for b in _paginate(client.list_branches, "branches", appId=app_id, maxResults=50)
    }
    if branch in existing:
        client.update_branch(appId=app_id, branchName=branch, **settings)
        return False
    client.create_branch(appId=app_id, branchName=branch, **settings)
    return True


def provision(client=None, repository: str = "", access_token: str = "", **values: str) -> dict:
    """Create or converge the app, its branch and its environment.

    `repository` and `access_token` are OPTIONAL and their absence is a stated
    limitation rather than a silent one. **AN APP WITH NO REPOSITORY CONNECTION
    BUILDS NOTHING**, and it is created, configured and reported healthy all the
    same -- `get-app` answers, the environment is right, and no build has ever
    run. Modern Amplify connects GitHub through a GitHub App installation, which
    is a console authorisation a script cannot perform, so this function does
    the half it can do honestly and NAMES the half it cannot in its return
    value. Reading `repository_connected: False` as "provisioned" is the reading
    this key exists to prevent.
    """
    client = client or boto3.client("amplify", region_name=spec.REGION)

    app = find_app(client)
    if app is None:
        created = {
            "name": spec.APP_NAME,
            "platform": spec.PLATFORM,
            "buildSpec": spec.build_spec(),
            "enableBranchAutoBuild": True,
        }
        if repository:
            created["repository"] = repository
        if access_token:
            created["accessToken"] = access_token
        app = dict(client.create_app(**created)["app"])
    else:
        # The buildspec and the platform are resent on every converge, for
        # `UpdateUserPoolClient`'s reason one service over: the console can edit
        # both, and a spec edited there is a second copy of a file that lives in
        # git. `WEB` rather than `WEB_COMPUTE` is the one worth resending -- it
        # is a STATIC host, so `web/app/api/**` would simply not exist, and
        # every auth route plus `POST /api/approvals` would be absent while the
        # build and the deploy both reported success.
        client.update_app(
            appId=app["appId"], platform=spec.PLATFORM, buildSpec=spec.build_spec()
        )

    app_id = str(app["appId"])
    origin = branch_url(app)

    # AUTH_URL is derived from the app that now exists -- see this module's
    # header for why it cannot be supplied by the caller.
    carried = converge_environment(client, app_id, AUTH_URL=origin, **values)
    made = converge_branch(client, app_id)

    return {
        "app_id": app_id,
        "AUTH_URL": origin,
        "branch_created": made,
        "carried_keys": sorted(carried),
        # Named rather than inferred. See `provision`'s docstring.
        "repository_connected": bool(app.get("repository")),
    }


if __name__ == "__main__":
    import os

    result = provision(
        repository=os.getenv("AMPLIFY_REPOSITORY", ""),
        access_token=os.getenv("AMPLIFY_ACCESS_TOKEN", ""),
        COGNITO_ISSUER=os.getenv("COGNITO_ISSUER", ""),
        COGNITO_CLIENT_ID=os.getenv("COGNITO_CLIENT_ID", ""),
        COGNITO_DOMAIN=os.getenv("COGNITO_DOMAIN", ""),
    )
    for key, value in result.items():
        print(f"{key}: {value}")
    if not result["repository_connected"]:
        print(
            "\nNO REPOSITORY IS CONNECTED, so no build will ever run. Connect it "
            "in the Amplify console (GitHub App installation), then re-run this "
            "script -- it is idempotent and will preserve the environment."
        )
