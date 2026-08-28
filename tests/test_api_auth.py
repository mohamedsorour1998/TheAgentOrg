"""What the credential layer refuses. K5's tests.

OWNER: Lane K.

WHY THIS FILE IS MOSTLY REFUSALS, like `tests/test_approve_server.py` before it:
an auth layer's value is entirely in what it declines. A test that a valid key
works proves the happy path and would pass identically against a function that
accepted everything -- so every acceptance test here is paired with the refusal it
is supposed to be distinguishable from.

THE ONE PROPERTY THIS FILE EXISTS FOR: an empty key store refuses. That direction
cannot be checked by inspection, because both directions read as reasonable code,
and getting it wrong is invisible from inside a running deployment -- every
request succeeds. So it is asserted first and asserted from the transport as well
as from the function, since a route that forgot to call `resolve` would leave the
function correct and the API open.
"""

import secrets
import threading
import urllib.error
import urllib.request

import pytest

from agentorg import queue
from agentorg.api import auth, idempotency, service
from agentorg.api import server as api_server
from agentorg.api.errors import Unauthenticated

# Every scope the API grants. Read from the module rather than restated, so a new
# scope is covered by the parametrised tests without anybody remembering this file.
assert auth.SCOPES, "auth.SCOPES is empty; the scope tests would pin nothing"


@pytest.fixture(autouse=True)
def _clean_substrate():
    """Fresh stores on both sides of every test. See conftest.py guard 5."""
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
    """A real socket, shut down afterwards.

    A REAL SERVER RATHER THAN A CALL INTO THE HANDLER, because the property under
    test is that a ROUTE authenticates -- and a handler invoked directly would
    prove only that `resolve` works, which the function-level tests already do. A
    route that forgot to call it is exactly the defect this fixture can see.
    """
    server = api_server.serve(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def _get(base, path, headers=None):
    """A GET returning `(status, body_bytes)`. 4xx is an answer, not an error."""
    request = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read()


# ──────────────────────────────────────────────────────────────────────────────
# THE DIRECTION THAT MATTERS
# ──────────────────────────────────────────────────────────────────────────────

def test_an_empty_key_store_refuses_a_well_formed_key():
    """"Nobody provisioned a key" must not read as "this caller may do anything".

    The failure this prevents is invisible from inside a deployment: every request
    succeeds, and the first signal is somebody else's run in your tenant. Same
    direction as `budgets.check` refusing a tenant with no budget row.
    """
    assert not auth.key_store().keys, "the store should start empty"
    with pytest.raises(Unauthenticated):
        auth.resolve("Bearer agtk_" + "a" * 32 + "_plausible-looking-secret")


@pytest.mark.parametrize(
    "path",
    ["/v1/runs/some-run", "/v1/repositories/acme%2Fauth/config"],
)
def test_a_get_route_refuses_when_no_key_is_provisioned(live_server, path):
    """The same property from the transport, which is where it can be lost.

    `resolve` refusing is necessary and not sufficient: a route that never called
    it would leave every function-level test green and the API open. This drives a
    real socket.
    """
    status, _ = _get(live_server, path,
                     {"Authorization": "Bearer agtk_" + "a" * 32 + "_x"})
    assert status == 401, f"{path} answered {status} with no key provisioned"


def test_health_and_openapi_are_deliberately_open(live_server):
    """The two exceptions, and they are exceptions on purpose.

    THE CONTROL FOR EVERY 401 ABOVE. Without it, a server that refused
    everything -- including a route with no credential requirement -- would pass
    all of them, and "this API refuses" would be indistinguishable from "this API
    is broken".
    """
    assert _get(live_server, "/v1/health")[0] == 200
    assert _get(live_server, "/v1/openapi.json")[0] == 200


def test_an_issued_key_verifies_and_names_its_tenant():
    """The acceptance half. Paired with the refusals so neither is vacuous."""
    key_id, key = auth.issue_key("tenant-alpha")
    credential = auth.resolve(f"Bearer {key}")
    assert credential.tenant_id == "tenant-alpha"
    assert credential.key_id == key_id
    assert credential.scopes == frozenset(auth.SCOPES)


def test_a_key_containing_the_separator_in_its_secret_still_verifies():
    """A REGRESSION TEST FOR A REAL BUG, found by issuing and resolving in one go.

    `secrets.token_urlsafe` emits base64url, whose alphabet CONTAINS `_`, so a
    plain `split("_")` returned four or five pieces for a legitimately issued key
    and refused it. A test asserting only that malformed keys are refused passes
    against that bug, because refusing everything satisfies it -- which is why
    this asserts on a key that ACTUALLY CONTAINS the separator rather than on a
    freshly issued one that might not.
    """
    key_id, _ = auth.issue_key("tenant-alpha")
    stored = auth.key_store().get(key_id)
    salt = secrets.token_bytes(16)
    secret = "has_two_underscores"
    stored.salt = salt
    stored.digest = auth.hash_secret(secret, salt)

    credential = auth.resolve(f"Bearer agtk_{key_id}_{secret}")
    assert credential.tenant_id == "tenant-alpha"
    assert "_" in secret, "this test would pin nothing without a separator in the secret"


# ──────────────────────────────────────────────────────────────────────────────
# MALFORMED, WRONG AND REVOKED
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "   ",
        "agtk_abc_def",                    # no scheme
        "Basic agtk_abc_def",              # wrong scheme
        "Bearer",                          # scheme only
        "Bearer nope_abc_def",             # wrong prefix
        "Bearer agtk_abc",                 # too few parts
        "Bearer agtk__secret",             # empty key id
        "Bearer agtk_abc_",                # empty secret
    ],
    ids=lambda h: repr(h),
)
def test_every_malformed_authorization_header_is_refused(header):
    """One refusal for every shape, and the SAME refusal.

    A message distinguishing "no header" from "wrong scheme" from "not our prefix"
    would let a caller probe the format, and the format is half of a credential.
    """
    auth.issue_key("tenant-alpha")  # a real key exists, so this is not vacuous
    with pytest.raises(Unauthenticated):
        auth.resolve(header)


def test_the_scheme_is_case_insensitive_but_the_key_is_not():
    """HTTP schemes are case-insensitive by specification; secrets are not.

    A caller sending `bearer` is not an attacker. A caller sending the key in
    another case IS presenting a different secret, and `compare_digest` over the
    scrypt output makes that a refusal for free -- asserted so a future
    "normalisation" cannot fold it away.
    """
    _, key = auth.issue_key("tenant-alpha")
    assert auth.resolve(f"bearer {key}").tenant_id == "tenant-alpha"
    prefix, key_id, secret = key.split("_", 2)
    with pytest.raises(Unauthenticated):
        auth.resolve(f"Bearer {prefix}_{key_id}_{secret.upper()}")


def test_a_wrong_secret_against_a_real_key_id_is_refused():
    """The case a length check or a prefix check would let through."""
    key_id, _ = auth.issue_key("tenant-alpha")
    with pytest.raises(Unauthenticated):
        auth.resolve(f"Bearer agtk_{key_id}_wrong-secret-same-length-ish")


def test_a_revoked_key_is_refused_and_its_row_is_kept():
    """Revocation is a flag, not a delete.

    Kept so a later question about which key made a call still has an answer --
    the instinct behind `modules/state`'s IAM omitting `DeleteItem`, because "an
    audit trail that can be pruned by the thing it audits is not one".
    """
    key_id, key = auth.issue_key("tenant-alpha")
    assert auth.resolve(f"Bearer {key}").key_id == key_id  # live before
    assert auth.key_store().revoke(key_id) is True
    with pytest.raises(Unauthenticated):
        auth.resolve(f"Bearer {key}")
    assert auth.key_store().get(key_id) is not None, "the row was deleted, not revoked"


def test_revoking_a_key_that_does_not_exist_says_so():
    """False rather than an exception, so a caller can tell the two apart.

    An operator revoking a typo'd key id must learn that nothing happened. Raising
    would be equally honest; silently returning would not.
    """
    assert auth.key_store().revoke("no-such-key") is False


# ──────────────────────────────────────────────────────────────────────────────
# WHAT IS NEVER STORED, AND WHAT IS NEVER GRANTED
# ──────────────────────────────────────────────────────────────────────────────

def test_the_store_holds_no_recoverable_secret():
    """A store dump cannot yield a live credential.

    Asserted by SEARCHING the stored row's values for the secret, rather than by
    checking that a field named `secret` is absent -- a field named something else
    holding the same bytes would satisfy the weaker check. This is the same
    instinct as `tests/test_tenancy_secrets.py` grepping the module's own logs for
    the plaintext.
    """
    _, key = auth.issue_key("tenant-alpha")
    secret = key.split("_", 2)[2]
    stored = auth.key_store().get(key.split("_")[1])
    haystack = repr(vars(stored)).encode()
    assert secret.encode() not in haystack, (
        "the plaintext secret is recoverable from the stored row"
    )
    assert secret, "this test would pin nothing with an empty secret"


def test_issuing_refuses_a_blank_tenant():
    """`""` is the single-tenant marker and must be translated, never bound.

    A credential scoped to a blank tenant matches a blank tenant column, which is
    a row nobody owns -- `tenancy.scope_for` refuses it for the same reason.
    """
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="may not be blank"):
            auth.issue_key(blank)


def test_issuing_refuses_an_unknown_scope():
    """Dropped silently, the key verifies and then fails on the route it was for.

    And the refusal names the gate case explicitly, because that is the scope
    somebody will try to add.
    """
    with pytest.raises(ValueError, match="unknown scope"):
        auth.issue_key("tenant-alpha", ["gates:approve"])
    with pytest.raises(ValueError, match="unknown scope"):
        auth.issue_key("tenant-alpha", ["runs:write", "typo:scope"])


@pytest.mark.parametrize("scope", auth.SCOPES)
def test_a_credential_without_a_scope_is_refused_for_it(scope):
    """`require` refuses, and refuses as 401 rather than 403.

    A token without the scope has not established a right to be told what it is
    missing; "you need runs:write" is a map of the API for anybody holding a
    low-privilege key.
    """
    others = [s for s in auth.SCOPES if s != scope]
    _, key = auth.issue_key("tenant-alpha", others)
    credential = auth.resolve(f"Bearer {key}")
    with pytest.raises(Unauthenticated):
        credential.require(scope)
    for held in others:
        credential.require(held)  # must not raise


def test_the_credential_is_frozen_so_a_handler_cannot_widen_its_own_scopes():
    """A mutable credential is a scope check a later line can undo."""
    _, key = auth.issue_key("tenant-alpha", ["runs:read"])
    credential = auth.resolve(f"Bearer {key}")
    with pytest.raises((AttributeError, TypeError)):
        credential.scopes = frozenset(auth.SCOPES)
    with pytest.raises((AttributeError, TypeError)):
        credential.tenant_id = "tenant-beta"


def test_a_scoped_route_refuses_the_wrong_scope_end_to_end(live_server):
    """The scope check reaches the transport, not only the function.

    A read-only key on the submit route must be a 401 -- otherwise `require` is
    correct and unused, which is the same class of defect as a check that did not
    run.
    """
    _, key = auth.issue_key("tenant-alpha", ["runs:read"])
    request = urllib.request.Request(
        live_server + "/v1/runs",
        data=b'{"ticket_id":"7","ticket_text":"x"}',
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            pytest.fail(f"a runs:read key submitted a run: {response.status}")
    except urllib.error.HTTPError as refused:
        assert refused.code == 401


# ──────────────────────────────────────────────────────────────────────────────
# THE ORDERING INSIDE `resolve`, PINNED OVER THE AST
# ──────────────────────────────────────────────────────────────────────────────
#
# ADDED BECAUSE A RED STEP CAME BACK INERT, which is the honest reason and the
# useful one. Moving `if stored.revoked` from AFTER the digest comparison to
# BEFORE it left this file at `29 passed`, byte-identical -- so nothing here
# pinned the ordering, and CLAUDE.md's rule applies: "a RED step must be shown to
# change the output, not merely to have been applied."
#
# The ordering is real and cannot be pinned behaviourally. Both orders refuse a
# revoked key; the only difference is that the wrong one refuses it WITHOUT
# hashing, so a revoked key answers measurably faster than a live key with a wrong
# secret -- which tells a caller the key id exists. A timing assertion would be
# flaky on a loaded machine (CLAUDE.md records 116.88s -> 149.68s -> 102.83s for
# one unchanged suite), so the structure is asserted instead.
#
# Over the AST rather than the text, for this repository's most repeatable
# reason: the comment beside that check explains the ordering, so a substring
# search for "revoked" would be satisfied by the explanation while the code moved.

def test_resolve_checks_the_digest_before_it_checks_revocation():
    """The refusal order inside `resolve`, asserted structurally.

    `hash_secret` must be called before `stored.revoked` is read. Reversed, a
    revoked key is refused without the scrypt work and its response time reports
    that the key id is real -- the same class of leak the constant-time compare
    exists to close, one level up.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(auth.resolve))
    hash_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "hash_secret"
    ]
    revoked_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "revoked"
    ]
    assert hash_lines, "no hash_secret call found in resolve; this test pins nothing"
    assert revoked_lines, "no `revoked` read found in resolve; this test pins nothing"
    assert max(hash_lines) < min(revoked_lines), (
        f"`revoked` is read at line {min(revoked_lines)} before the last "
        f"hash_secret call at line {max(hash_lines)}. A revoked key would then be "
        f"refused without the scrypt work, so its response time reports that the "
        f"key id exists."
    )


def test_an_unknown_key_id_still_pays_for_a_hash():
    """The same leak on the other branch, and this one IS observable structurally.

    An early `return`/`raise` on a missing key id reads as correct code and makes
    the response time answer "does this key exist?". `resolve` hashes against a
    throwaway salt in that branch instead, so the two cost the same.
    """
    import ast
    import inspect

    source = inspect.getsource(auth.resolve)
    tree = ast.parse(source)
    # The branch body for `if stored is None:` must contain a hash_secret call.
    branches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and ast.unparse(node.test) == "stored is None"
    ]
    assert len(branches) == 1, (
        f"expected exactly one `stored is None` branch, found {len(branches)}; "
        f"this test would pin the wrong thing"
    )
    calls = [
        ast.unparse(node.func)
        for node in ast.walk(branches[0])
        if isinstance(node, ast.Call)
    ]
    assert "hash_secret" in calls, (
        "the unknown-key-id branch does not hash, so it returns faster than a "
        "wrong secret against a real key id and reports which key ids exist"
    )
