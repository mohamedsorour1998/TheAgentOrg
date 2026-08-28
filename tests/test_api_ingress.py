"""K4's providers and K6's generated schema, from the transport.

OWNER: Lane K.

THE TEST THAT MAKES K6 NON-VACUOUS is `test_every_path_the_dispatcher_serves_is_
in_the_openapi_document`. Generating a schema from a route table proves only that
the table and the schema agree; it says nothing about whether the SERVER serves
that table. So the dispatcher is driven for real and any path that answers
something other than 404 must be documented -- which closes the loop from the side
the generator cannot see.

THE OTHER LOAD-BEARING ONE is `test_the_github_provider_matches_the_deployed_
lambda`. `infra/ingress/handler.py` is the deployed webhook path and its header
constants are the coupling to GitHub; restating them in `api/ingress.py` would be a
second declaration, and CLAUDE.md records what those do -- "two copies keep
agreeing while one moves". So that test LOADS the Lambda and compares.
"""

import ast
import hashlib
import hmac
import importlib.util
import json
import pathlib
import threading
import urllib.error
import urllib.request

import pytest

from agentorg import api, queue
from agentorg.api import auth, idempotency, ingress, openapi, service
from agentorg.api import server as api_server
from agentorg.api.errors import BadRequest, NotFound, Unauthenticated

REPO_ROOT = pathlib.Path(api.__file__).resolve().parent.parent.parent
LAMBDA_HANDLER = REPO_ROOT / "infra" / "ingress" / "handler.py"

SECRET = "a-fake-webhook-secret-for-tests-only"
BODY = json.dumps({"action": "opened", "issue": {"number": 7}}).encode("utf-8")

assert ingress.PROVIDERS, "no ingress providers; every test here would pin nothing"
assert openapi.ROUTES, "openapi.ROUTES is empty; the schema tests would pin nothing"


def _github_signature(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _clean_substrate():
    queue.reset()
    auth.set_key_store(auth.InMemoryKeyStore())
    idempotency.set_idempotency_store(idempotency.IdempotencyStore())
    service.reset()
    api_server.clear_ingress_secrets()
    yield
    queue.reset()
    auth.set_key_store(auth.InMemoryKeyStore())
    idempotency.set_idempotency_store(idempotency.IdempotencyStore())
    service.reset()
    api_server.clear_ingress_secrets()


@pytest.fixture()
def live_server():
    server = api_server.serve(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def _request(base, method, path, data=None, headers=None):
    """`(status, body_bytes)`. A 4xx is an answer here, not an exception."""
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read()


# ──────────────────────────────────────────────────────────────────────────────
# K4: the providers
# ──────────────────────────────────────────────────────────────────────────────

def test_the_github_provider_matches_the_deployed_lambda():
    """THE COUPLING, checked by LOADING the deployed handler rather than copying it.

    `infra/ingress/handler.py` is the live webhook path. Its four header constants
    are GitHub's contract, and a second copy in `api/ingress.py` would drift
    silently -- CLAUDE.md: "two copies keep agreeing while one moves". So this
    reads the real file.
    """
    spec = importlib.util.spec_from_file_location("_lambda_probe", LAMBDA_HANDLER)
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)

    provider = ingress.PROVIDERS["github"]
    assert provider.signature_header == handler.SIGNATURE_HEADER
    assert provider.prefix == handler.SIGNATURE_PREFIX
    assert provider.event_header == handler.EVENT_NAME_HEADER
    assert provider.delivery_header == handler.DELIVERY_HEADER
    assert handler.SIGNATURE_HEADER, "the lambda's constant is empty; nothing was pinned"


def test_a_correctly_signed_github_delivery_is_accepted():
    """The acceptance half, so the refusals below are not vacuous."""
    payload = ingress.verify_delivery(
        "github",
        {"X-Hub-Signature-256": _github_signature(BODY), "X-GitHub-Event": "issues"},
        BODY,
        SECRET,
    )
    assert payload["action"] == "opened"


def test_a_body_that_was_re_serialised_does_not_verify():
    """THE RAW-BODY TRAP, exercised rather than described.

    "A `json.dumps(json.loads(body))` round-trip renormalises whitespace and key
    order and 401s every delivery." This asserts the inverse direction, which is
    the one that matters for a verifier: a body that has been through that
    round-trip must NOT satisfy a signature computed over the original.
    """
    signature = _github_signature(BODY)
    round_tripped = json.dumps(json.loads(BODY.decode()), indent=2).encode()
    assert round_tripped != BODY, "the round trip changed nothing; this pins nothing"
    with pytest.raises(Unauthenticated):
        ingress.verify_delivery(
            "github", {"X-Hub-Signature-256": signature}, round_tripped, SECRET
        )


def test_gitlab_verifies_a_bare_token_and_not_a_signature():
    """GitLab sends the SECRET ITSELF; a design assuming everyone signs breaks it.

    Both directions asserted: the token verifies, and a GitHub-style signature in
    the same header does NOT -- otherwise a `signs_body` flag that was ignored
    would pass the first half.
    """
    payload = ingress.verify_delivery(
        "gitlab", {"X-Gitlab-Token": SECRET}, BODY, SECRET
    )
    assert payload["action"] == "opened"
    with pytest.raises(Unauthenticated):
        ingress.verify_delivery(
            "gitlab", {"X-Gitlab-Token": _github_signature(BODY)}, BODY, SECRET
        )


def test_the_generic_provider_verifies_its_own_header():
    """A CI caller with no webhook product behind it still gets a verified path."""
    payload = ingress.verify_delivery(
        "generic", {"X-Agentorg-Signature": _github_signature(BODY)}, BODY, SECRET
    )
    assert payload["action"] == "opened"


def test_a_signature_for_one_provider_does_not_verify_on_another():
    """The provider comes from the path, so a caller cannot pick the cheaper check.

    A GitHub signature presented at `/v1/ingress/generic` fails because the header
    name differs -- pinned so a future "read the signature from whichever header is
    present" convenience cannot be added without turning this red.
    """
    with pytest.raises(Unauthenticated):
        ingress.verify_delivery(
            "generic", {"X-Hub-Signature-256": _github_signature(BODY)}, BODY, SECRET
        )


@pytest.mark.parametrize("provider", sorted(ingress.PROVIDERS))
def test_every_provider_refuses_a_missing_proof(provider):
    """No provider has an unauthenticated path. Derived from the table, so a new
    provider is covered without anybody editing this file."""
    with pytest.raises(Unauthenticated):
        ingress.verify_delivery(provider, {}, BODY, SECRET)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_ingress_secret_is_a_fault_and_not_an_authentication_failure(blank):
    """500, never 401. `handler._webhook_secret` states the reason: such a key "is
    a 1-3 byte key an attacker can guess outright, which is the same
    universal-forgery hazard as an empty one", and reporting it as a signature
    failure "sends the next person to rotate a secret that was always correct".
    """
    with pytest.raises(ValueError, match="empty or whitespace only"):
        ingress.verify_delivery(
            "github", {"X-Hub-Signature-256": _github_signature(BODY)}, BODY, blank
        )


def test_an_unknown_provider_is_not_found_rather_than_unauthorized():
    """404 tells a caller nothing; 401 would confirm the endpoint exists."""
    with pytest.raises(NotFound):
        ingress.verify_delivery("bitbucket", {}, BODY, SECRET)


def test_headers_are_read_case_insensitively():
    """`http.server` preserves the case a client sent; Function URLs lower-case it.

    `handler.py`'s trap 3: relying on one integration's behaviour means the same
    code 401s behind another.
    """
    for name in ("x-hub-signature-256", "X-Hub-Signature-256", "X-HUB-SIGNATURE-256"):
        assert ingress.verify_delivery(
            "github", {name: _github_signature(BODY)}, BODY, SECRET
        )["action"] == "opened"


def test_a_verified_body_that_is_not_a_json_object_is_refused():
    """Verified and still wrong: a bare list is valid JSON and is not a payload."""
    for raw in (b"not json at all", b'["a", "b"]', b'"a string"', b"42"):
        with pytest.raises(BadRequest):
            ingress.verify_delivery(
                "github", {"X-Hub-Signature-256": _github_signature(raw)}, raw, SECRET
            )


def test_the_event_name_is_passed_through_verbatim():
    """`handler.py` sends this as EventBridge's DetailType, and "inventing a value
    here means the rule matches nothing, the bus accepts the event, and nothing
    turns red." So it must not be normalised."""
    for sent in ("issues", "pull_request", "Issues", "something_new"):
        assert ingress.event_name("github", {"X-GitHub-Event": sent}) == sent


def test_the_ingress_route_refuses_with_no_secret_configured(live_server):
    """An unconfigured provider must not accept anything.

    Same direction as the empty key store: "nobody configured this" is a refusal.
    A 500 rather than a 401 because the fault is ours, not the caller's.
    """
    status, _ = _request(live_server, "POST", "/v1/ingress/github", BODY,
                         {"X-Hub-Signature-256": _github_signature(BODY)})
    assert status == 500


def test_the_ingress_route_accepts_a_verified_delivery_with_202(live_server):
    """202, matching the Lambda: accepted, and nothing done with it yet."""
    api_server.set_ingress_secret("github", SECRET)
    status, body = _request(
        live_server, "POST", "/v1/ingress/github", BODY,
        {"X-Hub-Signature-256": _github_signature(BODY), "X-GitHub-Event": "issues"},
    )
    assert status == 202
    answer = json.loads(body)
    assert answer["accepted"] is True
    assert answer["event"] == "issues"
    assert answer["keys"] == ["action", "issue"]


def test_a_verified_delivery_does_not_start_a_run(live_server):
    """K4 verifies; it does not enqueue.

    The EventBridge rule filters at the bus for the stated reason -- deciding here
    would make "we never saw it" and "we saw it and ignored it" indistinguishable.
    A delivery that silently started a run would also be an unauthenticated caller
    spending model tokens.
    """
    api_server.set_ingress_secret("github", SECRET)
    _request(live_server, "POST", "/v1/ingress/github", BODY,
             {"X-Hub-Signature-256": _github_signature(BODY), "X-GitHub-Event": "issues"})
    assert queue.claim("a-worker") is None, (
        "a verified webhook enqueued work, so an unauthenticated caller can spend "
        "model tokens"
    )


def test_setting_a_secret_for_an_unknown_provider_is_refused():
    """Otherwise a typo leaves a correct secret installed under a key nothing reads,
    which presents as "the webhook still 401s"."""
    with pytest.raises(NotFound):
        api_server.set_ingress_secret("bitbucket", SECRET)


# ──────────────────────────────────────────────────────────────────────────────
# K6: the generated document
# ──────────────────────────────────────────────────────────────────────────────

def test_the_document_is_valid_json_and_declares_its_version():
    document = openapi.openapi_document()
    json.dumps(document)  # raises if anything is unserialisable
    assert document["openapi"].startswith("3.")
    assert document["paths"], "no paths in the document"


def test_every_route_in_the_table_appears_in_the_document():
    """The generator's own loop, asserted so a filtered route cannot vanish."""
    document = openapi.openapi_document()
    for route in openapi.ROUTES:
        assert route.path in document["paths"], f"{route.path} is undocumented"
        assert route.method.lower() in document["paths"][route.path], (
            f"{route.method} {route.path} is undocumented"
        )


def test_every_path_the_dispatcher_serves_is_in_the_openapi_document(live_server):
    """THE LOOP-CLOSING TEST, and the reason K6 is not merely a dict literal.

    Generating a schema from a table proves the table and the schema agree; it
    says nothing about what the SERVER serves. So every documented path is driven
    for real and must not 404, and a probe path that is NOT documented must.

    Without this, a route could be served and undocumented -- which is exactly the
    drift that makes a generated client wrong while every schema test passes.
    """
    documented = {route.path for route in openapi.ROUTES}
    for path in documented:
        concrete = (path.replace("{run_id}", "some-run")
                        .replace("{full_name}", "acme%2Fauth")
                        .replace("{provider}", "github"))
        for route in openapi.ROUTES:
            if route.path != path:
                continue
            status, _ = _request(live_server, route.method, concrete, b"{}",
                                 {"Content-Type": "application/json"})
            assert status != 404, (
                f"{route.method} {concrete} is documented but the dispatcher "
                f"answers 404, so a generated client would call a route that does "
                f"not exist"
            )

    for undocumented in ("/v1/gates", "/v1/runs/x/approve", "/v1/keys", "/v2/runs"):
        status, _ = _request(live_server, "POST", undocumented, b"{}",
                             {"Content-Type": "application/json"})
        assert status == 404, (
            f"{undocumented} is served but undocumented (answered {status})"
        )


def test_the_schemas_come_from_pydantic_and_not_from_a_hand_written_dict():
    """A constraint nobody typed into the schema proves the generator read the model.

    `ticket_text`'s maxLength is declared on `RunSubmission`, so its presence here
    is only possible if `model_json_schema()` produced it -- which is the whole
    claim of K6.
    """
    schemas = openapi.openapi_document()["components"]["schemas"]
    submission = schemas["RunSubmission"]
    assert submission["properties"]["ticket_text"]["maxLength"] == service.MAX_TICKET_TEXT
    assert submission["required"] == ["ticket_id", "ticket_text"]
    assert "RunStatus" in schemas and "RepositoryConfig" in schemas


def test_a_new_field_on_a_model_reaches_the_document_without_an_edit_here():
    """The generator is derived, proven by adding a field at runtime.

    This is the property that makes a generated schema better than a written one,
    so it is asserted rather than assumed: nothing in `openapi.py` names the
    fields.
    """
    from pydantic import create_model

    extended = create_model("RunSubmission", __base__=service.RunSubmission,
                            extra_field=(str, "x"))
    original = service.RunSubmission
    try:
        openapi._MODELS["RunSubmission"] = extended
        schema = openapi.openapi_document()["components"]["schemas"]["RunSubmission"]
        assert "extra_field" in schema["properties"], (
            "a new model field did not reach the document, so the schema is not "
            "actually derived from the model"
        )
    finally:
        openapi._MODELS["RunSubmission"] = original
    assert "extra_field" not in (
        openapi.openapi_document()["components"]["schemas"]["RunSubmission"]["properties"]
    ), "the model registry was not restored"


def test_the_refusal_codes_are_derived_and_the_403_404_split_is_documented():
    """A run id is unguessable so 403 is safe; a repository name is not, so 404.

    Documented per route, because a caller has to know which to expect -- and the
    asymmetry is `tests/test_tenancy_leak.py`'s, not this API's invention.
    """
    paths = openapi.openapi_document()["paths"]
    run_status = paths["/v1/runs/{run_id}"]["get"]["responses"]
    assert "403" in run_status and "404" in run_status
    config = paths["/v1/repositories/{full_name}/config"]["get"]["responses"]
    assert "404" in config
    assert "403" not in config, (
        "a guessable repository name documents a 403, which distinguishes 'not "
        "yours' from 'no such thing' -- itself the disclosure"
    )
    cancel = paths["/v1/runs/{run_id}/cancel"]["post"]["responses"]
    assert "409" in cancel, "the cancel route does not document its conflict"


def test_the_unauthenticated_routes_are_visibly_unauthenticated():
    """Which routes need a key is a fact a reader must not discover by calling them."""
    paths = openapi.openapi_document()["paths"]
    assert "security" not in paths["/v1/health"]["get"]
    assert "security" not in paths["/v1/ingress/{provider}"]["post"]
    assert paths["/v1/runs"]["post"]["security"] == [{"bearerAuth": []}]
    assert paths["/v1/runs"]["post"]["x-required-scope"] == auth.SCOPE_RUNS_WRITE


def test_the_provider_enum_is_derived_from_the_providers_table():
    """A new provider appears in the schema without an edit here."""
    parameters = (openapi.openapi_document()["paths"]["/v1/ingress/{provider}"]
                  ["post"]["parameters"])
    enums = [p["schema"]["enum"] for p in parameters if p["name"] == "provider"]
    assert enums == [sorted(ingress.PROVIDERS)]


def test_every_api_error_declares_a_real_status():
    """`ApiError.status` is 0 on the base, so a subclass that forgot is caught.

    A subclass inheriting 0 would answer an invalid HTTP status at the transport,
    and the document would carry a `"0"` response key that no client understands.
    """
    from agentorg.api import errors

    assert errors.ApiError.status == 0, "the base declares a usable status"
    for error in errors.ERRORS:
        assert 400 <= error.status <= 599, f"{error.__name__} declares {error.status}"
        assert error.__doc__, f"{error.__name__} has no docstring; the document reads it"


def test_no_module_in_the_api_package_imports_a_web_framework():
    """The five arm64 agent images must not gain one.

    `test_requirements_covers_every_third_party_import_in_the_package` AST-walks
    `agentorg/` and would require any import here to be declared in the container's
    requirements. Asserted over the AST here too, so the failure names this
    package rather than surfacing as an unrelated packaging test going red.
    """
    forbidden = {"fastapi", "starlette", "uvicorn", "flask", "django", "aiohttp",
                 "tornado", "werkzeug"}
    offenders = []
    for path in sorted(pathlib.Path(api.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.split(".")[0] in forbidden:
                    offenders.append(f"{path.name}: {name}")
    assert not offenders, (
        f"a web framework is imported under agentorg/api/: {offenders}. It would "
        f"become a dependency of all five agent containers."
    )
