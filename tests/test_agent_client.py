"""The remote seam: one call site, two destinations. Owner: Sorour.

`agentorg/common/agent_client.py` is the only place that decides whether an
agent runs IN THIS PROCESS or in its deployed AgentCore runtime. graph.py used
to call `planner.run(state)` directly; it now calls `call_agent("planner",
state)`, and this file is what pins the two branches apart.

WHAT THESE TESTS ARE WRITTEN AGAINST. Not the shape of the code -- the failure
modes a seam like this actually has, every one of which has already cost this
project something:

  * A remote call that returns 200 with an empty body. `agents/server.py`'s own
    docstring names this ("turning it into a 200 with an empty result would
    recreate this project's signature defect: a green response meaning 'the
    check did not run'"). So the empty-result test asserts a RAISE, and the RED
    step for it deletes the check and watches this test go red.
  * `qualifier="DEFAULT"` missing. Measured, and recorded in the plan's shared
    context: without it `invoke_agent_runtime` fails ResourceNotFoundException
    even against a READY runtime with a READY endpoint. A stub cannot reproduce
    that error, so the test asserts the ARGUMENT is passed, which is the part
    code can be wrong about.
  * base64 in the payload. The CLI wants base64, boto3 wants raw bytes -- two
    interfaces to one API. A test asserting only "some payload was sent" passes
    against both, so the test decodes the payload and reads the ticket id out of
    it.
  * The wrong runtime answering. `theagentorg_review` must not satisfy
    `theagentorg_reviewer`, and a planner call must not accept a DevResult.
    Both are substring/duck-typing accidents that would render as success.

NO TEST HERE REACHES AWS. `_no_live_agentcore` blocks both clients for the whole
file, in the shape tests/test_deploy_note.py proved necessary rather than
guessed: THIS MACHINE HAS WORKING AWS CREDENTIALS, so a mutation that deletes a
branch can make a test fall through to the real control plane and call it for
real. The guard makes reaching AWS from this file the failure, instead of resting
the offline guarantee on the correctness of the code under test.
"""

import base64
import inspect
import io
import json

import pytest
from pydantic import ValidationError

from agentorg import github_ops
from agentorg.agents import developer, planner, server
from agentorg.common import agent_client, config
from agentorg.state import DevResult, PlanResult, RunState

TICKET = "Add a per-IP login rate limit."

# Built here rather than imported from fixtures_loader so a fixture edit cannot
# quietly change what these tests think a PlanResult is.
PLAN_FIXTURE = PlanResult(
    tasks=["Add a rate limiter"],
    acceptance_criteria=["429 after the fifth attempt"],
    target_files=["app/auth.py"],
)


def _state(**kwargs):
    return RunState(ticket_id="SEAM-1", ticket_text=TICKET, **kwargs)


# --------------------------------------------------------------------------
# The offline guard. Both clients, whole file.
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_live_agentcore(monkeypatch):
    """Block both AgentCore clients for every test in this file.

    A fifth-and-sixth seam guard in the shape of the four in tests/conftest.py
    and the one in tests/test_deploy_note.py. The data plane is the dangerous
    one: `invoke_agent_runtime` against a live runtime is a real, billable model
    call in a container, and this machine's credentials would let it happen.

    pytest.fail's `Failed` derives from BaseException, so no `except Exception`
    anywhere under test can swallow this into an honest-looking degradation.
    """
    def _blocked_data():
        pytest.fail(
            "This test reached the real agent_client._agentcore_data_client, "
            "which on a machine with AWS credentials INVOKES a live AgentCore "
            "runtime. Wire a _StubClient with _wire() instead.",
            pytrace=False,
        )

    def _blocked_control():
        pytest.fail(
            "This test reached the real agent_client._agentcore_control_client, "
            "which on a machine with AWS credentials is a live "
            "bedrock-agentcore-control call. Wire a _StubClient with _wire() "
            "instead.",
            pytrace=False,
        )

    monkeypatch.setattr(agent_client, "_agentcore_data_client", _blocked_data)
    monkeypatch.setattr(agent_client, "_agentcore_control_client", _blocked_control)


# --------------------------------------------------------------------------
# The stub client. Records every argument, so a test can assert on the call
# rather than only on the return value.
# --------------------------------------------------------------------------

class _StubPaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_kwargs):
        return iter(self._pages)


class _StubClient:
    """Stand-in for both AgentCore clients.

    One object serves both planes because a test that wires it in gets to assert
    on the control-plane lookup AND the data-plane invoke from the same place.
    """

    def __init__(self, *, envelope=None, pages=None, status=200, raise_on_invoke=None):
        self.envelope = envelope
        self.pages = pages if pages is not None else [_page(*_all_five())]
        self.status = status
        self.raise_on_invoke = raise_on_invoke
        self.invocations = []
        self.paginators_asked_for = []

    # -- control plane --
    def get_paginator(self, name):
        self.paginators_asked_for.append(name)
        return _StubPaginator(self.pages)

    # -- data plane --
    def invoke_agent_runtime(self, **kwargs):
        self.invocations.append(kwargs)
        if self.raise_on_invoke is not None:
            raise self.raise_on_invoke
        body = json.dumps(self.envelope).encode("utf-8")
        # botocore hands back a file-like object for the streaming `response`
        # blob, not bytes. MEASURED against botocore 1.43.75's own parser for
        # InvokeAgentRuntime: type BytesIO under a test harness, StreamingBody
        # over a real socket. Both answer .read(); bytes would not.
        return {"statusCode": self.status, "response": io.BytesIO(body),
                "contentType": "application/json"}


def _page(*runtimes):
    """One ListAgentRuntimes page. Field names are botocore's, not invented."""
    return {"agentRuntimes": list(runtimes)}


def _runtime(name, *, arn=None, status="READY"):
    return {
        "agentRuntimeName": name,
        "agentRuntimeArn": arn or f"arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/{name}",
        "status": status,
    }


def _all_five():
    return [_runtime(f"theagentorg_{role}") for role in server.AGENTS]


def _wire(monkeypatch, client):
    """Put the seam into remote mode against `client`, and hand it back."""
    monkeypatch.setattr(config, "REMOTE_AGENTS", True)
    monkeypatch.setattr(agent_client, "_agentcore_data_client", lambda: client)
    monkeypatch.setattr(agent_client, "_agentcore_control_client", lambda: client)
    return client


def _envelope(role, result):
    """What agents/server.py returns on 200 (server.py:172)."""
    return {"agent": role, "result": result.model_dump(mode="json")}


# For the two tests that must raise BEFORE any invoke. A recognisable envelope
# rather than None, so that if one of them ever DOES reach the stub, the failure
# says which test leaked rather than raising AttributeError inside the stub.
_UNREACHED_ENVELOPE = {"agent": "this-stub-should-not-have-been-reached", "result": {}}


# --------------------------------------------------------------------------
# The signature Task 3 depends on
# --------------------------------------------------------------------------

def test_the_signature_task_3_consumes():
    """`call_agent(role, state, **kwargs)`. Task 3 is written against this.

    Asserted rather than trusted, because a later refactor to
    `call_agent(state, role)` or to keyword-only parameters would break a
    consumer that is not in this repository yet, and nothing else here would
    notice.
    """
    sig = inspect.signature(agent_client.call_agent)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["role", "state", "kwargs"], (
        f"call_agent's parameters changed to {[p.name for p in params]}; "
        "Task 3 calls call_agent(role, state, **kwargs)"
    )
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[2].kind is inspect.Parameter.VAR_KEYWORD


# --------------------------------------------------------------------------
# Local mode -- the default, and Tuesday's fallback
# --------------------------------------------------------------------------

def test_remote_agents_defaults_false():
    """The local path stays the tested default.

    Read off the module rather than the environment: config.py resolves the
    knob at import, and every other test in the suite depends on this default
    being false. If this flips, 550 tests change what they exercise.
    """
    assert config.REMOTE_AGENTS is False, (
        "REMOTE_AGENTS must default false -- the local path is the tested "
        "default and the demo's fallback"
    )


def test_local_mode_calls_the_in_process_function(monkeypatch):
    """REMOTE_AGENTS=false must behave exactly as before this module existed."""
    monkeypatch.setattr(config, "REMOTE_AGENTS", False)
    called = []
    monkeypatch.setattr(planner, "run", lambda s: called.append(s) or PLAN_FIXTURE)

    state = _state()
    result = agent_client.call_agent("planner", state)

    assert called, "local mode did not call planner.run"
    # Identity, not equality: the agent must receive THE state, not a copy. A
    # copy would silently discard every mutation an agent makes to it.
    assert called[0] is state, "local mode passed a copy of the state, not the state"
    assert result is PLAN_FIXTURE, "local mode did not return planner.run's own result"


def test_local_mode_forwards_a_kwarg(monkeypatch):
    """`poisoned=True` must reach developer.run, or the poisoned demo dies.

    graph.py calls `call_agent("developer", state, poisoned=poisoned)`. A seam
    that accepted **kwargs and dropped them would leave the poisoned ticket
    with no AWS key in its diff, the scanners with nothing to catch, and the
    run PROMOTED -- which is the exact regression developer.py's
    `_key_is_in_the_change` docstring records as 2 blocks in 5 live runs.
    """
    monkeypatch.setattr(config, "REMOTE_AGENTS", False)
    seen = []

    def _recording_run(state, poisoned=False):
        seen.append(poisoned)
        return DevResult(branch="b", diff="d", summary="s", files_changed=["f"])

    monkeypatch.setattr(developer, "run", _recording_run)

    agent_client.call_agent("developer", _state(), poisoned=True)

    assert seen == [True], f"developer.run saw poisoned={seen}, expected [True]"


def test_local_mode_reaches_every_one_of_the_five_roles(monkeypatch):
    """All five roles resolve to a module that actually gets called.

    Parametrising over `server.AGENTS` rather than a literal list is what makes
    a sixth agent, or a renamed one, visible here. The `assert reached` guard is
    the rule-2 matcher check: if AGENTS were ever empty this loop would run zero
    assertions and stay green.
    """
    monkeypatch.setattr(config, "REMOTE_AGENTS", False)
    assert server.AGENTS, "server.AGENTS is empty; this test would pin nothing"

    reached = []
    for role, module in server.AGENTS.items():
        monkeypatch.setattr(
            module, "run",
            lambda s, _role=role, **_kw: reached.append(_role) or PLAN_FIXTURE,
        )

    for role in server.AGENTS:
        agent_client.call_agent(role, _state())

    assert reached == list(server.AGENTS), (
        f"reached {reached}, expected every role in {list(server.AGENTS)}"
    )


# --------------------------------------------------------------------------
# An unknown role
# --------------------------------------------------------------------------

def test_an_unknown_role_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="bandit"):
        agent_client.call_agent("bandit", _state())


def test_an_unknown_role_raises_in_remote_mode_too(monkeypatch):
    """The role check must sit BEFORE the branch, not inside the local one.

    Otherwise a typo'd role in remote mode goes to ARN resolution and fails as
    "no runtime named theagentorg_bandit" -- an infrastructure error for what is
    a caller's spelling mistake, and one that costs a network round trip to
    discover. The autouse guard is what proves no client was constructed: if the
    check moved below the branch, this test would fail by naming the guard
    instead of the role.
    """
    _wire(monkeypatch, _StubClient())
    with pytest.raises(ValueError, match="bandit"):
        agent_client.call_agent("bandit", _state())


def test_the_unknown_role_error_names_the_valid_ones():
    """A bare "unknown role" tells a reader nothing they can act on."""
    with pytest.raises(ValueError) as exc:
        agent_client.call_agent("securty", _state())
    message = str(exc.value)
    for role in server.AGENTS:
        assert role in message, f"the error does not name the valid role {role!r}: {message}"


# --------------------------------------------------------------------------
# Remote mode -- the invoke
# --------------------------------------------------------------------------

def test_remote_mode_passes_the_DEFAULT_qualifier(monkeypatch):
    """RED step (a). Without qualifier="DEFAULT" the real call 404s.

    Measured and recorded in this plan's shared context: `invoke_agent_runtime`
    fails ResourceNotFoundException without it, even against a READY runtime
    with a READY endpoint. A stub cannot reproduce that service behaviour, so
    what is asserted is the argument -- which is the part the code can get
    wrong.
    """
    client = _wire(monkeypatch, _StubClient(envelope=_envelope("planner", PLAN_FIXTURE)))

    agent_client.call_agent("planner", _state())

    assert client.invocations, "remote mode never called invoke_agent_runtime"
    assert client.invocations[0].get("qualifier") == "DEFAULT", (
        "invoke_agent_runtime was called without qualifier='DEFAULT'; against a "
        "real runtime that is ResourceNotFoundException. Got kwargs: "
        f"{sorted(client.invocations[0])}"
    )


def test_remote_mode_sends_raw_json_bytes_not_base64(monkeypatch):
    """boto3 takes bytes here; the CLI takes base64. Do not mix the two.

    The load-bearing assertion is that the payload PARSES as JSON and carries
    this run's ticket id. `assert "payload" in kwargs` would pass against a
    base64 blob, an empty byte string, and a pickled object alike.
    """
    client = _wire(monkeypatch, _StubClient(envelope=_envelope("planner", PLAN_FIXTURE)))

    state = _state()
    agent_client.call_agent("planner", state)

    payload = client.invocations[0]["payload"]
    assert isinstance(payload, bytes), f"payload must be bytes, got {type(payload).__name__}"

    sent = json.loads(payload.decode("utf-8"))
    assert sent["ticket_id"] == state.ticket_id
    assert sent["run_id"] == state.run_id, "the payload is not THIS run's state"

    # And prove it is not base64 of the same JSON, which is what copying the
    # CLI's encoding into boto3 code produces. Round-tripping it is the only
    # check that tells the two apart -- both are bytes, and both are non-empty.
    assert base64.b64encode(json.dumps(sent).encode()) != payload, (
        "the payload is base64-encoded; boto3 wants raw bytes"
    )
    assert client.invocations[0].get("contentType") == "application/json"


def test_remote_mode_returns_the_agents_own_result(monkeypatch):
    """The happy path: the envelope's `result` becomes a validated model."""
    client = _wire(monkeypatch, _StubClient(envelope=_envelope("planner", PLAN_FIXTURE)))

    result = agent_client.call_agent("planner", _state())

    assert isinstance(result, PlanResult), f"got {type(result).__name__}"
    assert result == PLAN_FIXTURE
    assert client.invocations, "nothing was invoked; this result came from somewhere else"


# --------------------------------------------------------------------------
# Remote mode -- the answers that must NOT be accepted
# --------------------------------------------------------------------------

def test_remote_mode_raises_rather_than_returning_an_empty_result(monkeypatch):
    """RED step (b). A 200 with an empty result is the reassuring non-answer.

    `{"result": {}}` is what a runtime serving a half-deployed image, or a
    handler that caught its own exception, would return. An explicit check
    rather than leaving it to pydantic -- see
    test_an_empty_result_is_refused_for_every_role for why the distinction
    matters.
    """
    _wire(monkeypatch, _StubClient(envelope={"agent": "planner", "result": {}}))

    with pytest.raises(RuntimeError, match="empty"):
        agent_client.call_agent("planner", _state())


def test_an_empty_result_is_refused_for_every_role(monkeypatch):
    """The explicit check, not pydantic, must be what refuses `{}`.

    Every one of the five result models happens to have at least one required
    field today, so pydantic alone would refuse `{}` for all of them. That is an
    accident of the current contract, not a property of it: state.py's rule is
    that fields may be ADDED, and giving `verdict` a default would turn an empty
    body into a successful call for that role. This loop is what makes such a
    change fail here rather than on the projector.
    """
    assert server.AGENTS, "server.AGENTS is empty; this test would pin nothing"
    for role in server.AGENTS:
        _wire(monkeypatch, _StubClient(envelope={"agent": role, "result": {}}))
        with pytest.raises(RuntimeError, match="empty"):
            agent_client.call_agent(role, _state())


def test_remote_mode_raises_on_a_500(monkeypatch):
    """server.py returns 500 with {"error", "detail"} when an agent raises.

    Swallowing that into a fixture, or into an empty result, would put a green
    verdict on the projector for a check that crashed.
    """
    _wire(monkeypatch, _StubClient(
        status=500,
        envelope={"error": "FileNotFoundError", "detail": "gitleaks not on PATH"},
    ))

    with pytest.raises(RuntimeError) as exc:
        agent_client.call_agent("security", _state())

    message = str(exc.value)
    assert "500" in message, f"the error does not name the status: {message}"
    assert "FileNotFoundError" in message, (
        f"the runtime's own error was dropped, leaving nothing to debug: {message}"
    )


def test_remote_mode_raises_on_a_500_even_when_the_body_looks_valid(monkeypatch):
    """The status is checked, not just the body's shape.

    A runtime that 500s while returning a well-formed envelope -- a proxy error
    page, a retry wrapper, a half-written response -- must not be read as a
    result. This is the mutation "return a 500 and swallow it": deleting the
    status check leaves the other 500 test passing, because that one's body has
    no `result` key and would be refused as empty anyway.
    """
    _wire(monkeypatch, _StubClient(status=500, envelope=_envelope("planner", PLAN_FIXTURE)))

    with pytest.raises(RuntimeError, match="500"):
        agent_client.call_agent("planner", _state())


def test_a_result_of_the_wrong_type_is_refused(monkeypatch):
    """A planner call must not accept a DevResult.

    server.py's docstring names invoking the wrong runtime as the most likely
    failure of a five-runtime deploy. Per-role validation is what catches it:
    without it, `state.plan` would hold a DevResult and the failure would
    surface three stages later as an AttributeError on `.tasks`.
    """
    dev = DevResult(branch="b", diff="d", summary="s", files_changed=["f"])
    _wire(monkeypatch, _StubClient(envelope={"agent": "planner",
                                             "result": dev.model_dump(mode="json")}))

    # (ValidationError, RuntimeError), not a bare Exception: what matters is that
    # the call did not RETURN, and either refusal is correct -- pydantic's, or an
    # explicit one if a future change wraps it. A bare `Exception` here would
    # also be satisfied by an AttributeError or a TypeError from a typo in this
    # module, which is not the property under test.
    with pytest.raises((ValidationError, RuntimeError)) as exc:
        agent_client.call_agent("planner", _state())

    # The message must name what was EXPECTED, not merely that something failed:
    # "3 validation errors for PlanResult" sends a reader to the right runtime,
    # while a bare "validation error" sends them to the payload.
    assert "PlanResult" in str(exc.value) or "tasks" in str(exc.value), (
        f"the failure does not say what was expected: {exc.value}"
    )


def test_a_body_that_is_not_the_envelope_is_refused(monkeypatch):
    """A JSON list, string or number is not `{"agent", "result"}`.

    A proxy error page or a runtime serving a different image answers this way.
    The check sits BEFORE the status branch, because that branch reads `error`
    and `detail` off the body -- reversed, a non-object 500 raises AttributeError
    from a .get() and blames the wrong layer. Both orders are exercised here:
    the 200 case and the 500 case.
    """
    for status in (200, 500):
        client = _wire(monkeypatch, _StubClient(status=status, envelope=["not", "an", "envelope"]))
        with pytest.raises(RuntimeError, match="envelope"):
            agent_client.call_agent("planner", _state())
        assert client.invocations, "nothing was invoked; the test proved nothing"


def test_an_empty_body_is_refused(monkeypatch):
    """A zero-byte 200 is the emptiest possible non-answer.

    It parses to `{}`, which has no `agent` key -- so the wrong-runtime check is
    what catches it first. Asserted so that a change to either check cannot let a
    blank response through.
    """
    client = _wire(monkeypatch, _StubClient(envelope=None))
    # json.dumps(None) is "null", which parses to None -- not a dict.
    with pytest.raises(RuntimeError):
        agent_client.call_agent("planner", _state())
    assert client.invocations


def test_a_result_from_the_wrong_runtime_is_refused(monkeypatch):
    """The envelope echoes `agent`; a mismatch means the wrong runtime answered.

    Cheap to check and impossible to infer later. Without it, a deploy that
    pointed two roles at one image would render as five successful agents.
    """
    _wire(monkeypatch, _StubClient(envelope=_envelope("reviewer", PLAN_FIXTURE)))

    with pytest.raises(RuntimeError) as exc:
        agent_client.call_agent("planner", _state())

    message = str(exc.value)
    assert "planner" in message and "reviewer" in message, (
        f"the error does not name both roles, so it cannot be diagnosed: {message}"
    )


# --------------------------------------------------------------------------
# Remote mode -- ARN resolution
# --------------------------------------------------------------------------

def test_the_arn_is_resolved_from_the_control_plane(monkeypatch):
    """The ARN comes from list_agent_runtimes, not from a constructed string.

    A hand-built ARN needs the account id, which nothing in this process knows
    reliably, and an ARN that is merely well-formed fails at invoke time rather
    than at lookup time.
    """
    client = _wire(monkeypatch, _StubClient(envelope=_envelope("planner", PLAN_FIXTURE)))

    agent_client.call_agent("planner", _state())

    assert client.paginators_asked_for == ["list_agent_runtimes"], (
        f"asked for paginators {client.paginators_asked_for}"
    )
    arn = client.invocations[0]["agentRuntimeArn"]
    assert arn.endswith("theagentorg_planner"), f"resolved the wrong ARN: {arn}"


def test_the_arn_is_matched_by_exact_name_not_substring(monkeypatch):
    """`theagentorg_review` must not satisfy a call for `reviewer`.

    github_ops:524 already records this ruling for deploy_note ("a runtime
    called theagentorg_planner_v2 must not be able to satisfy
    theagentorg_planner"). The same accident here would silently invoke a
    different agent and validate its answer against the wrong model -- or, if
    the shapes happened to overlap, accept it.
    """
    decoys = [_runtime("theagentorg_review"), _runtime("theagentorg_reviewer_v2")]
    client = _wire(monkeypatch, _StubClient(pages=[_page(*decoys)]))

    with pytest.raises(RuntimeError, match="theagentorg_reviewer"):
        agent_client.call_agent("reviewer", _state())

    assert not client.invocations, (
        "a decoy runtime was invoked: "
        f"{[i.get('agentRuntimeArn') for i in client.invocations]}"
    )


def test_a_missing_runtime_raises_rather_than_invoking_anything(monkeypatch):
    """No runtime, no guess. The error must name what was looked for."""
    client = _wire(monkeypatch, _StubClient(pages=[_page()]))

    with pytest.raises(RuntimeError) as exc:
        agent_client.call_agent("sre", _state())

    assert "theagentorg_sre" in str(exc.value)
    assert not client.invocations, "invoked something despite resolving no ARN"


def test_the_lookup_is_paginated(monkeypatch):
    """Five runtimes share an account with three other projects' resources.

    A bare list_agent_runtimes() returns one page, and one page is not a promise
    of the whole set -- so a runtime that exists would render as missing. The
    decoys sit on page one and the real runtime on page two, which a
    single-page lookup cannot find.
    """
    pages = [
        _page(_runtime("rosettacloud_something"), _runtime("theagentorg_planner")),
        _page(_runtime("theagentorg_sre")),
    ]
    client = _wire(monkeypatch, _StubClient(
        pages=pages,
        envelope={"agent": "sre", "result": {"verdict": "go", "ci_status": "passing"}},
    ))

    agent_client.call_agent("sre", _state())

    arn = client.invocations[0]["agentRuntimeArn"]
    assert arn.endswith("theagentorg_sre"), f"page two was not read: {arn}"


def test_the_runtime_names_match_the_set_the_repo_deploys():
    """The names this module builds must be the names deploy.yml creates.

    deploy.yml:218 builds `theagentorg_${agent}` and github_ops.RUNTIME_NAMES
    restates it for deploy_note. This is the third place that has to agree, so
    the agreement is asserted rather than assumed: a rename in one place with
    the suite still green is how the two ECR/AgentCore namespaces already
    diverged once.
    """
    built = {agent_client._runtime_name(role) for role in server.AGENTS}
    assert built == set(github_ops.RUNTIME_NAMES), (
        f"agent_client builds {sorted(built)}, github_ops deploys "
        f"{sorted(github_ops.RUNTIME_NAMES)}"
    )


# --------------------------------------------------------------------------
# Remote mode -- kwargs cannot ride in the function call, so they ride in
# the payload
# --------------------------------------------------------------------------

def test_a_kwarg_is_folded_into_the_payload_state(monkeypatch):
    """`poisoned=True` must reach the container, which takes no kwargs.

    server.py:164 calls `AGENTS[role].run(state)` -- no kwargs, and it has no
    way to pass one. So the only channel is the state itself. A seam that
    accepted `poisoned=True` and sent the unmodified state would run the
    poisoned demo against a CLEAN diff and promote it.
    """
    client = _wire(monkeypatch, _StubClient(envelope=_envelope(
        "developer", DevResult(branch="b", diff="d", summary="s", files_changed=["f"]),
    )))

    agent_client.call_agent("developer", _state(), poisoned=True)

    sent = json.loads(client.invocations[0]["payload"].decode("utf-8"))
    assert sent["poisoned"] is True, (
        f"poisoned did not reach the payload; sent keys: {sorted(sent)}"
    )


def test_folding_a_kwarg_does_not_mutate_the_caller_s_state(monkeypatch):
    """The fold must happen on a copy.

    graph.py hands the SAME RunState to every stage and saves it at the end. If
    the remote branch stamped `poisoned=True` onto it, that value would persist
    into the security and sre calls and into runs/<run_id>.state.json -- turning
    a per-call argument into run-wide state nobody set.
    """
    _wire(monkeypatch, _StubClient(envelope=_envelope(
        "developer", DevResult(branch="b", diff="d", summary="s", files_changed=["f"]),
    )))

    state = _state()
    assert state.poisoned is False, "precondition: the fixture state is not poisoned"

    agent_client.call_agent("developer", state, poisoned=True)

    assert state.poisoned is False, (
        "the caller's state was mutated; poisoned is now run-wide state"
    )


def test_a_kwarg_the_remote_branch_cannot_send_raises(monkeypatch):
    """It must not silently drop what it cannot deliver.

    `security.run(state, use_real_scanners=False)` is a real signature this repo
    has. It is NOT a RunState field, so remote mode has no channel for it. Doing
    the call anyway would run the security agent with real scanners while the
    caller believed they were off -- an argument accepted and ignored, which is
    the same class of defect as a check that did not run.
    """
    client = _wire(monkeypatch, _StubClient(envelope=_UNREACHED_ENVELOPE))

    with pytest.raises(ValueError, match="use_real_scanners"):
        agent_client.call_agent("security", _state(), use_real_scanners=False)

    assert not client.invocations, "invoked the runtime despite dropping an argument"


def test_a_folded_kwarg_is_validated(monkeypatch):
    """`poisoned="yes"` is not a bool, and must not be sent as one.

    ValidationError specifically: the fold goes back through
    RunState.model_validate rather than model_copy(update=...) precisely so this
    fails HERE, on this machine, instead of inside a container as a 422 nobody
    reads. `pytest.raises(Exception)` would also pass if the fold crashed with a
    KeyError, which would not be the same thing at all.
    """
    client = _wire(monkeypatch, _StubClient(envelope=_UNREACHED_ENVELOPE))

    with pytest.raises(ValidationError, match="poisoned"):
        agent_client.call_agent("developer", _state(), poisoned="not-a-bool")

    assert not client.invocations, "sent an invalid state to the runtime"


# --------------------------------------------------------------------------
# Step 4: developer reads `poisoned` off the state when the kwarg is absent
# --------------------------------------------------------------------------

def test_developer_reads_poisoned_from_the_state_when_the_kwarg_is_absent():
    """This is what makes the remote developer call possible at all.

    The fixture path is the one under test: with LLM_DISABLED (conftest's
    default) developer.run falls back to fixtures_loader.dev(poisoned=...), so
    the AWS key in the diff is the observable difference between the two.
    """
    poisoned = developer.run(_state(poisoned=True))
    clean = developer.run(_state(poisoned=False))

    assert "AKIA" in poisoned.diff, (
        "state.poisoned=True did not produce the poisoned diff; the scanners "
        "would have nothing to catch"
    )
    assert "AKIA" not in clean.diff, "the clean run picked up a key it should not have"


def test_the_poisoned_kwarg_still_wins_over_the_state_field():
    """graph.py passes the kwarg. It must keep deciding.

    Both directions, because a sentinel implemented as `poisoned or
    state.poisoned` would pass the True case and silently fail the False one --
    making `poisoned=False` unable to override a poisoned state.
    """
    forced_on = developer.run(_state(poisoned=False), poisoned=True)
    assert "AKIA" in forced_on.diff, "the kwarg could not turn poisoning ON"

    forced_off = developer.run(_state(poisoned=True), poisoned=False)
    assert "AKIA" not in forced_off.diff, "the kwarg could not turn poisoning OFF"


def test_poisoned_is_an_addition_to_runstate_not_a_replacement():
    """state.py is frozen for renames and removals; additions are allowed.

    Every pre-existing field is named here explicitly. A rename would break all
    five lanes at once, and the module docstring at state.py:6 is the rule this
    asserts.
    """
    fields = set(RunState.model_fields)
    assert "poisoned" in fields, "the new optional field is missing"
    assert {
        "run_id", "ticket_id", "ticket_text", "started_at",
        "plan", "dev", "review", "security", "sre",
        "decisions", "revision_count", "status",
    } <= fields, f"a pre-existing RunState field was renamed or removed: {sorted(fields)}"
    assert RunState.model_fields["poisoned"].default is False, (
        "poisoned must default False, or every existing run becomes poisoned"
    )


# --------------------------------------------------------------------------
# Step 5: graph.py's five call sites
# --------------------------------------------------------------------------

def test_graph_routes_all_five_stages_through_call_agent(monkeypatch):
    """The whole point: one seam, five stages, no direct `.run` left.

    Driven through run_pipeline rather than read out of the source, so it pins
    BEHAVIOUR -- a call site that still called `planner.run` directly would not
    appear in `seen`. The `assert seen` guard is rule 2: if call_agent were
    never reached this list would be empty and every `in` assertion below would
    be checking nothing.
    """
    from agentorg import graph

    seen = []
    real = agent_client.call_agent

    def _recording(role, state, **kwargs):
        seen.append(role)
        return real(role, state, **kwargs)

    monkeypatch.setattr(graph.agent_client, "call_agent", _recording)
    monkeypatch.setattr(config, "REMOTE_AGENTS", False)

    graph.run_pipeline("SEAM-1", TICKET, poisoned=False, auto_approve=True)

    assert seen, "run_pipeline never reached call_agent; the seam is not wired in"
    for role in server.AGENTS:
        assert role in seen, f"{role} still bypasses call_agent (saw {seen})"


def test_the_local_pipeline_still_blocks_the_poisoned_ticket(monkeypatch):
    """The one behaviour that must be identical to before this module existed.

    Not a tautology check on the seam: this is the demo's second half, and it
    depends on `poisoned=True` surviving the trip through call_agent into
    developer.run.
    """
    from agentorg import graph

    monkeypatch.setattr(config, "REMOTE_AGENTS", False)
    state = graph.run_pipeline("POISON-1", TICKET, poisoned=True, auto_approve=True)

    assert state.status == "blocked", f"the poisoned ticket ended {state.status!r}"
    assert state.security is not None and state.security.verdict == "block"
