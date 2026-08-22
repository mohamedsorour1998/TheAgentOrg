"""HTTP entrypoint for one agent inside an AgentCore runtime. OWNER: Sorour.

Each of the five runtimes runs this same module with a different AGENT_ROLE, so
there is one server to reason about rather than five near-copies that drift.

WHY THIS EXISTS AT ALL. `agentorg/agents/*.py` each expose `run(state) ->
Result`, which is an in-process Python call. AgentCore invokes a container over
HTTP, so something has to translate. This is that translation and nothing more:
it does not decide, does not fall back, and does not reorder the graph. The
verdict logic stays in `agentorg/state.py` and the agents.

THE CONTRACT, from the AgentCore runtime spec:

    POST /invocations   the payload; returns the agent's result as JSON
    GET  /ping          liveness; 200 with a small body

Served with the standard library rather than a framework, matching
`agentorg/approve_server.py`. A container's HTTP surface is two routes; adding
FastAPI to serve them would add a dependency to every image for no behaviour.

WHAT A CALLER SENDS. The full RunState as JSON, because every agent's `run`
takes a RunState and reads different parts of it -- the reviewer needs
`state.dev`, the security agent needs the diff, the planner needs only the
ticket. Sending a narrower payload per agent would mean five payload shapes and
five places to change when the contract moves.

WHAT IT RETURNS. `{"result": <the agent's result>, "agent": "<role>"}`. The role
is echoed so a caller reading a log can tell which runtime answered; a bare
result cannot be attributed, and during a five-runtime deploy the most likely
failure is invoking the wrong one.

FAILURES ARE NOT SWALLOWED HERE. If `run` raises, this returns 500 with the
exception type and message. The agents already absorb every model-side failure
and fall back to fixtures -- see the note in `planner.py` -- so an exception
reaching this layer means something the agents deliberately did not handle, and
turning it into a 200 with an empty result would recreate this project's
signature defect: a green response meaning "the check did not run".
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import ValidationError

from ..common import llm
from ..state import RunState
from . import developer, planner, reviewer, security, sre

# The five roles, mapped to the module that implements each. Deriving the valid
# set from this dict rather than restating it means a sixth agent is one line.
AGENTS = {
    "planner": planner,
    "developer": developer,
    "reviewer": reviewer,
    "security": security,
    "sre": sre,
}

# A payload larger than this is refused before it is read into memory. The
# RunState for a real run is a few kilobytes; the cap exists so a malformed or
# hostile Content-Length cannot make the container allocate without bound.
MAX_BODY_BYTES = 4 * 1024 * 1024

_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def agent_role() -> str:
    """Which agent this container serves, from AGENT_ROLE.

    Raises rather than defaulting. A default would mean a misconfigured runtime
    silently serving the wrong agent -- returning plans where the caller
    expected a security verdict -- and every response would look successful.
    """
    role = os.environ.get("AGENT_ROLE", "").strip()
    if role not in AGENTS:
        raise RuntimeError(
            f"AGENT_ROLE must be one of {sorted(AGENTS)}; got {role!r}. "
            f"Set it with `agentcore configure --env AGENT_ROLE=<role>`."
        )
    return role


class Handler(BaseHTTPRequestHandler):
    """The two routes AgentCore calls."""

    # BaseHTTPRequestHandler defaults to HTTP/1.0, which closes the connection
    # after every response. AgentCore reuses connections across invocations.
    protocol_version = "HTTP/1.1"

    server_version = "agentorg-agent/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        """Route access logs through logging so CloudWatch captures them.

        The default writes to stderr in its own format; AgentCore collects
        stdout/stderr but the timestamps would not match the application's.
        """
        logging.getLogger(__name__).info("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        # Explicit length, because HTTP/1.1 keep-alive needs it to know where
        # one response ends.
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/ping", ""):
            # Reports the role too, so a health check confirms which agent this
            # runtime serves rather than only that a process is listening.
            self._send(200, {"status": "healthy", "agent": os.environ.get("AGENT_ROLE", "")})
            return
        self._send(404, {"error": "not found", "path": self.path})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/invocations":
            self._send(404, {"error": "not found", "path": self.path})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "Content-Length is not an integer"})
            return

        if length <= 0:
            self._send(400, {"error": "empty body; POST the RunState as JSON"})
            return

        if length > MAX_BODY_BYTES:
            self._send(413, {"error": f"body exceeds {MAX_BODY_BYTES} bytes"})
            return

        raw = self.rfile.read(length)

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send(400, {"error": f"body is not valid JSON: {exc}"})
            return

        # A caller may send the RunState bare or wrapped as {"state": ...}.
        # Accepting both costs one line and removes a class of 400 that looks
        # like an agent failure.
        if isinstance(payload, dict) and "state" in payload:
            payload = payload["state"]

        try:
            state = RunState.model_validate(payload)
        except ValidationError as exc:
            # The frozen contract rejected it. Returning the validation detail
            # is what makes a caller's mistake fixable without container logs.
            self._send(422, {"error": "payload is not a valid RunState", "detail": exc.errors()})
            return

        try:
            role = agent_role()
            # RESET BEFORE THE CALL, so `source` below describes THIS invocation
            # rather than whatever the previous one on this warm container did.
            # AgentCore reuses containers, so without the reset a single early
            # model success would label every later fixture answer a model answer.
            llm.reset_source()
            result = AGENTS[role].run(state)
        except Exception as exc:
            logging.getLogger(__name__).exception("agent invocation failed")
            self._send(500, {"error": type(exc).__name__, "detail": str(exc)})
            return

        # `source` IS THE PROVENANCE, AND IT HAS TO TRAVEL, for exactly the reason
        # `RunState.poisoned` is a field rather than a kwarg: the fact exists only
        # inside this container.
        #
        # MEASURED 2026-08-22 before this was here. The deployed pipeline printed
        # `_source=none` while the plan comment on the target repo was
        # unmistakably model-written -- six tasks naming files no fixture contains.
        # `llm.last_source()` on the RUNNER is always None under
        # REMOTE_AGENTS=true, because the model call happens here and the runner
        # never touches its own `llm` module. So the provenance feature reported
        # nothing precisely on the path it was built to describe.
        #
        # An extra key on the envelope is backward compatible: a runner reading an
        # older container's response finds it absent and records "" -- unknown --
        # which is the honest answer for a container that could not tell it.
        #
        # mode="json" so datetimes and enums serialise; model_dump() alone
        # returns objects json.dumps cannot encode.
        self._send(200, {
            "agent": role,
            "result": result.model_dump(mode="json"),
            "source": llm.last_source() or "",
        })


def main() -> None:
    """Serve until killed. Port 8080 is the AgentCore default."""
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

    # Fail at startup, not on the first invocation. A container that starts
    # healthy and 500s on every call is harder to diagnose than one that never
    # reports ready, and AgentCore surfaces a crash-looping container plainly.
    role = agent_role()

    port = int(os.environ.get("PORT", "8080"))

    # Threading, because AgentCore may issue concurrent invocations and the
    # single-threaded server would serialise them behind one slow model call.
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logging.getLogger(__name__).info("agent %s listening on port %s", role, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
