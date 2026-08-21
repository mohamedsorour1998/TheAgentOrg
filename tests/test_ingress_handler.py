"""Pins the GitHub-webhook ingress Lambda: HMAC first, EventBridge second.

Owner: Task 5 (cloud-native platform lane).

WHY THIS FILE IS A SECURITY TEST AND NOT A UNIT TEST
----------------------------------------------------
The Lambda under test sits behind a Function URL created with
`authorization_type = "NONE"`. That is deliberate -- GitHub cannot sign a SigV4
request -- and it has one consequence that governs every test here: the endpoint
is INTERNET-REACHABLE AND UNAUTHENTICATED AT THE AWS LAYER. Nothing in IAM, no
resource policy and no VPC stands between an anonymous POST and
`handler.handler`. The HMAC over the request body is the only access control
that exists, so the only thing separating this deployment from "anyone on the
internet can start a pipeline run in our account" is that the signature check
runs BEFORE any work.

"Before any work" is the property, not "returns 401". A handler that publishes
to EventBridge and *then* notices the signature was wrong returns exactly the
same 401 to the caller while having already triggered the pipeline. So every
reject-path test here asserts TWO things: the status code, and that the
EventBridge stub recorded ZERO calls.

WHY EVERY ZERO-COUNT ASSERTION IS FOLLOWED BY A REPLAY
------------------------------------------------------
`assert stub.calls == []` passes for two completely different reasons: the
handler correctly refused to publish, or the stub was never wired to the handler
at all and could not have recorded anything. Those are indistinguishable from
the assertion's point of view, and this repository has already shipped
nineteen assertions that turned out to pin nothing for exactly this shape of
reason.

So `_assert_the_stub_would_have_recorded` replays a VALID delivery through the
same handler and the SAME stub instance and requires it to record one call. A
zero-count assertion is only meaningful next to a proof that the counter works.

HOW THE HANDLER IS IMPORTED
---------------------------
`infra/ingress/handler.py` is not part of the `agentorg` package and must not
become part of it: `tests/test_agentcore_deploy_assets.py` AST-walks every
`agentorg/**/*.py` and fails on a third-party top-level import that is absent
from `agentorg/agents/requirements.txt`. This handler imports `boto3`, which the
Lambda Python runtime provides and which therefore must NOT be added to that
file. Keeping the handler under `infra/` keeps it outside that walk, so it is
loaded here by file path rather than by package import.

THE THREE SEAMS, AND WHY THEY FAIL LOUDLY BY DEFAULT
----------------------------------------------------
Same pattern as tests/conftest.py. `handler._events()` and `handler._secrets()`
are the only places a boto3 client is constructed, and the autouse fixture below
replaces both with `pytest.fail` raisers. On a machine with AWS credentials an
unstubbed test would otherwise make a real `PutEvents` (billable, and it would
fire the real pipeline) and a real `GetSecretValue`.

`pytest.fail` is load-bearing rather than decorative: `Failed` derives from
BaseException, and `handler.handler` wraps its publish in a blind
`except Exception` so that an unexpected AWS error becomes a 500 instead of a
Lambda 502. An ordinary raiser in these seams would be swallowed by that
`except` and reported as a tidy 500 -- the test would pass green while the live
call went out.

The third seam is the module-level secret cache. Lambda reuses a warm container,
so the handler caches the fetched secret; a cache that survives between tests is
the "a stale hit looks exactly like a fetch" defect, so the fixture clears it on
BOTH sides of every test.

EVERY SECRET LITERAL IN THIS FILE IS FAKE. Nothing here reads `.env`.
"""

import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from typing import NoReturn

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLER_PATH = REPO_ROOT / "infra" / "ingress" / "handler.py"


def _load_handler():
    """Import infra/ingress/handler.py by path. See the module docstring."""
    assert HANDLER_PATH.is_file(), f"handler not found at {HANDLER_PATH}"
    spec = importlib.util.spec_from_file_location("ingress_handler", HANDLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


handler = _load_handler()

# ── fakes ─────────────────────────────────────────────────────────────────────
# Not credentials. The webhook secret below is a literal invented for this file;
# the ARN is the documented account/region with an obviously fake secret name.

FAKE_SECRET = "fake-webhook-secret-for-tests-only"
FAKE_OTHER_SECRET = "a-different-fake-secret-entirely"
FAKE_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:339712964409:secret:FAKE-EXAMPLE-000000"
)
FAKE_BUS = "theagentorg-shared-github-ingress"
FAKE_SOURCE = "github.webhook"

# A body whose bytes a JSON round-trip would NOT reproduce: two spaces after the
# colon, a trailing newline and non-alphabetical key order. Used to pin that the
# HMAC is taken over the raw octets.
RAW_QUIRKY_BODY = b'{"zeta":  1, "action": "opened", "alpha": 2}\n'

SIMPLE_BODY = b'{"action": "opened", "issue": {"number": 7, "title": "hi"}}'


def _sign(secret: str, body: bytes) -> str:
    """Sign exactly the way GitHub does. Computed here, not borrowed from the
    handler -- a helper shared with the code under test would mirror its bugs."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def _event(
    *,
    body: bytes | None = SIMPLE_BODY,
    signature: str | None = None,
    method: str = "POST",
    event_name: str | None = "issues",
    delivery: str = "fake-delivery-0000",
    is_base64: bool = False,
    signature_header: str = "x-hub-signature-256",
    extra_headers: dict | None = None,
) -> dict:
    """Build a Lambda Function URL (payload format 2.0) event."""
    headers: dict[str, str] = {"content-type": "application/json"}
    if event_name is not None:
        headers["x-github-event"] = event_name
    headers["x-github-delivery"] = delivery
    if signature is not None:
        headers[signature_header] = signature
    if extra_headers:
        headers.update(extra_headers)

    event: dict = {
        "version": "2.0",
        "rawPath": "/",
        "headers": headers,
        "requestContext": {"http": {"method": method, "path": "/"}},
        "isBase64Encoded": is_base64,
    }
    if body is not None:
        event["body"] = (
            base64.b64encode(body).decode("ascii") if is_base64 else body.decode("utf-8")
        )
    return event


class _EventsStub:
    """Records every PutEvents attempt. Recording happens BEFORE the configured
    failure, so `raises` still counts as an attempt -- an attempted publish on a
    reject path is the defect, whether or not it succeeded."""

    def __init__(self, failed: int = 0, raises: BaseException | None = None):
        self.calls: list[dict] = []
        self._failed = failed
        self._raises = raises

    def put_events(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return {
            "FailedEntryCount": self._failed,
            "Entries": [{"EventId": "fake-event-id"} if not self._failed else
                        {"ErrorCode": "InternalException", "ErrorMessage": "fake"}],
        }


class _SecretsStub:
    def __init__(self, secret_string: str = FAKE_SECRET, raises: BaseException | None = None):
        self.calls: list[dict] = []
        self._secret_string = secret_string
        self._raises = raises

    def get_secret_value(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return {"SecretString": self._secret_string}


def _unpatched_events() -> NoReturn:
    pytest.fail(
        "This test reached the real handler._events(), which constructs a live "
        "EventBridge client. With AWS credentials present that is a real, "
        "billable PutEvents against the shared bus -- and it would trigger the "
        "pipeline. Wire the stub with the `wired` fixture.",
        pytrace=False,
    )


def _unpatched_secrets() -> NoReturn:
    pytest.fail(
        "This test reached the real handler._secrets(), which constructs a live "
        "Secrets Manager client and would read the real webhook secret. Wire the "
        "stub with the `wired` fixture.",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _no_live_aws_and_no_cached_secret(monkeypatch):
    """Block both AWS seams, supply the Lambda's env, clear the secret cache."""
    handler._reset_caches()
    monkeypatch.setenv("WEBHOOK_SECRET_ARN", FAKE_SECRET_ARN)
    monkeypatch.setenv("EVENT_BUS_NAME", FAKE_BUS)
    monkeypatch.setenv("EVENT_SOURCE", FAKE_SOURCE)
    monkeypatch.setattr(handler, "_events", _unpatched_events)
    monkeypatch.setattr(handler, "_secrets", _unpatched_secrets)
    yield
    handler._reset_caches()


@pytest.fixture()
def wired(monkeypatch):
    """Replace both seams with recording stubs. Returns (events, secrets)."""

    def _wire(secret_string: str = FAKE_SECRET, events=None, secrets=None):
        events_stub = _EventsStub() if events is None else events
        secrets_stub = _SecretsStub(secret_string) if secrets is None else secrets
        monkeypatch.setattr(handler, "_events", lambda: events_stub)
        monkeypatch.setattr(handler, "_secrets", lambda: secrets_stub)
        return events_stub, secrets_stub

    return _wire


def _assert_the_stub_would_have_recorded(events_stub, secret: str = FAKE_SECRET) -> None:
    """Prove a zero-count assertion was not vacuous.

    Replays a VALID delivery through the same handler and the same stub object.
    If the stub is not actually wired to the handler, or cannot record, this
    fails -- and the `== 0` assertion it accompanies proved nothing.
    """
    before = len(events_stub.calls)
    body = b'{"action": "opened", "issue": {"number": 4242}}'
    response = handler.handler(_event(body=body, signature=_sign(secret, body)), None)

    assert response["statusCode"] == 202, (
        "the control replay of a VALID delivery did not return 202, so the "
        "zero-PutEvents assertion beside this call cannot be trusted: it may be "
        "zero because nothing works, not because the reject path refused to "
        f"publish. got {response!r}"
    )
    assert len(events_stub.calls) == before + 1, (
        "the EventBridge stub did not record a call even for a VALID delivery, "
        "so it could not have recorded one on the reject path either. The "
        "accompanying 'zero PutEvents' assertion is vacuous. "
        f"calls before={before}, after={len(events_stub.calls)}"
    )


def _assert_the_stub_could_still_have_recorded(events_stub) -> None:
    """Vacuity control for tests whose SECRET stub is deliberately broken.

    `_assert_the_stub_would_have_recorded` replays a valid delivery, which is
    impossible when the secret is unreadable or misspelled -- no correct
    signature exists to send. So instead this proves the events stub passed to
    the handler is a LIVE recorder by invoking it directly and then restoring its
    ledger.

    Weaker than the replay, and deliberately so: it shows the stub can record,
    not that the handler is wired to it. It is used only where the replay is
    impossible by construction, and the tests that use it say so.
    """
    before = list(events_stub.calls)
    events_stub.put_events(Entries=[{"Detail": "{}"}])

    assert len(events_stub.calls) == len(before) + 1, (
        "the EventBridge stub does not record calls at all, so the "
        "'zero PutEvents' assertion beside this one is vacuous."
    )
    events_stub.calls[:] = before


# ── the happy path ────────────────────────────────────────────────────────────


def test_a_valid_signature_returns_202_and_publishes_exactly_one_event(wired):
    events, secrets = wired()

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=_sign(FAKE_SECRET, SIMPLE_BODY)), None
    )

    assert response["statusCode"] == 202, response
    assert len(events.calls) == 1, (
        f"expected exactly one PutEvents, got {len(events.calls)}: {events.calls}"
    )
    assert secrets.calls == [{"SecretId": FAKE_SECRET_ARN}], (
        f"the secret was not read from the ARN in the environment: {secrets.calls}"
    )

    entries = events.calls[0]["Entries"]
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry["EventBusName"] == FAKE_BUS, entry
    assert entry["Source"] == FAKE_SOURCE, entry
    assert json.loads(entry["Detail"]) == json.loads(SIMPLE_BODY), entry


def test_the_detail_is_the_raw_body_verbatim_not_a_reserialised_copy(wired):
    """The event on the bus carries GitHub's bytes, not our idea of them."""
    events, _ = wired()

    handler.handler(
        _event(body=RAW_QUIRKY_BODY, signature=_sign(FAKE_SECRET, RAW_QUIRKY_BODY)),
        None,
    )

    assert len(events.calls) == 1, events.calls
    detail = events.calls[0]["Entries"][0]["Detail"]
    assert detail == RAW_QUIRKY_BODY.decode("utf-8"), (
        "Detail was re-serialised rather than forwarded. Round-tripping it here "
        "is not a security bug on its own, but it is the same reflex that breaks "
        f"the HMAC one line earlier. got {detail!r}"
    )


def test_the_detail_type_is_githubs_event_name_verbatim(wired):
    """CROSS-FILE CONTRACT. The EventBridge rule in
    infra/Terraform/modules/ingress/main.tf matches `detail-type: ["issues"]`,
    which is GitHub's documented event name for the Issues subscription. That
    only matches if the handler forwards the `x-github-event` header unchanged.
    Invent a detail-type here and the rule silently stops matching -- the bus
    accepts the event, no rule fires, and nothing anywhere turns red."""
    events, _ = wired()

    handler.handler(
        _event(
            body=SIMPLE_BODY,
            signature=_sign(FAKE_SECRET, SIMPLE_BODY),
            event_name="issues",
        ),
        None,
    )

    assert len(events.calls) == 1, events.calls
    assert events.calls[0]["Entries"][0]["DetailType"] == "issues", (
        "DetailType is not GitHub's event name. The Terraform rule matches "
        '`detail-type: ["issues"]`; anything else means matched-nothing. '
        f"got {events.calls[0]['Entries'][0]!r}"
    )


def test_the_hmac_is_computed_over_the_raw_body_not_a_json_round_trip(wired):
    """The paid-for trap, pinned directly.

    `json.dumps(json.loads(body))` normalises whitespace and so changes the
    digest for any body GitHub did not happen to serialise identically. The
    symptom is every request 401ing, which reads as a wrong secret.
    """
    events, _ = wired()
    assert json.dumps(json.loads(RAW_QUIRKY_BODY)).encode() != RAW_QUIRKY_BODY, (
        "this test's fixture body survives a JSON round-trip unchanged, so it "
        "cannot detect a handler that round-trips before signing. Fix the body."
    )

    response = handler.handler(
        _event(body=RAW_QUIRKY_BODY, signature=_sign(FAKE_SECRET, RAW_QUIRKY_BODY)),
        None,
    )

    assert response["statusCode"] == 202, (
        "a correctly signed body with non-canonical JSON whitespace was "
        f"rejected -- the handler is not hashing the raw bytes. got {response!r}"
    )
    assert len(events.calls) == 1, events.calls


def test_a_base64_encoded_body_is_decoded_before_the_hmac(wired):
    """A Function URL sets isBase64Encoded for a body it does not treat as text.

    GitHub signs the octets it sent. If the handler hashes the base64 TEXT it
    received instead, every such delivery 401s -- again looking like a bad
    secret rather than an encoding bug.
    """
    events, _ = wired()

    response = handler.handler(
        _event(
            body=SIMPLE_BODY,
            signature=_sign(FAKE_SECRET, SIMPLE_BODY),
            is_base64=True,
        ),
        None,
    )

    assert response["statusCode"] == 202, (
        f"a base64-encoded delivery was rejected: {response!r}"
    )
    assert len(events.calls) == 1, events.calls
    assert json.loads(events.calls[0]["Entries"][0]["Detail"]) == json.loads(SIMPLE_BODY)


@pytest.mark.parametrize(
    "header_name",
    ["x-hub-signature-256", "X-Hub-Signature-256", "X-HUB-SIGNATURE-256"],
)
def test_the_signature_header_is_found_whatever_its_case(wired, header_name):
    """Function URLs lower-case header names -- but relying on that silently is
    how a handler works in one integration and 401s in another."""
    events, _ = wired()

    response = handler.handler(
        _event(
            body=SIMPLE_BODY,
            signature=_sign(FAKE_SECRET, SIMPLE_BODY),
            signature_header=header_name,
        ),
        None,
    )

    assert response["statusCode"] == 202, f"{header_name}: {response!r}"
    assert len(events.calls) == 1, events.calls


@pytest.mark.parametrize(
    ("secret_string", "expected_secret"),
    [
        pytest.param(FAKE_SECRET, FAKE_SECRET, id="plain-string"),
        pytest.param(
            json.dumps({"webhook_secret": FAKE_SECRET}), FAKE_SECRET, id="json-object"
        ),
    ],
)
def test_the_secret_is_accepted_as_a_plain_string_or_a_json_object(
    wired, secret_string, expected_secret
):
    """Both shapes exist in the wild: `put-secret-value --secret-string x` gives
    the first, the console's key/value editor gives the second. Guessing wrong
    401s every request for a reason that looks like a wrong secret."""
    events, _ = wired(secret_string=secret_string)

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=_sign(expected_secret, SIMPLE_BODY)), None
    )

    assert response["statusCode"] == 202, response
    assert len(events.calls) == 1, events.calls


def test_the_secret_is_fetched_once_and_reused_across_invocations(wired):
    """Warm containers must not pay a Secrets Manager call per delivery."""
    events, secrets = wired()

    for _ in range(3):
        handler.handler(
            _event(body=SIMPLE_BODY, signature=_sign(FAKE_SECRET, SIMPLE_BODY)), None
        )

    assert len(events.calls) == 3, events.calls
    assert len(secrets.calls) == 1, (
        f"expected one GetSecretValue across three deliveries, got {secrets.calls}"
    )


# ── the reject paths: 4xx AND zero PutEvents ──────────────────────────────────


def test_a_wrong_signature_returns_401_and_publishes_nothing(wired):
    events, _ = wired()
    signed_with_the_wrong_secret = _sign(FAKE_OTHER_SECRET, SIMPLE_BODY)

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=signed_with_the_wrong_secret), None
    )

    assert response["statusCode"] == 401, response
    assert events.calls == [], (
        "the handler published to EventBridge for a delivery it then rejected "
        "with 401. The caller sees a refusal; the pipeline has already been "
        f"triggered. calls={events.calls}"
    )
    _assert_the_stub_would_have_recorded(events)


def test_a_missing_signature_header_returns_401_and_publishes_nothing(wired):
    events, _ = wired()

    response = handler.handler(_event(body=SIMPLE_BODY, signature=None), None)

    assert response["statusCode"] == 401, response
    assert events.calls == [], f"published an unsigned delivery: {events.calls}"
    _assert_the_stub_would_have_recorded(events)


def test_an_unsigned_delivery_never_even_reads_the_secret(wired):
    """Anonymous traffic must not be able to drive Secrets Manager calls. The
    endpoint is public, so this is a cost and throttling surface too."""
    _, secrets = wired()

    response = handler.handler(_event(body=SIMPLE_BODY, signature=None), None)

    assert response["statusCode"] == 401, response
    assert secrets.calls == [], (
        f"a request with no signature header still read the secret: {secrets.calls}"
    )


def test_a_body_tampered_after_signing_is_rejected_and_publishes_nothing(wired):
    """The forgery that matters: a signature that is genuinely well-formed and
    genuinely ours, replayed over a different payload."""
    events, _ = wired()
    signature_for_a_different_body = _sign(FAKE_SECRET, SIMPLE_BODY)
    tampered = b'{"action": "opened", "issue": {"number": 7, "title": "PWNED"}}'

    response = handler.handler(
        _event(body=tampered, signature=signature_for_a_different_body), None
    )

    assert response["statusCode"] == 401, response
    assert events.calls == [], f"published a tampered body: {events.calls}"
    _assert_the_stub_would_have_recorded(events)


def test_a_signature_without_the_sha256_prefix_is_rejected(wired):
    """GitHub sends `sha256=<hex>`. A handler comparing bare hex against a
    prefixed expectation, or vice versa, still 401s -- but one that STRIPS the
    prefix before comparing has widened what it accepts for no reason."""
    events, _ = wired()
    bare_hex = _sign(FAKE_SECRET, SIMPLE_BODY).removeprefix("sha256=")
    assert "=" not in bare_hex, bare_hex

    response = handler.handler(_event(body=SIMPLE_BODY, signature=bare_hex), None)

    assert response["statusCode"] == 401, response
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


@pytest.mark.parametrize("signature", ["", "sha256=", "sha256=zzzz", "garbage"])
def test_a_malformed_signature_is_rejected_and_publishes_nothing(wired, signature):
    events, _ = wired()

    response = handler.handler(_event(body=SIMPLE_BODY, signature=signature), None)

    assert response["statusCode"] == 401, f"{signature!r}: {response!r}"
    assert events.calls == [], f"{signature!r}: {events.calls}"
    _assert_the_stub_would_have_recorded(events)


def test_a_signature_containing_non_ascii_is_rejected_not_a_crash(wired):
    """`hmac.compare_digest` raises TypeError on a non-ASCII str, so a handler
    comparing strings turns a hostile header into a 502 rather than a 401."""
    events, _ = wired()

    response = handler.handler(_event(body=SIMPLE_BODY, signature="sha256=é中"), None)

    assert response["statusCode"] == 401, response
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


def test_a_delivery_with_no_body_at_all_is_rejected_and_publishes_nothing(wired):
    events, _ = wired()

    response = handler.handler(
        _event(body=None, signature=_sign(FAKE_SECRET, b"")), None
    )

    assert response["statusCode"] == 401, response
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


def test_a_non_post_method_is_rejected_without_publishing(wired):
    """The URL is public, so it is crawled. A GET must not reach the HMAC path
    or the pipeline, and must not be answered with a 401 that reads as a
    configuration problem."""
    events, _ = wired()

    response = handler.handler(
        _event(
            body=SIMPLE_BODY,
            signature=_sign(FAKE_SECRET, SIMPLE_BODY),
            method="GET",
        ),
        None,
    )

    assert response["statusCode"] == 405, response
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


def test_the_signature_is_compared_with_compare_digest_and_not_equality(
    wired, monkeypatch
):
    """A behavioural pin, not a grep.

    `==` and `hmac.compare_digest` agree on every result and differ only in
    timing, so no black-box assertion can tell them apart. Instead this spies on
    the function the handler is required to use: swap it for `==` and the spy is
    never called, and this test fails by name. The spy delegates to the real
    implementation, so the handler's behaviour is unchanged while observed.
    """
    events, _ = wired()
    real_compare = hmac.compare_digest
    seen: list[tuple] = []

    def spy(a, b):
        seen.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(handler.hmac, "compare_digest", spy)

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=_sign(FAKE_OTHER_SECRET, SIMPLE_BODY)), None
    )

    assert response["statusCode"] == 401, response
    assert seen, (
        "the handler rejected the signature without calling "
        "hmac.compare_digest. A plain `==` leaks the position of the first "
        "differing byte through timing, which is the whole reason "
        "compare_digest exists."
    )
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in seen), (
        "compare_digest was called with str arguments. That raises TypeError on "
        f"any non-ASCII header value instead of returning False. saw {seen!r}"
    )
    assert events.calls == [], events.calls
    # Restore only the spy -- `monkeypatch.undo()` would also revert the autouse
    # fixture's env vars and the stub wiring, breaking the replay it enables.
    monkeypatch.setattr(handler.hmac, "compare_digest", real_compare)
    _assert_the_stub_would_have_recorded(events)


# ── failures that must not look like success ───────────────────────────────────


def test_a_nonzero_failed_entry_count_becomes_a_500_not_a_202(wired):
    """PutEvents answers HTTP 200 while refusing an entry. Reading only the HTTP
    status turns a dropped event into a delivery GitHub shows as succeeded --
    the run never starts and nothing anywhere says why."""
    events, _ = wired(events=_EventsStub(failed=1))

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=_sign(FAKE_SECRET, SIMPLE_BODY)), None
    )

    assert len(events.calls) == 1, events.calls
    assert response["statusCode"] == 500, (
        "PutEvents reported FailedEntryCount=1 and the handler still answered "
        f"202. A dropped event now reads as an accepted one. got {response!r}"
    )


def test_a_publish_that_raises_becomes_a_500_not_a_202(wired):
    events, _ = wired(events=_EventsStub(raises=RuntimeError("fake throttling")))

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=_sign(FAKE_SECRET, SIMPLE_BODY)), None
    )

    assert len(events.calls) == 1, events.calls
    assert response["statusCode"] == 500, response


def test_an_unreadable_secret_is_a_500_and_never_a_401(wired):
    """Until a human writes the secret version, GetSecretValue raises. A 401
    there says "your signature is wrong" to someone whose signature is perfect,
    and sends them hunting the secret they just set. 500 says "we are broken",
    which is true."""
    events, _ = wired(
        secrets=_SecretsStub(raises=RuntimeError("fake ResourceNotFoundException"))
    )

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=_sign(FAKE_SECRET, SIMPLE_BODY)), None
    )

    assert response["statusCode"] == 500, (
        "an unreadable webhook secret was reported as 401. That is "
        '"denied" standing in for "not configured yet". got '
        f"{response!r}"
    )
    assert events.calls == [], events.calls
    # The replay cannot run here: the secret is unreadable, so no valid signature
    # exists to send. Prove the recorder is live instead.
    _assert_the_stub_could_still_have_recorded(events)


def test_a_json_secret_without_the_expected_key_is_a_500_and_never_a_401(wired):
    """A JSON secret whose key is misspelled would otherwise be used verbatim as
    the HMAC key -- every delivery 401s and the secret looks wrong."""
    events, _ = wired(secret_string=json.dumps({"webhookSecret": FAKE_SECRET}))

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=_sign(FAKE_SECRET, SIMPLE_BODY)), None
    )

    assert response["statusCode"] == 500, (
        "a JSON secret with no `webhook_secret` key was silently used as the "
        f"key itself, so every delivery 401s. got {response!r}"
    )
    assert events.calls == [], events.calls
    # As above: the handler refuses this secret outright, so there is no valid
    # signature to replay.
    _assert_the_stub_could_still_have_recorded(events)


def test_an_undecodable_base64_body_is_rejected_and_publishes_nothing(wired):
    events, _ = wired()
    event = _event(body=SIMPLE_BODY, signature=_sign(FAKE_SECRET, SIMPLE_BODY))
    event["isBase64Encoded"] = True
    event["body"] = "!!! not base64 !!!"

    response = handler.handler(event, None)

    assert response["statusCode"] in (400, 401), response
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


def test_a_verified_body_that_is_not_json_is_rejected_before_publishing(wired):
    """A GitHub App set to `application/x-www-form-urlencoded` sends a correctly
    signed body that PutEvents cannot accept as Detail. Catching it here names
    the real cause instead of surfacing an EventBridge validation error."""
    events, _ = wired()
    form_body = b"payload=%7B%22action%22%3A%22opened%22%7D"

    response = handler.handler(
        _event(body=form_body, signature=_sign(FAKE_SECRET, form_body)), None
    )

    assert response["statusCode"] == 400, response
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


# ── the response must not become an oracle ────────────────────────────────────


@pytest.mark.parametrize(
    "case",
    ["valid", "wrong-signature", "missing-header"],
)
def test_no_response_ever_echoes_the_secret_or_the_expected_signature(wired, case):
    """The endpoint is public and unauthenticated, so its response body is
    readable by anyone. It must not hand back the key or the digest it wanted."""
    wired()
    signature = {
        "valid": _sign(FAKE_SECRET, SIMPLE_BODY),
        "wrong-signature": _sign(FAKE_OTHER_SECRET, SIMPLE_BODY),
        "missing-header": None,
    }[case]

    response = handler.handler(_event(body=SIMPLE_BODY, signature=signature), None)

    rendered = json.dumps(response)
    assert FAKE_SECRET not in rendered, f"{case}: the response echoed the secret"
    assert _sign(FAKE_SECRET, SIMPLE_BODY) not in rendered, (
        f"{case}: the response echoed the expected signature, which lets a "
        "caller obtain a valid signature for a body of their choosing"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX ROUND 1: the digest comparison's EXACTNESS, and the empty-secret guard.
#
# A reviewer defeated the original suite with four mutations that every gate
# passed. All four were reproduced independently before these tests were written,
# and the probes below record what each one actually let through -- because two of
# the four had a different real consequence than first diagnosed:
#
#   TRUNCATION  comparing only `sha256=` + 8 hex chars. Measured: a signature
#               carrying 8 correct hex chars and 56 WRONG ones returns 202. That
#               reduces forgery from 2^256 to ~2^32. A real break, and nothing in
#               the suite varied signature LENGTH against a valid body.
#   CASE-FOLD   `.lower()` on both operands. Measured: correct-hex-uppercased goes
#               401 -> 202, and nothing else changes; every wrong digest is still
#               refused. So it is a WIDENING, not a break -- an attacker still
#               needs the full correct digest. Pinned anyway: GitHub sends
#               lowercase hex, so accepting other cases is latitude nobody asked
#               for, and the next edit in that direction may not be so harmless.
#   PREFIX `in` `startswith` -> `in` on the prefix gate. Measured: it forges
#               nothing, but `XXsha256=<correct digest>` now reaches Secrets
#               Manager, where before it was refused at the gate with ZERO
#               GetSecretValue calls. The property it breaks is therefore the
#               anonymous-cost one, not authentication.
#   EMPTY KEY   deleting `if not secret: raise`. Measured: with an empty secret
#               version, a signature computed with an EMPTY key returns 202 and
#               publishes -- forgeable by anyone who knows the secret is empty,
#               with no secret material at all. Baseline correctly answers 500.
# ─────────────────────────────────────────────────────────────────────────────


def _valid_hex(secret: str, body: bytes) -> str:
    """The correct digest, no prefix -- so tests can mutate it structurally."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.mark.parametrize(
    ("label", "keep"),
    [
        pytest.param("prefix + 8 hex", 8, id="8-hex"),
        pytest.param("prefix + 16 hex", 16, id="16-hex"),
        pytest.param("prefix + 32 hex", 32, id="32-hex"),
        pytest.param("prefix + 63 hex (one short)", 63, id="63-hex"),
    ],
)
def test_a_truncated_signature_is_rejected_however_correct_its_prefix(
    wired, label, keep
):
    """THE SERIOUS ONE. A prefix-only comparison reduces forgery to brute force.

    Every signature here is a genuine PREFIX of the correct digest, against a
    body whose signature is otherwise valid -- so a handler comparing any leading
    slice accepts them all. At 8 hex characters that is ~32 bits, which is
    online-guessable against an endpoint that anyone can reach.
    """
    events, _ = wired()
    truncated = "sha256=" + _valid_hex(FAKE_SECRET, SIMPLE_BODY)[:keep]

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=truncated), None
    )

    assert response["statusCode"] == 401, (
        f"{label}: a truncated signature was ACCEPTED. The comparison is not "
        "over the full digest, so an attacker needs only the leading "
        f"{keep} hex characters -- roughly {keep * 4} bits. got {response!r}"
    )
    assert events.calls == [], f"{label}: published on a truncated signature"
    _assert_the_stub_would_have_recorded(events)


def test_a_signature_with_a_correct_prefix_and_wrong_tail_is_rejected(wired):
    """Full LENGTH, wrong content past the first 8 characters.

    Separate from the truncation cases because it defeats a different bad
    implementation: one that compares `len` correctly but only the first N bytes.
    This is the exact shape the reviewer's `[:15]` mutation accepted.
    """
    events, _ = wired()
    good = _valid_hex(FAKE_SECRET, SIMPLE_BODY)
    forged = "sha256=" + good[:8] + ("0" * (len(good) - 8))
    assert len(forged) == len("sha256=" + good), "the forgery must match in length"
    assert forged != "sha256=" + good, "the forgery must differ from the real digest"

    response = handler.handler(_event(body=SIMPLE_BODY, signature=forged), None)

    assert response["statusCode"] == 401, (
        "a signature sharing only its first 8 hex characters with the real "
        f"digest was accepted: {response!r}"
    )
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


def test_a_signature_longer_than_the_real_digest_is_rejected(wired):
    """The other side of the length axis: correct digest plus trailing junk."""
    events, _ = wired()
    padded = "sha256=" + _valid_hex(FAKE_SECRET, SIMPLE_BODY) + "deadbeef"

    response = handler.handler(_event(body=SIMPLE_BODY, signature=padded), None)

    assert response["statusCode"] == 401, (
        f"a signature with trailing junk after a correct digest was accepted: {response!r}"
    )
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


@pytest.mark.parametrize(
    ("label", "transform"),
    [
        pytest.param("hex UPPERCASED", lambda h: h.upper(), id="upper-hex"),
        pytest.param("hex MiXeD case", lambda h: h[:32].upper() + h[32:], id="mixed-hex"),
    ],
)
def test_the_hex_digest_is_compared_case_sensitively(wired, label, transform):
    """A WIDENING rather than a break -- and pinned for that reason, not despite it.

    Measured: case-folding both operands changes exactly one outcome, correct-hex
    uppercased going 401 -> 202. Every wrong digest is still refused, so an
    attacker gains nothing without the true digest. But GitHub documents
    lowercase hex, so anything else is latitude the protocol never asked for, and
    an unpinned comparison invites the next edit to relax something that does
    matter. This test says which direction is intended.
    """
    events, _ = wired()
    recased = "sha256=" + transform(_valid_hex(FAKE_SECRET, SIMPLE_BODY))

    response = handler.handler(_event(body=SIMPLE_BODY, signature=recased), None)

    assert response["statusCode"] == 401, (
        f"{label}: accepted. GitHub sends a lowercase hex digest; comparing "
        "case-insensitively widens what counts as a valid signature beyond the "
        f"protocol. got {response!r}"
    )
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


@pytest.mark.parametrize(
    ("label", "signature_for"),
    [
        pytest.param("prefix buried after junk", lambda h: "XX" + "sha256=" + h, id="leading-junk"),
        pytest.param("uppercase prefix", lambda h: "SHA256=" + h, id="upper-prefix"),
        pytest.param("sha1 prefix", lambda h: "sha1=" + h, id="sha1-prefix"),
    ],
)
def test_the_prefix_gate_requires_the_prefix_at_the_START(wired, label, signature_for):
    """`startswith`, never `in`.

    What the `in` variant actually costs is NOT authentication -- measured, it
    forges nothing. It is that `XXsha256=<correct digest>` stops being refused at
    the gate and instead reaches `_webhook_secret()`, so an anonymous caller can
    drive a Secrets Manager call against a public endpoint. That is the same
    property `test_an_unsigned_delivery_never_even_reads_the_secret` protects,
    which could not see this shape because its input had no prefix anywhere.
    """
    events, secrets = wired()
    signature = signature_for(_valid_hex(FAKE_SECRET, SIMPLE_BODY))

    response = handler.handler(_event(body=SIMPLE_BODY, signature=signature), None)

    assert response["statusCode"] == 401, f"{label}: {response!r}"
    assert events.calls == [], f"{label}: {events.calls}"
    assert secrets.calls == [], (
        f"{label}: a malformed signature header reached Secrets Manager. The "
        "prefix gate must refuse before the secret is read, or anonymous "
        f"traffic can drive GetSecretValue on a public endpoint. {secrets.calls}"
    )
    _assert_the_stub_would_have_recorded(events)


def test_a_doubled_prefix_is_rejected_at_the_comparison_not_at_the_gate(wired):
    """`sha256=sha256=<hex>` genuinely STARTS WITH the prefix, so it passes the
    structural gate and is refused by the digest comparison instead.

    Written as its own test rather than folded into the prefix-gate cases because
    the expectation is different, and pretending otherwise would be asserting a
    property the handler does not have: this input DOES legitimately reach
    Secrets Manager. What matters is that it is refused and publishes nothing.
    """
    events, _ = wired()
    signature = "sha256=sha256=" + _valid_hex(FAKE_SECRET, SIMPLE_BODY)

    response = handler.handler(_event(body=SIMPLE_BODY, signature=signature), None)

    assert response["statusCode"] == 401, response
    assert events.calls == [], events.calls
    _assert_the_stub_would_have_recorded(events)


@pytest.mark.parametrize(
    ("label", "secret_string"),
    [
        pytest.param("empty string", "", id="empty"),
        pytest.param("whitespace only", "   ", id="whitespace"),
        pytest.param("empty JSON value", json.dumps({"webhook_secret": ""}), id="empty-json-value"),
    ],
)
def test_an_empty_webhook_secret_is_a_500_and_never_authenticates_anyone(
    wired, label, secret_string
):
    """An empty HMAC key is a universal forgery, and it needs no secret material.

    Anyone who knows (or guesses) that the secret version is empty can compute
    `hmac.new(b"", body)` and be believed. This is reachable by accident: a human
    running `put-secret-value --secret-string ""`, or writing the JSON key with an
    empty value, in step 6.

    So the forged signature below is built with an EMPTY key -- exactly what an
    attacker could produce -- and the handler must answer 500 (we are
    misconfigured) rather than 202 (you are GitHub) or 401 (your signature is
    wrong, which would send the next person hunting a signature bug).
    """
    events, _ = wired(secret_string=secret_string)
    forged = "sha256=" + hmac.new(b"", SIMPLE_BODY, hashlib.sha256).hexdigest()

    response = handler.handler(_event(body=SIMPLE_BODY, signature=forged), None)

    assert response["statusCode"] == 500, (
        f"{label}: an empty webhook secret produced {response['statusCode']}. "
        "With an empty key the HMAC is forgeable by anyone, so this must fail "
        "closed as a configuration fault -- not 202, and not 401."
    )
    assert events.calls == [], f"{label}: published on an empty-key signature"
    # Replay impossible: the handler refuses this secret, so no signature it
    # would accept can be constructed.
    _assert_the_stub_could_still_have_recorded(events)


def test_an_empty_secret_does_not_even_reach_the_comparison(wired, monkeypatch):
    """Fail closed BEFORE the compare, not by happening to mismatch.

    Without this, a handler could pass the test above for the wrong reason -- an
    empty key still produces a digest, so most signatures would mismatch and 401
    anyway. The property is that an empty secret is rejected as a fault, so the
    comparison must never run.
    """
    wired(secret_string="")
    calls: list[tuple] = []
    real = hmac.compare_digest
    monkeypatch.setattr(
        handler.hmac, "compare_digest", lambda a, b: (calls.append((a, b)), real(a, b))[1]
    )

    response = handler.handler(
        _event(body=SIMPLE_BODY, signature=_sign(FAKE_SECRET, SIMPLE_BODY)), None
    )

    assert response["statusCode"] == 500, response
    assert calls == [], (
        "the handler compared a signature against a digest keyed on an EMPTY "
        "secret. It must reject the secret as unusable before computing anything."
    )
