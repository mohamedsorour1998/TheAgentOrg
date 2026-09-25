"""The retrieval record must come back from the container, or "switched on" is unprovable.

Found 2026-09-25 while switching RETRIEVAL_ENABLED on for the deployed runtimes. Each
agent's `_prompt` writes `state.retrieval` -- on the CONTAINER's copy of the run. The
200 envelope carried `result`, `source` and `usage` and nothing else, so the runner's
record stayed empty whether the knowledge bases were read or not: run #73's record was
empty, and with retrieval on it would have been exactly as empty.

The server half is EXECUTED over a real socket rather than read with `getsource`: this
repository has found tests satisfied by the comment explaining the thing they check.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from agentorg.agents import server
from agentorg.common import agent_client, config
from agentorg.state import PlanResult, RetrievalRecord, RunState

PLAN = PlanResult(tasks=["t"], acceptance_criteria=["a"], target_files=["app/auth.py"])
LOOKED_UP = RetrievalRecord(corpora=["conventions=retrieved"], documents=3, queries=["pin requests"])


def test_the_container_returns_what_the_agent_looked_up(monkeypatch):
    def planner_that_retrieves(state):
        state.retrieval = LOOKED_UP          # what planner._prompt does
        return PLAN

    monkeypatch.setenv("AGENT_ROLE", "planner")
    monkeypatch.setitem(server.AGENTS, "planner", SimpleNamespace(run=planner_that_retrieves))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        body = RunState(ticket_id="73", ticket_text="x").model_dump_json().encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/invocations", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as reply:
            envelope = json.loads(reply.read())
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert envelope["agent"] == "planner", envelope
    assert envelope.get("retrieval") == LOOKED_UP.model_dump(mode="json"), (
        "the 200 envelope does not carry the agent's retrieval record, so the runner "
        f"cannot tell a run that read its knowledge bases from one that did not: {envelope}"
    )


def _remote(monkeypatch, envelope):
    monkeypatch.setattr(config, "REMOTE_AGENTS", True)
    monkeypatch.setattr(agent_client, "_remote_state", lambda r, s, k: s.model_copy(deep=True))
    monkeypatch.setattr(agent_client, "_invoke", lambda role, state: envelope)


def test_the_runner_takes_the_record_from_the_reply_and_does_not_double_it(monkeypatch):
    # The container started from this record and added to it, so the reply holds the
    # WHOLE record. Appending would count the earlier lookup twice.
    earlier = RetrievalRecord(corpora=["conventions=retrieved"], documents=1, queries=["plan"])
    whole = RetrievalRecord(corpora=["conventions=retrieved", "repo_history=retrieved"],
                            documents=4, queries=["plan", "diff and ticket"])
    _remote(monkeypatch, {"agent": "planner", "result": PLAN.model_dump(mode="json"),
                          "retrieval": whole.model_dump(mode="json")})
    state = RunState(ticket_id="73", ticket_text="x", retrieval=earlier)

    agent_client.call_agent("planner", state)

    assert state.retrieval == whole, f"expected the container's whole record, got {state.retrieval}"


def test_an_older_container_leaves_the_record_as_it_was(monkeypatch):
    earlier = RetrievalRecord(corpora=["conventions=retrieved"], documents=1, queries=["plan"])
    _remote(monkeypatch, {"agent": "planner", "result": PLAN.model_dump(mode="json")})
    state = RunState(ticket_id="73", ticket_text="x", retrieval=earlier)

    agent_client.call_agent("planner", state)

    assert state.retrieval == earlier


def test_a_malformed_record_is_ignored_and_does_not_fail_the_stage(monkeypatch):
    _remote(monkeypatch, {"agent": "planner", "result": PLAN.model_dump(mode="json"),
                          "retrieval": {"documents": "not a number"}})
    state = RunState(ticket_id="73", ticket_text="x")

    result = agent_client.call_agent("planner", state)

    assert result == PLAN
    assert state.retrieval is None
