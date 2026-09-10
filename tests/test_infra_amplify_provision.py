"""The Amplify provisioner. Owner: Lane Q's unfinished half.

`infra/amplify/spec.py` shipped with eleven tests and no module that applies it,
so this lane was the repository's second named pattern in its purest form -- a
capability that is complete, tested, and reached by nothing. These tests cover
the half that talks to AWS, against a fake client, so they run in the hermetic
suite alongside everything else.

WHAT THEY DEFEND. Every assertion below is a setting whose wrong value produces
a provisioning run that reports success:

  * a list that stops after page one creates a SECOND app rather than finding
    the first, and the second has a different `defaultDomain`;
  * an environment converge built from anything but a fresh read deletes the
    console's own keys, and the next build dies at CLONE time;
  * an `AUTH_URL` supplied by a caller instead of derived can name an origin the
    app does not answer on, which is a sign-in loop rather than an error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.amplify import provision, spec

LIVE_VALUES = {
    "COGNITO_ISSUER": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Pyt161csn",
    "COGNITO_CLIENT_ID": "2uc5d3stt4912418sh5m4btt0s",
    "COGNITO_DOMAIN": "https://theagentorg-shared-reviewers.auth.us-east-1.amazoncognito.com",
}


class FakeAmplify:
    """A fake that can express a SECOND page and a SECOND app.

    Deliberately not a `Mock`. This repository has found fifteen instances of a
    double that could not express the failing case, and the failing case here is
    pagination -- so `list_apps` genuinely pages, one app at a time, and a
    provisioner that ignores `nextToken` sees exactly one of them.
    """

    def __init__(self, apps=(), branches=(), environment=None, domains=()):
        self.apps = [dict(a) for a in apps]
        self.branches = list(branches)
        self.environment = dict(environment or {})
        # DEFAULTS TO EMPTY, WHICH IS THE REAL DEFAULT: an app with no custom
        # domain attached. The three tests written before `custom_origin`
        # existed failed with AttributeError the moment it was added, which is
        # this double doing its job -- a fake that cannot express a call the
        # code now makes is the fifteen-times-over pattern, and the fix is to
        # model the call rather than to stub the method away at each call site.
        self.domains = [dict(d) for d in domains]
        self.calls: list[tuple] = []

    def list_domain_associations(self, appId, maxResults=None, nextToken=None):
        self.calls.append(("list_domain_associations", appId))
        return {"domainAssociations": self.domains}

    def list_apps(self, maxResults=None, nextToken=None):
        index = int(nextToken or 0)
        self.calls.append(("list_apps", index))
        if index >= len(self.apps):
            return {"apps": []}
        page = {"apps": [self.apps[index]]}
        if index + 1 < len(self.apps):
            page["nextToken"] = str(index + 1)
        return page

    def create_app(self, **kwargs):
        self.calls.append(("create_app", kwargs))
        made = {
            "appId": "d-new",
            "name": kwargs["name"],
            "defaultDomain": "dnew123.amplifyapp.com",
            "platform": kwargs.get("platform"),
            "repository": kwargs.get("repository", ""),
            "environmentVariables": {},
        }
        self.apps.append(made)
        return {"app": made}

    def update_app(self, **kwargs):
        self.calls.append(("update_app", kwargs))
        if "environmentVariables" in kwargs:
            self.environment = dict(kwargs["environmentVariables"])
        return {"app": {}}

    def get_app(self, appId):
        self.calls.append(("get_app", appId))
        return {"app": {"appId": appId, "environmentVariables": dict(self.environment)}}

    def list_branches(self, appId, maxResults=None, nextToken=None):
        self.calls.append(("list_branches", appId))
        return {"branches": [{"branchName": b} for b in self.branches]}

    def create_branch(self, **kwargs):
        self.calls.append(("create_branch", kwargs))
        self.branches.append(kwargs["branchName"])
        return {"branch": {}}

    def update_branch(self, **kwargs):
        self.calls.append(("update_branch", kwargs))
        return {"branch": {}}


# ── pagination: the defect that creates a second app ──────────────────────────

def test_find_app_reads_past_the_first_page():
    """A single-page list finds nothing and the caller CREATES A SECOND APP.

    Not a tidiness assertion. The reference deployment hit the single-page shape
    three separate times on the Cognito side, and here the consequence is worse
    than a duplicate: two apps have two `defaultDomain`s, so `AUTH_URL`, the
    Cognito callback list and the app people actually reach become three
    different origins. Every sign-in then fails the state check while holding a
    perfectly valid session, which reads as "auth is broken".
    """
    client = FakeAmplify(apps=[
        {"appId": "d-other", "name": "something-else", "defaultDomain": "a.amplifyapp.com"},
        {"appId": "d-ours", "name": spec.APP_NAME, "defaultDomain": "b.amplifyapp.com"},
    ])

    found = provision.find_app(client)

    assert found is not None, (
        "the app was on page two and find_app stopped at page one; a caller now "
        "creates a SECOND app with the same name and a different defaultDomain"
    )
    assert found["appId"] == "d-ours", found


def test_the_pagination_token_is_lowercase_nextToken():
    """Amplify's is `nextToken`; Cognito's is `NextToken`.

    Asserted as a real second page rather than by reading source, because the
    two provisioners in `infra/` sit beside each other and the copy-paste that
    reads correctly is the one that silently stops paging.
    """
    client = FakeAmplify(apps=[
        {"appId": "d-1", "name": "x", "defaultDomain": "a.amplifyapp.com"},
        {"appId": "d-2", "name": "y", "defaultDomain": "b.amplifyapp.com"},
    ])

    list(provision._paginate(client.list_apps, "apps", maxResults=100))

    requested = [index for name, index in client.calls if name == "list_apps"]
    assert requested == [0, 1], (
        f"pages requested were {requested}; a second page was never fetched, so "
        f"the token key is wrong or unread"
    )


# ── the full-replace trap, on the live API rather than in the spec ────────────

def test_converge_reads_fresh_and_preserves_what_the_console_wrote():
    """`update_app(environmentVariables=)` is a FULL REPLACE.

    `tests/test_infra_amplify.py` pins the merge as a pure function; this pins
    that the provisioner actually READS before it merges. A converge built from
    a stale copy dropped `AMPLIFY_MONOREPO_APP_ROOT`, `AMPLIFY_DIFF_DEPLOY` and
    `_LIVE_UPDATES` on the reference, and the next build died 59 seconds in at
    clone time reporting `Cannot read 'next' version in package.json` -- a
    deleted variable wearing the costume of a packaging fault.
    """
    client = FakeAmplify(environment={
        "AMPLIFY_DIFF_DEPLOY": "false",
        "_LIVE_UPDATES": "[]",
        "COGNITO_ISSUER": "stale",
    })

    carried = provision.converge_environment(
        client, "d-ours", AUTH_URL="https://main.d1.amplifyapp.com", **LIVE_VALUES
    )

    assert ("get_app", "d-ours") in client.calls, (
        "no fresh read was made, so the map sent to update_app cannot contain "
        "keys this module does not own"
    )
    assert client.environment["AMPLIFY_DIFF_DEPLOY"] == "false", (
        "a console-written key was deleted; the next build fails at clone time"
    )
    assert client.environment["_LIVE_UPDATES"] == "[]", "the Node pin was dropped"
    assert client.environment["COGNITO_ISSUER"] == LIVE_VALUES["COGNITO_ISSUER"]
    assert carried == {"AMPLIFY_DIFF_DEPLOY", "_LIVE_UPDATES"}, sorted(carried)


# ── AUTH_URL, and why no caller may supply it ─────────────────────────────────

def test_auth_url_is_derived_from_the_app_and_not_from_the_caller():
    """The circularity: `AUTH_URL` is the app's own origin, and the app must
    exist before it can be known.

    A caller-supplied value can name an origin the app does not answer on, and
    the symptom is a redirect loop rather than an error -- Cognito redirects to
    it, nothing serves the callback, and the state cookie expires ten minutes
    later.
    """
    client = FakeAmplify()

    result = provision.provision(client, **LIVE_VALUES)

    assert result["AUTH_URL"] == f"https://{spec.BRANCH}.dnew123.amplifyapp.com", (
        result["AUTH_URL"]
    )
    assert client.environment["AUTH_URL"] == result["AUTH_URL"], (
        "the derived origin was reported and not written"
    )


def test_an_app_with_no_default_domain_refuses_rather_than_building_a_url():
    """`https://main.` is a syntactically fine URL that resolves to nothing."""
    with pytest.raises(RuntimeError, match="defaultDomain"):
        provision.branch_url({"appId": "d-x", "defaultDomain": ""})


# ── the two things a green provisioning run can still be missing ──────────────

def test_a_missing_repository_connection_is_REPORTED_and_not_inferred():
    """An app with no GitHub connection is created, configured, and builds
    NOTHING -- while `get-app` answers and the environment is correct.

    So "provisioned" and "will ever deploy" are different facts, and this key is
    the only thing that separates them. Same shape as `scan_provenance`: a run
    that fell back must not be indistinguishable from one nobody measured.
    """
    client = FakeAmplify()

    result = provision.provision(client, **LIVE_VALUES)

    assert result["repository_connected"] is False, (
        "no repository was passed, so a build can never run; reporting this as "
        "connected makes a dead app read as a live one"
    )


def test_the_platform_is_resent_on_every_converge():
    """`WEB` is a STATIC host, and the console can set it.

    On a static platform `web/app/api/**` does not exist -- so all three Cognito
    auth routes and `POST /api/approvals`, the one surface here that can open a
    human gate over a network, would be absent while the build and the deploy
    both reported success.
    """
    client = FakeAmplify(apps=[
        {"appId": "d-ours", "name": spec.APP_NAME, "defaultDomain": "b.amplifyapp.com",
         "platform": "WEB", "repository": "https://github.com/x/y"},
    ])

    provision.provision(client, **LIVE_VALUES)

    resent = [k for name, k in client.calls
              if name == "update_app" and k.get("platform")]
    assert resent, "an existing app's platform was never resent"
    assert resent[0]["platform"] == "WEB_COMPUTE", resent[0]["platform"]


# ── the custom domain, and the re-run that would silently break sign-in ───────

def _with_domains(client, associations):
    """Attach domain associations to an existing fake.

    Sets the DATA the fake's own method reads, rather than replacing the method
    with a lambda -- a replaced method stops recording into `calls`, so a test
    could no longer tell whether the call was made at all.
    """
    client.domains = [dict(a) for a in associations]
    return client


def test_a_custom_domain_wins_over_the_amplify_default():
    """The app keeps its `defaultDomain` forever, so `branch_url` goes on
    returning a URL that WORKS and is no longer the one people use.

    The damage is not a 404. Sign-in would redirect to Cognito carrying
    `redirect_uri=https://main.<id>.amplifyapp.com/api/auth/callback`, which is
    not in the pool's callback list, so Cognito refuses it -- and every value in
    the Amplify console still reads present and correct.
    """
    client = _with_domains(FakeAmplify(apps=[
        {"appId": "d-ours", "name": spec.APP_NAME, "defaultDomain": "d9.amplifyapp.com"},
    ]), [{
        "domainName": "rosettacloud.app",
        "domainStatus": "AVAILABLE",
        "subDomains": [{"subDomainSetting": {"prefix": "theagentorg", "branchName": "main"}}],
    }])

    result = provision.provision(client, **LIVE_VALUES)

    assert result["AUTH_URL"] == "https://theagentorg.rosettacloud.app", result["AUTH_URL"]
    assert result["origin_source"] == "custom-domain", result["origin_source"]
    assert client.environment["AUTH_URL"] == "https://theagentorg.rosettacloud.app"


def test_a_FAILED_association_is_skipped_rather_than_trusted():
    """Its subdomains are the ones that did NOT take, so naming one points
    `AUTH_URL` at a host that does not resolve -- measured live: a failed
    association left `theagentorg.rosettacloud.app` CNAMEd to a deleted
    CloudFront distribution with no A record at all."""
    client = _with_domains(FakeAmplify(apps=[
        {"appId": "d-ours", "name": spec.APP_NAME, "defaultDomain": "d9.amplifyapp.com"},
    ]), [{
        "domainName": "rosettacloud.app",
        "domainStatus": "FAILED",
        "subDomains": [{"subDomainSetting": {"prefix": "theagentorg", "branchName": "main"}}],
    }])

    result = provision.provision(client, **LIVE_VALUES)

    assert result["AUTH_URL"] == "https://main.d9.amplifyapp.com", (
        "a FAILED association was used as the origin; that host does not resolve"
    )
    assert result["origin_source"] == "amplify-default"


def test_a_root_domain_association_does_not_produce_a_leading_dot():
    """An empty prefix is the ROOT domain -- a legitimate setting, not a missing
    value. `https://.rosettacloud.app` is what a naive f-string produces and it
    reaches Cognito's callback list, where it matches nothing."""
    client = _with_domains(FakeAmplify(), [{
        "domainName": "rosettacloud.app",
        "domainStatus": "AVAILABLE",
        "subDomains": [{"subDomainSetting": {"prefix": "", "branchName": "main"}}],
    }])

    assert provision.custom_origin(client, "d-ours") == "https://rosettacloud.app"


def test_a_domain_bound_to_a_DIFFERENT_branch_is_not_this_branchs_origin():
    """Two branches, two origins. A preview branch's domain must not become the
    production `AUTH_URL`."""
    client = _with_domains(FakeAmplify(), [{
        "domainName": "rosettacloud.app",
        "domainStatus": "AVAILABLE",
        "subDomains": [{"subDomainSetting": {"prefix": "preview", "branchName": "staging"}}],
    }])

    assert provision.custom_origin(client, "d-ours") == ""
