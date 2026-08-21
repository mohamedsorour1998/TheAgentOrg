"""GitHub webhook -> HMAC verification -> EventBridge. Runs on AWS Lambda.

OWNER: Task 5 (cloud-native platform lane).

THE ONE THING TO UNDERSTAND BEFORE EDITING THIS FILE
====================================================
The Function URL in front of this handler is created with
`authorization_type = "NONE"` (infra/Terraform/modules/ingress/main.tf). That is
not an oversight and it cannot be tightened: GitHub cannot sign a SigV4 request,
and IAM auth would reject every delivery. The consequence is blunt --

    THIS ENDPOINT IS INTERNET-REACHABLE AND UNAUTHENTICATED AT THE AWS LAYER.

No IAM policy, no resource policy, no VPC and no WAF stands between an anonymous
POST from anywhere on the internet and this function's first line. The HMAC-SHA256
signature over the request body is the ONLY access control in the entire path.

So the ordering below is the security boundary, not a style choice:

    method check -> signature present -> secret fetch -> compare_digest -> PUBLISH

Nothing before `compare_digest` succeeds may cost money, mutate anything, or
publish. A handler that publishes to EventBridge and then returns 401 is the
real-world defect: the caller reads a refusal while the pipeline has already
started. `tests/test_ingress_handler.py` asserts ZERO `PutEvents` on every
reject path for exactly that reason, and proves the counter works by replaying a
valid delivery through the same stub.

WHY THIS FILE LIVES UNDER infra/ AND NOT UNDER agentorg/
========================================================
`tests/test_agentcore_deploy_assets.py:164` AST-walks every `agentorg/**/*.py`
and fails when a third-party top-level import is missing from
`agentorg/agents/requirements.txt`. This module imports `boto3`, which the Lambda
Python runtime already provides -- adding it to that requirements file would ship
a redundant dependency into the five agent images. Keeping the handler outside
the package keeps it outside the walk. Do not move it, and do not import
`agentorg` from here: the deployment package for this Lambda is this one file.

THE FOUR TRAPS, EACH HANDLED DELIBERATELY
=========================================
1. RAW BODY. The HMAC is computed over the exact octets GitHub sent. Any JSON
   round-trip (`json.dumps(json.loads(body))`) renormalises whitespace and key
   order and changes the digest, and the symptom is every delivery 401ing --
   which reads as a wrong secret and sends the next person to rotate a secret
   that was always correct. `_raw_body` returns bytes and nothing between it and
   `hmac.new` touches them.

2. isBase64Encoded. A Function URL base64-encodes a body it does not classify as
   text; the flag says which. GitHub signed the DECODED octets, so the decode
   must happen before the HMAC. Decision, stated because getting it wrong looks
   identical to trap 1: we honour the flag and decode, we do not sniff the body,
   and we do not attempt an opportunistic base64 decode when the flag is false --
   a JSON body can be valid base64 by accident, and guessing would make the
   digest depend on the payload's contents.

3. HEADER CASE. Function URLs deliver header names lower-cased, so
   `headers["x-hub-signature-256"]` works today. Relying on that silently means
   the same code 401s behind anything that preserves case (API Gateway REST,
   ALB, a local test harness). `_header` lower-cases the incoming keys itself, so
   the behaviour does not depend on the integration in front of it.

4. compare_digest, NEVER `==`. `==` on a digest returns early at the first
   differing byte and leaks its position through timing, which is enough to
   forge a signature one byte at a time. Both sides are converted to BYTES
   before comparison as well: `hmac.compare_digest` raises TypeError on a str
   containing non-ASCII, so a hostile header of `sha256=é` against a str
   comparison would become a 502 instead of a 401.

WHAT ELSE THIS REFUSES TO CONFLATE
==================================
"Denied" and "not configured yet" are different answers and this handler never
returns the first for the second. An unreadable secret, a JSON secret missing
its `webhook_secret` key, and a failed publish are all 500 -- never 401. A 401
in this system means "your signature did not verify", and if it can also mean
"the secret version has not been written yet" then the first person to debug it
goes hunting a signature bug that does not exist.

Likewise a `PutEvents` that answers HTTP 200 with `FailedEntryCount: 1` has
DROPPED the event. Returning 202 there would show GitHub a green delivery for a
run that will never start, so the count is checked and turned into a 500, which
GitHub shows as a failed delivery and offers to redeliver.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os

import boto3

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

# The key inside a JSON-shaped secret. Secrets Manager stores an opaque string;
# `put-secret-value --secret-string s3cret` gives a bare string while the
# console's key/value editor gives `{"webhook_secret": "s3cret"}`. Both shapes
# are accepted (see `_webhook_secret`) because which one you get depends on how
# the human in step 6 wrote it, and a mismatch would 401 every delivery.
SECRET_JSON_KEY = "webhook_secret"

SIGNATURE_HEADER = "x-hub-signature-256"
EVENT_NAME_HEADER = "x-github-event"
DELIVERY_HEADER = "x-github-delivery"
SIGNATURE_PREFIX = "sha256="

# Module-level, so a warm container reuses them instead of paying a client
# construction and a GetSecretValue per delivery. Cleared by `_reset_caches`,
# which exists for the tests: a secret cached across tests is the "a stale hit
# looks exactly like a fetch" defect.
_EVENTS_CLIENT = None
_SECRETS_CLIENT = None
_SECRET_CACHE: str | None = None


def _events():
    """The EventBridge seam. The ONLY place an events client is built.

    Single seam on purpose: the test suite replaces this one function with a
    recording stub, which is what makes "zero PutEvents on the reject path"
    assertable at all.
    """
    global _EVENTS_CLIENT
    if _EVENTS_CLIENT is None:
        _EVENTS_CLIENT = boto3.client("events")
    return _EVENTS_CLIENT


def _secrets():
    """The Secrets Manager seam. The ONLY place a secretsmanager client is built."""
    global _SECRETS_CLIENT
    if _SECRETS_CLIENT is None:
        _SECRETS_CLIENT = boto3.client("secretsmanager")
    return _SECRETS_CLIENT


def _reset_caches() -> None:
    """Drop the cached clients and the cached secret. Used by the tests."""
    global _EVENTS_CLIENT, _SECRETS_CLIENT, _SECRET_CACHE
    _EVENTS_CLIENT = None
    _SECRETS_CLIENT = None
    _SECRET_CACHE = None


def _response(status: int, message: str) -> dict:
    """A body that says nothing a caller could use.

    The endpoint is public, so this response is readable by anyone. It never
    contains the secret, the expected signature, or any part of either -- handing
    back the digest we wanted would let a caller obtain a valid signature for a
    body of their choosing, which is the whole authentication scheme.
    """
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"message": message}),
    }


def _header(event: dict, name: str) -> str:
    """Read a header case-insensitively. See trap 3 in the module docstring."""
    headers = event.get("headers") or {}
    lowered = {str(k).lower(): v for k, v in headers.items()}
    value = lowered.get(name.lower(), "")
    return value if isinstance(value, str) else ""


def _method(event: dict) -> str:
    ctx = event.get("requestContext") or {}
    http = ctx.get("http") or {}
    method = http.get("method") or event.get("httpMethod") or ""
    return str(method).upper()


def _raw_body(event: dict) -> bytes | None:
    """The exact octets GitHub signed, or None if they cannot be recovered.

    Returns bytes and never str: everything downstream of here hashes these
    bytes unchanged. See traps 1 and 2 in the module docstring.
    """
    body = event.get("body")
    if body is None:
        return None
    if event.get("isBase64Encoded"):
        if not isinstance(body, str):
            return None
        try:
            # validate=True so a body that is merely non-base64 text is rejected
            # here rather than silently decoding to different octets than were
            # signed. A wrong digest would report as 401 and hide the cause.
            return base64.b64decode(body, validate=True)
        except (ValueError, TypeError):
            return None
    if isinstance(body, bytes):
        return body
    return str(body).encode("utf-8")


def _webhook_secret() -> str:
    """Fetch and cache the webhook secret. Raises on anything unusable.

    Raising rather than returning "" is deliberate: an empty key would still
    produce a valid-looking HMAC that never matches, so every delivery would
    401 and the fault would look like a wrong signature.
    """
    global _SECRET_CACHE
    if _SECRET_CACHE is not None:
        return _SECRET_CACHE

    arn = os.environ["WEBHOOK_SECRET_ARN"]
    raw = _secrets().get_secret_value(SecretId=arn)["SecretString"]

    secret = raw
    stripped = raw.strip()
    if stripped.startswith("{"):
        # A JSON object whose key is misspelled must NOT fall through to using
        # the whole JSON document as the HMAC key: that 401s every delivery
        # while looking exactly like a wrong secret.
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict) or SECRET_JSON_KEY not in parsed:
            raise ValueError(
                f"the webhook secret is JSON but has no {SECRET_JSON_KEY!r} key; "
                f"keys present: {sorted(parsed) if isinstance(parsed, dict) else type(parsed)}"
            )
        secret = str(parsed[SECRET_JSON_KEY])

    # `not secret` alone misses a whitespace-only value, which is reachable by
    # accident (`put-secret-value --secret-string " "`, or a JSON value of "  ").
    # Such a key is not empty, so the HMAC succeeds and every delivery merely
    # 401s -- but it is a 1-3 byte key an attacker can guess outright, which is
    # the same universal-forgery hazard as an empty one. Both fail closed here.
    if not secret.strip():
        raise ValueError("the webhook secret is empty or whitespace only")

    _SECRET_CACHE = secret
    return secret


def _signature_matches(secret: str, body: bytes, provided: str) -> bool:
    """Constant-time compare of `sha256=<hex>` against the body's real digest.

    Both operands are encoded to bytes before comparing -- see trap 4. Length
    mismatch is handled by compare_digest itself, which is why no early return
    on `len` appears here.
    """
    expected = SIGNATURE_PREFIX + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(
        provided.encode("utf-8", errors="replace"), expected.encode("utf-8")
    )


def handler(event, context):
    """Verify the signature, then publish. In that order, always.

    `context` is unused and stays in the signature because it is the Lambda
    invocation contract, not an optional convenience -- the runtime calls this
    with two positional arguments. No `# noqa` marks it: this repo forbids them
    (and ruff's default rule set does not flag unused arguments anyway).
    """
    method = _method(event)
    if method != "POST":
        # The URL is public, so it gets crawled. 405 rather than 401 so a probe
        # never reads as a signature failure in the logs.
        LOG.info("rejected non-POST method: %s", method)
        return _response(405, "method not allowed")

    provided = _header(event, SIGNATURE_HEADER)
    if not provided.startswith(SIGNATURE_PREFIX):
        # Covers both the missing header and a malformed one, and does so BEFORE
        # touching Secrets Manager: an anonymous caller must not be able to drive
        # GetSecretValue calls against a public endpoint.
        LOG.warning("rejected delivery: no usable %s header", SIGNATURE_HEADER)
        return _response(401, "unauthorized")

    body = _raw_body(event)
    if body is None:
        LOG.warning("rejected delivery: body missing or not decodable")
        return _response(401, "unauthorized")

    try:
        secret = _webhook_secret()
    except Exception:
        # 500, never 401. "We cannot read our own secret" is not "your signature
        # is wrong", and conflating them sends the next person to rotate a
        # secret that was always correct.
        LOG.exception("could not read the webhook secret")
        return _response(500, "webhook secret unavailable")

    if not _signature_matches(secret, body, provided):
        LOG.warning(
            "rejected delivery %s: signature mismatch",
            _header(event, DELIVERY_HEADER) or "<no delivery id>",
        )
        return _response(401, "unauthorized")

    # ── verified. only now does anything happen. ──────────────────────────────

    detail = body.decode("utf-8", errors="replace")
    try:
        json.loads(detail)
    except ValueError:
        # A correctly signed body that is not JSON means the App is set to
        # `application/x-www-form-urlencoded`. PutEvents would reject the Detail
        # with a validation error that names nothing useful; say it here instead.
        LOG.error("verified delivery is not JSON -- check the App's content type")
        return _response(400, "body is not JSON; set the App content type to application/json")

    entry = {
        "EventBusName": os.environ["EVENT_BUS_NAME"],
        "Source": os.environ.get("EVENT_SOURCE", "github.webhook"),
        # GitHub's event name verbatim. The EventBridge rule matches
        # `detail-type: ["issues"]`; inventing a value here means the rule
        # matches nothing, the bus accepts the event, and nothing turns red.
        "DetailType": _header(event, EVENT_NAME_HEADER) or "unknown",
        "Detail": detail,
    }

    try:
        result = _events().put_events(Entries=[entry])
    except Exception:
        LOG.exception("PutEvents raised")
        return _response(500, "could not publish the event")

    # PutEvents answers HTTP 200 while refusing entries. Reading only the HTTP
    # status shows GitHub a green delivery for a run that never starts.
    if result.get("FailedEntryCount"):
        LOG.error("PutEvents rejected the entry: %s", result.get("Entries"))
        return _response(500, "the event bus rejected the event")

    LOG.info(
        "accepted delivery %s (%s)",
        _header(event, DELIVERY_HEADER) or "<no delivery id>",
        entry["DetailType"],
    )
    return _response(202, "accepted")
