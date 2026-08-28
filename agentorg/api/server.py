"""The HTTP transport. The only module here that knows about sockets.

OWNER: Lane K.

THE CONTRACT IS `agentorg/agents/server.py`'S, DELIBERATELY REUSED
=================================================================
Same base class, same status vocabulary, same reasons. A caller who has integrated
against an AgentCore runtime does not learn a second dialect, and a reviewer who
has read that file can review this one.

The one ordering that is not merely a convention: **413 IS CHECKED BEFORE THE
BODY IS READ.** `agents/server.py` puts it there "so a malformed or hostile
Content-Length cannot make the container allocate without bound", and
`test_the_body_cap_is_checked_before_the_read` asserts it here by sending a
declared length far above the cap with no body behind it -- a server that read
first would block on a socket that never delivers.

WHAT BINDS WHERE, AND WHY THE DEFAULT IS LOOPBACK
================================================
`serve()` binds `127.0.0.1` by default and takes an explicit `host` to do anything
else. That default is not this module claiming to be safe off-host -- it has real
authentication, unlike `approve_server.py` -- it is the same direction every other
default in this repository takes: the safe value is what you get for saying
nothing, and the exposed one costs a deliberate argument.

**`approve_server.py` is a different program and this does not change it.** That
one has NO authentication, binds loopback only, and must never be exposed
off-host, because it resumes a paused pipeline past a human gate. This module
cannot resume anything (see `api/__init__.py`), so the two are not
interchangeable and neither one's binding argument transfers to the other.

WHAT AN UNAUTHENTICATED CALLER REACHES
=====================================
`GET /v1/health` and `GET /v1/openapi.json` -- neither reads tenant data -- and
`POST /v1/ingress/<provider>`, which carries a provider signature instead of a
bearer key and verifies it before parsing. Everything else calls `auth.resolve`
first, and with an empty key store that is a 401. There is no route that reads a
tenant id from a body or a path: the tenant comes from the credential, which is
what stops this API becoming a way around `tenancy`'s accessors.

INGRESS SECRETS ARE SUPPLIED BY THE OPERATOR, NOT READ FROM A TENANT'S ROW
=========================================================================
`set_ingress_secret(provider, secret)` and nothing else. This module does not
reach into Secrets Manager (that is the Lambda's, with an IAM policy scoped to one
secret ARN) and does not read `tenancy` secrets, because a webhook arrives with no
credential and therefore no tenant -- so a lookup would have to guess whose secret
to try, and "try them all" is an oracle for which tenants exist.

The honest consequence, stated because it is a real limit: a single ingress secret
per provider means one webhook endpoint serves every tenant, and the payload has
to say which repository it concerns. Per-tenant ingress needs the secret keyed by
something in the URL, which is a design decision for whoever wires this to real
customers -- not something to invent silently here.
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from . import auth, ingress, openapi, service
from .errors import ApiError, BadRequest, NotFound, PayloadTooLarge, Unprocessable

# Same cap and same reason as `agents/server.py`: a submission is a few hundred
# bytes and the cap exists so a hostile Content-Length cannot make the process
# allocate. Checked BEFORE the read.
MAX_BODY_BYTES = 4 * 1024 * 1024

# The header a client sends to make a retry safe. Read case-insensitively by
# `BaseHTTPRequestHandler`'s own header container.
IDEMPOTENCY_HEADER = "Idempotency-Key"

# Operator-supplied ingress secrets, per provider. Empty by default, and empty
# means every ingress delivery is refused -- the same direction as the key store:
# "nobody configured this" must not read as "anyone may post here".
_INGRESS_SECRETS: dict[str, str] = {}


def set_ingress_secret(provider: str, secret: str) -> None:
    """Install the shared secret for one provider. Refuses an unknown provider.

    Refuses rather than storing, so a typo'd provider name cannot leave a secret
    installed under a key nothing reads -- which would present as "the webhook
    still 401s" with a secret that was correctly configured for a provider that
    does not exist.
    """
    ingress.provider_for(provider)
    _INGRESS_SECRETS[provider.lower().strip()] = secret


def clear_ingress_secrets() -> None:
    """Drop every ingress secret. For tests, and for tests only."""
    _INGRESS_SECRETS.clear()


def _ingress_secret(provider: str) -> str:
    """The provider's secret, or `""`.

    Returns the blank rather than raising, because `ingress.verify_delivery`
    already refuses a blank secret with the message that explains why -- and one
    refusal in one place is better than two that could disagree about which
    status it is.
    """
    return _INGRESS_SECRETS.get(provider.lower().strip(), "")


class Handler(BaseHTTPRequestHandler):
    """The routes in `openapi.ROUTES`, and nothing else.

    `protocol_version = "HTTP/1.1"` with an explicit Content-Length on every
    response, matching `agents/server.py` -- without the length, keep-alive has no
    way to know where one response ends.
    """

    protocol_version = "HTTP/1.1"
    server_version = "agentorg-api/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        """Access logs through `logging`, as `agents/server.py` does."""
        logging.getLogger(__name__).info("%s - %s", self.address_string(), fmt % args)

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        """The request body, refusing before allocating.

        THE ORDER IS THE POINT and it is `agents/server.py`'s:

            1. Content-Length is not an integer -> 400
            2. length <= 0                      -> 400
            3. length > cap                     -> 413, WITHOUT READING
            4. read

        Step 3 before step 4 is what stops a declared 4 GiB from being read into
        memory. A server that read first and then compared would have already
        done the damage, and the test that catches the difference has to send a
        length with no body behind it -- which is why it is written that way.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise BadRequest("Content-Length is not an integer") from exc
        if length <= 0:
            raise BadRequest("empty body; POST JSON")
        if length > MAX_BODY_BYTES:
            raise PayloadTooLarge(f"body exceeds {MAX_BODY_BYTES} bytes")
        return self.rfile.read(length)

    def _json_body(self) -> dict:
        """The body as a dict, or a 400. Never a bare list or scalar."""
        raw = self._read_body()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest(f"body is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise BadRequest(
                f"body is a JSON {type(payload).__name__}, not an object"
            )
        return payload

    def _credential(self) -> auth.Credential:
        """Who is calling. Raises `Unauthenticated` when nobody verifies.

        THE ONLY SOURCE OF A TENANT IN THIS MODULE. No handler reads a tenant from
        a path or a body; `test_no_route_takes_a_tenant_from_the_request` asserts
        that structurally, because a route that accepted one would be a way around
        every accessor in `tenancy`.
        """
        return auth.resolve(self.headers.get("Authorization"))

    def _path(self) -> list[str]:
        """The path split into unquoted segments, with the query dropped.

        `unquote` per segment AFTER splitting, never before: unquoting first turns
        a `%2F` into a `/` and creates a segment boundary the client chose, which
        is how a path like `runs/%2E%2E%2Fetc` becomes traversal. Splitting first
        means an encoded slash stays inside one segment, where
        `log.is_safe_run_id` then refuses it.
        """
        raw = urlparse(self.path).path
        return [unquote(part) for part in raw.strip("/").split("/") if part]

    # ── dispatch ─────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        """Route, run, and turn every refusal into its status.

        `except ApiError` is FIRST and narrow -- those are this package's own
        deliberate refusals and each carries its status. The broad
        `except Exception` beneath it becomes a 500 with the exception's type,
        which is `agents/server.py`'s rule: an exception reaching this layer is
        something nobody classified, and "turning it into a 200 with an empty
        result would recreate this project's signature defect". The logger is
        fetched INLINE per CLAUDE.md, because ruff's BLE001 cannot resolve a
        module-level alias and `_log.exception(...)` turns the lint gate red.
        """
        try:
            self._route(method, self._path())
        except ApiError as refusal:
            self._send(refusal.status, refusal.payload())
        except ValidationError as invalid:
            # A pydantic failure that a handler did not already convert. Same 422
            # and same reason as `agents/server.py`: "returning the validation
            # detail is what makes a caller's mistake fixable without container
            # logs."
            self._send(422, Unprocessable("invalid request body",
                                          detail=invalid.errors()).payload())
        except ValueError as refused:
            # A refusal from a substrate module: `queue.enqueue`'s duplicate,
            # `ingress`'s blank secret, `idempotency`'s bounds. These are 500s
            # rather than 400s on purpose -- each one means the SERVICE was asked
            # to do something inconsistent, not that the caller's JSON was wrong,
            # and misreporting it as a 400 would send a caller looking at a body
            # that was fine.
            logging.getLogger(__name__).exception("a substrate module refused")
            self._send(500, {"error": type(refused).__name__,
                             "detail": str(refused)})
        except Exception as crash:
            logging.getLogger(__name__).exception("unhandled failure serving %s",
                                                  self.path)
            self._send(500, {"error": type(crash).__name__, "detail": str(crash)})

    def _route(self, method: str, parts: list[str]) -> None:
        """The dispatcher. Every served path is in `openapi.ROUTES`.

        A 404 for anything unmatched, including a wrong METHOD on a real path --
        deliberately not 405. A 405 tells an unauthenticated caller that a path
        exists, which is the same reasoning that makes an unknown ingress provider
        a 404, and this API's paths are not something an anonymous caller needs
        enumerated.
        """
        if parts[:1] != ["v1"]:
            raise NotFound("unknown path")
        rest = parts[1:]

        if method == "GET" and rest == ["health"]:
            # No credential, no tenant data. Reports the route count so a health
            # check confirms the dispatcher is loaded rather than only that a
            # process is listening -- `agents/server.py`'s /ping reports its role
            # for the same reason.
            self._send(200, {"status": "healthy", "routes": len(openapi.ROUTES)})
            return

        if method == "GET" and rest == ["openapi.json"]:
            self._send(200, openapi.openapi_document())
            return

        if method == "POST" and rest == ["runs"]:
            self._submit()
            return

        if method == "GET" and len(rest) == 2 and rest[0] == "runs":
            status = service.run_status(self._credential(), rest[1])
            self._send(200, status.model_dump(mode="json"))
            return

        if method == "POST" and len(rest) == 3 and rest[0] == "runs" \
                and rest[2] == "cancel":
            self._cancel(rest[1])
            return

        if len(rest) == 3 and rest[0] == "repositories" and rest[2] == "config":
            self._config(method, rest[1])
            return

        if method == "POST" and len(rest) == 2 and rest[0] == "ingress":
            self._ingress(rest[1])
            return

        raise NotFound("unknown path")

    # ── handlers ─────────────────────────────────────────────────────────────

    def _submit(self) -> None:
        """K1. Validate, submit, and say whether this was a replay.

        `idempotent_replay` IS ON THE RESPONSE, always, both true and false. Sent
        only when true it would be indistinguishable from an older server that
        did not send it -- the distinction `llm.absorb_usage_payload` makes
        between an absent key and a zero row, and the reason `Usage.
        cached_reported` records presence rather than truthiness.
        """
        credential = self._credential()
        payload = self._json_body()
        try:
            submission = service.RunSubmission.model_validate(payload)
        except ValidationError as invalid:
            raise Unprocessable("payload is not a valid run submission",
                                detail=invalid.errors()) from invalid
        key = (self.headers.get(IDEMPOTENCY_HEADER) or "").strip()
        status, replayed = service.submit_run(credential, submission,
                                             idempotency_key=key)
        body = status.model_dump(mode="json")
        body["idempotent_replay"] = replayed
        self._send(200, body)

    def _cancel(self, run_id: str) -> None:
        """K2's write. A body is OPTIONAL here, and that is deliberate.

        A cancel with no body is the common case (`curl -X POST`), so requiring
        one would make the simplest correct call a 400. When a body IS sent it may
        carry `reason`, which is logged and never written into the run's state --
        `gates.py` is the one writer of a `RunState`.
        """
        credential = self._credential()
        reason = ""
        if (self.headers.get("Content-Length") or "").strip() not in ("", "0"):
            reason = str(self._json_body().get("reason", ""))[:500]
        status = service.cancel_run(credential, run_id, reason=reason)
        self._send(200, status.model_dump(mode="json"))

    def _config(self, method: str, full_name: str) -> None:
        """K3. GET reads the effective config; PUT sets it.

        The repository name arrives URL-encoded because it contains a slash
        (`acme%2Fauth-service`), and `_path` unquotes AFTER splitting, so the
        encoded slash stays one segment rather than becoming a path boundary the
        caller chose.
        """
        credential = self._credential()
        if method == "GET":
            self._send(200, service.read_config(credential, full_name)
                       .model_dump(mode="json"))
            return
        if method != "PUT":
            raise NotFound("unknown path")
        payload = self._json_body()
        # The name comes from the PATH, never the body. Taking it from the body
        # would let a caller configure a repository other than the one they asked
        # for -- and the two would silently disagree in a log.
        payload["full_name"] = full_name
        try:
            config = service.RepositoryConfig.model_validate(payload)
        except ValidationError as invalid:
            raise Unprocessable("payload is not a valid repository config",
                                detail=invalid.errors()) from invalid
        self._send(200, service.write_config(credential, config)
                   .model_dump(mode="json"))

    def _ingress(self, provider: str) -> None:
        """K4. Verify the delivery, then report what was accepted.

        THIS DOES NOT START A RUN, and the reason is the EventBridge rule's:
        deciding here whether an event is interesting "would make 'we never saw
        it' and 'we saw it and ignored it' indistinguishable". So a verified
        delivery answers 202 with the event name it carried, and what starts a run
        is a submission -- which a caller makes with a credential, through
        `POST /v1/runs`.

        202 AND NOT 200, matching the Lambda's own answer for the same act: the
        delivery is accepted, and nothing has been done with it yet.
        """
        raw = self._read_body()
        payload = ingress.verify_delivery(
            provider, dict(self.headers.items()), raw, _ingress_secret(provider)
        )
        event = ingress.event_name(provider, dict(self.headers.items()))
        delivery = ingress.delivery_id(provider, dict(self.headers.items()))
        logging.getLogger(__name__).info(
            "accepted %s delivery %s (%s)", provider,
            delivery or "<no delivery id>", event or "<no event name>",
        )
        self._send(202, {
            "accepted": True,
            "provider": provider,
            "event": event,
            # The payload's own keys, so a caller can confirm the body arrived
            # intact -- and NOT the payload itself, which is unbounded and
            # attacker-controlled and would be echoed back to whoever posted it.
            "keys": sorted(payload)[:20],
        })


def serve(host: str = "127.0.0.1", port: int = 8100) -> ThreadingHTTPServer:
    """Build the server. Loopback unless a caller says otherwise.

    Returns the server rather than calling `serve_forever`, so a test can drive it
    on a real socket and shut it down -- and so the binding decision is visible in
    one place instead of buried in a `main`.

    Threading, as `agents/server.py` is: a control-plane call that waits on scrypt
    for 28 ms should not serialise behind another one.
    """
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    """Serve until killed. Loopback by default; `API_HOST` widens it deliberately.

    THE BIND ADDRESS IS READ FROM THE ENVIRONMENT, and Lane N found why by trying to
    deploy this. `serve()` with no arguments binds `127.0.0.1`, so a container task would
    report RUNNING and answer nothing — a green deploy for a service reachable by nobody,
    which is this repository's signature defect wearing an orchestrator's badge.

    THE DEFAULT STAYS LOOPBACK. `0.0.0.0` as the default would silently expose this API
    on every interface of every machine that ever ran `python -m agentorg.api.server`,
    including a laptop on a café network — and `approve_server.py`'s comment records that
    loopback binding is one of only three things standing in for the authentication it
    lacks. Widening a bind address must be an act, not an inheritance.

    So a deployment sets `API_HOST=0.0.0.0` explicitly, in a task definition somebody
    reviewed. `PORT` follows the same rule but is uncontroversial.

    `.strip()` because a value arriving from a task definition or a Compose file carries
    whatever whitespace the YAML gave it, and `socket.bind` on `"0.0.0.0 "` fails with
    `gaierror` naming the address rather than the setting.
    """
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    host = os.environ.get("API_HOST", "").strip() or "127.0.0.1"
    port = int(os.environ.get("API_PORT", "").strip() or "8100")
    server = serve(host=host, port=port)
    logging.getLogger(__name__).info(
        "control plane listening on http://%s:%s (%d routes; an empty key store "
        "means every authenticated route answers 401)",
        *server.server_address[:2], len(openapi.ROUTES),
    )
    if host not in ("127.0.0.1", "localhost", "::1"):
        # SAID OUT LOUD, once, at startup. Every authenticated route already refuses
        # without a provisioned key, so this is not a hole -- but an operator who set
        # API_HOST without reading the auth model should learn it here rather than from
        # a judge.
        logging.getLogger(__name__).warning(
            "API_HOST=%s is not loopback: this control plane is reachable off-host. "
            "Every authenticated route refuses without a provisioned M2M key, and no "
            "route can approve a gate -- verify both before exposing it.", host,
        )
    server.serve_forever()


if __name__ == "__main__":
    main()
