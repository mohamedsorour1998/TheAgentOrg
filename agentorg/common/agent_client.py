"""The one seam between an agent call and where that agent runs. OWNER: Sorour.

graph.py used to call `planner.run(state)` -- an in-process Python call. With the
five agents deployed to Bedrock AgentCore, the same line has to be able to mean
"POST the state to a container and validate what comes back". This module is the
only place that decides which, and `call_agent` is the only entry point:

    state.plan = call_agent("planner", state)

WHY A SEAM AND NOT A SWITCH IN graph.py. Five call sites, each needing the same
branch, is five chances to get it wrong and five places to fix when the remote
contract moves. More importantly, graph.py is the file that encodes the
pipeline's RULES -- the block rule's precedence, the revision cap, the three
gates -- and none of that changes because an agent moved into a container. It
should not have to be re-read to find out whether it did.

THE LOCAL BRANCH IS THE DEFAULT AND MUST STAY BYTE-FOR-BYTE HONEST.
config.REMOTE_AGENTS is false unless set, so `call_agent(role, state, **kwargs)`
resolves to `AGENTS[role].run(state, **kwargs)` -- the same call, on the same
object, returning the same instance. Not a copy, not a re-validated round trip:
the agents MUTATE nothing on the state, but graph.py assigns their results onto
it, and a seam that re-validated in the middle would quietly change identity
semantics the suite depends on. This branch is also the demo's fallback, which is
the other reason it does not get to be the interesting one.

WHAT THE REMOTE BRANCH REFUSES, AND WHY EACH REFUSAL IS HERE. Every one of these
is a way a remote call can look successful while answering nothing -- the exact
defect class this project exists to prevent:

  * A 200 with an empty `result`. Refused. agents/server.py's docstring already
    names it: "turning it into a 200 with an empty result would recreate this
    project's signature defect: a green response meaning 'the check did not
    run'". A SecurityResult validated out of `{}` would be a verdict nobody
    computed.
  * A non-200. Refused, with the runtime's own error text carried into the
    exception, because "the security agent crashed" and "the security agent
    passed" must not both render as a green stage.
  * A result of the wrong TYPE. Refused per role. server.py names invoking the
    wrong runtime as the likeliest failure of a five-runtime deploy, and a
    DevResult landing in `state.plan` would surface three stages later as an
    AttributeError on `.tasks`.
  * A result from the wrong runtime. server.py echoes `agent` in the envelope
    for exactly this; a mismatch means the ARN resolved to something else.
  * A runtime name matched by SUBSTRING. Refused -- exact set membership only.
    github_ops:525-526 records the same ruling for deploy_note: "a runtime called
    theagentorg_planner_v2 must not be able to satisfy theagentorg_planner".

TWO CLIENTS, NOT ONE. `invoke_agent_runtime` is on the DATA plane
(`bedrock-agentcore`); `list_agent_runtimes`, which resolves a name to an ARN, is
on the CONTROL plane (`bedrock-agentcore-control`). They are different botocore
service models and neither has the other's operation.

Both are constructed LAZILY, inside their functions, following
github_ops._agentcore_client and llm.available(): a module-level `import boto3`
would make every `import agentorg.graph` -- including CI's, which never makes an
AWS call -- pay for botocore's import.

TRAPS ALREADY PAID FOR ELSEWHERE IN THIS REPO, encoded here so they are not
rediscovered:

  * `qualifier="DEFAULT"` is REQUIRED. Without it the call fails
    ResourceNotFoundException even against a READY runtime with a READY
    endpoint. It is not optional-with-a-sensible-default; it is measured.
  * The payload is RAW JSON BYTES. The AWS CLI's `--payload` wants base64;
    boto3 wants bytes. Two interfaces to one API -- copying the CLI's encoding
    into boto3 code sends a base64 string as the body and the container fails to
    parse it.
  * ARNs are READ from the response field, never assembled and never scraped out
    of `--output text`, which appends a literal `None` line and cost this repo
    two failed deploy runs.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from ..agents.server import AGENTS
from ..state import RunState
from . import config, llm

# Bounds on the two calls this module makes. botocore's defaults are
# connect_timeout=60, read_timeout=60 with legacy retries; those are wrong in
# both directions here.
#
# The CONTROL-plane lookup is a metadata read that either answers in
# milliseconds or is not going to, so it gets github_ops' bounds (3s/5s) --
# during a judged demo, a stalled ARN lookup is a frozen projector.
#
# The DATA-plane invoke is the opposite shape: it runs a real agent, which makes
# a Bedrock model call inside the container. 5s would time out every honest
# invocation. 300s is the ceiling, chosen against what the local path already
# tolerates -- the full suite with all three real scanners on PATH takes roughly
# 100-150s, and the security agent is the slowest of the five.
#
# A RANGE, NOT A POINT, and the range is the honest form. Three runs of the same
# 793 tests on 2026-08-21 gave 102.83s, 116.88s and 149.68s; the spread is machine
# load, not test count (load average was 3.6 during the slowest, with other work
# running). An earlier form of this comment said ~173s, inherited from config.py
# rather than measured here. Quoting any single figure as "the" cost would be the
# same defect in a newer coat. The ceiling is deliberately far
# above either number: it bounds a HUNG call, and a ceiling that tripped on an
# honest slow invocation would be a self-inflicted failure on a projector.
#
# retries={"max_attempts": 0} on BOTH, deliberately. An agent invocation is not
# idempotent: it writes a PR comment and burns model tokens, so a silent botocore
# retry of a call that actually succeeded would double both. A failure here is
# raised for the caller to see, not smoothed over.
CONTROL_CONNECT_TIMEOUT = 3
CONTROL_READ_TIMEOUT = 5
INVOKE_CONNECT_TIMEOUT = 10
INVOKE_READ_TIMEOUT = 300

# AgentCore runtime names, which are `theagentorg_<role>` -- see
# .github/workflows/deploy.yml:218, which creates them, and
# github_ops.RUNTIME_NAMES, which lists them for deploy_note. UNDERSCORED: this
# is the AgentCore runtime namespace, not the HYPHENATED ECR repository
# namespace, and neither can be derived from the other.
RUNTIME_PREFIX = "theagentorg_"

# The qualifier that makes invoke_agent_runtime work. A constant with this
# comment attached rather than a bare string at the call site, because the one
# thing a future reader must not conclude is that it looks removable.
DEFAULT_QUALIFIER = "DEFAULT"

# Per-call arguments that can travel to a container, mapped to the RunState field
# that carries them. The state IS the remote payload, so an argument that is not
# a field has no channel -- and `security.run`'s `use_real_scanners` is exactly
# such an argument. Accepting it and dropping it would run the security agent
# with real scanners while the caller believed they were off, which is the same
# defect as a check that did not run, so the remote branch raises instead.
_KWARGS_CARRIED_ON_THE_STATE = {"poisoned": "poisoned"}


def _runtime_name(role: str) -> str:
    """The AgentCore runtime name for a role. One definition, three readers."""
    return f"{RUNTIME_PREFIX}{role}"


def _result_type(role: str) -> type[BaseModel]:
    """The model `role`'s runtime must return.

    Read off the agent's own `run` annotation rather than restated as a sixth
    role->type dict. A dict here would be a second declaration of a fact
    agents/*.py already own, free to drift the moment one of them changes its
    return type -- and drift in THIS direction is silent, because a wrong
    expected type shows up as a validation error blamed on the runtime.
    """
    import typing

    return typing.get_type_hints(AGENTS[role].run)["return"]


def _agentcore_control_client():
    """Bounded bedrock-agentcore-control client. A seam the tests replace.

    Lazy import, as in github_ops._agentcore_client and llm.available().
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore-control",
        region_name=config.AWS_REGION,
        config=Config(
            connect_timeout=CONTROL_CONNECT_TIMEOUT,
            read_timeout=CONTROL_READ_TIMEOUT,
            retries={"max_attempts": 0},
        ),
    )


def _agentcore_data_client():
    """Bounded bedrock-agentcore client -- the DATA plane. A seam the tests replace.

    A different service model from the control client above: this one has
    invoke_agent_runtime and no list_agent_runtimes, and that one is the reverse.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore",
        region_name=config.AWS_REGION,
        config=Config(
            connect_timeout=INVOKE_CONNECT_TIMEOUT,
            read_timeout=INVOKE_READ_TIMEOUT,
            retries={"max_attempts": 0},
        ),
    )


def _resolve_arn(role: str) -> str:
    """The deployed runtime's ARN, read from the control plane.

    READ, not constructed. Assembling
    `arn:aws:bedrock-agentcore:<region>:<account>:runtime/<name>` needs an
    account id this process has no reliable way to know, and an ARN that is
    merely well-formed fails at invoke time -- a network round trip later, with
    a ResourceNotFoundException that looks identical to the missing-qualifier
    trap.

    Paginated, for the reason github_ops:503 gives: the five runtimes share
    account 339712964409 with three other projects' resources, so one page is
    not a promise of the whole set. A runtime that exists on page two would
    otherwise render as absent.

    EXACT name match, per the ruling at github_ops:525-526.
    `theagentorg_review` must not satisfy a call for `reviewer`, and
    `theagentorg_planner_v2` must not satisfy `planner`.
    """
    wanted = _runtime_name(role)
    client = _agentcore_control_client()

    for page in client.get_paginator("list_agent_runtimes").paginate():
        for runtime in page.get("agentRuntimes", []):
            if runtime.get("agentRuntimeName") == wanted:
                arn = runtime.get("agentRuntimeArn")
                if not arn:
                    # Named but ARN-less. Raised rather than skipped: silently
                    # continuing would report the runtime as missing, sending a
                    # reader to look for a deploy that already happened.
                    raise RuntimeError(
                        f"runtime {wanted!r} exists but the control plane returned no "
                        f"agentRuntimeArn for it: {runtime!r}"
                    )
                return arn

    raise RuntimeError(
        f"no AgentCore runtime named {wanted!r} in {config.AWS_REGION}. "
        f"Deploy it with .github/workflows/deploy.yml, or unset REMOTE_AGENTS to "
        f"run {role!r} in this process."
    )


def _remote_state(role: str, state: RunState, kwargs: dict) -> RunState:
    """The state to send, with any per-call kwargs folded into it.

    A COPY. graph.py hands one RunState to every stage and saves it at the end,
    so stamping `poisoned=True` onto it for the developer call would leave that
    value set for the security and sre calls and write it into
    runs/<run_id>.state.json -- turning a single call's argument into run-wide
    state nobody set.

    `model_copy(update=...)` would skip validation, so the copy goes back through
    the model: `poisoned="yes"` must fail here, on this machine, rather than
    inside a container as a 422 nobody sees.
    """
    unsupported = sorted(set(kwargs) - set(_KWARGS_CARRIED_ON_THE_STATE))
    if unsupported:
        raise ValueError(
            f"cannot send {', '.join(unsupported)} to the {role!r} runtime: "
            f"agents/server.py invokes run(state) with no keyword arguments, so "
            f"only arguments carried on the RunState can cross the wire "
            f"({', '.join(sorted(_KWARGS_CARRIED_ON_THE_STATE))}). Accepting and "
            f"dropping it would run the agent with settings the caller did not ask "
            f"for."
        )

    if not kwargs:
        return state

    fields = state.model_dump()
    for name, value in kwargs.items():
        fields[_KWARGS_CARRIED_ON_THE_STATE[name]] = value
    return RunState.model_validate(fields)


def _classify_invoke_failure(role: str, arn: str, exc: Exception) -> str:
    """Say WHICH failure this was, in words that name the next action.

    Every caller of this raises; nothing here recovers. It exists because
    `call_agent` deliberately has no fallback -- a failed remote call must not
    quietly become a local one -- so the exception message is the entire diagnosis
    a demo operator gets, and "botocore.errorfactory.AccessDeniedException" is not
    one.

    Three conditions are separated, because each has a different fix and they are
    otherwise indistinguishable in the raw exception:

      * TIMED OUT -- the runtime accepted the call and did not answer inside
        INVOKE_READ_TIMEOUT. The agent may still be running. NOT a denial, and
        not a missing runtime.
      * DENIED / NOT FOUND -- an IAM or deploy problem, answered immediately.
        Waiting cannot fix it, which is exactly what a caller must not do.
      * anything else -- named as unclassified rather than forced into one of the
        two above. A classifier that guesses is worse than one that admits it did
        not recognise the error, because the guess is what makes a caller wait out
        a condition that will never clear.

    ClientError is NOT a subclass of BotoCoreError -- verified against botocore
    1.43.75, whose mro puts ClientError directly under Exception -- so a single
    `except BotoCoreError` would let every AccessDenied through unclassified.
    """
    from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

    where = f"{role!r} runtime ({arn})"

    if isinstance(exc, (ReadTimeoutError, ConnectTimeoutError)):
        kind = "read" if isinstance(exc, ReadTimeoutError) else "connect"
        limit = INVOKE_READ_TIMEOUT if isinstance(exc, ReadTimeoutError) else INVOKE_CONNECT_TIMEOUT
        return (
            f"the {where} TIMED OUT after the {kind} ceiling of {limit}s. The agent "
            f"may still be running in the container -- this is a timeout, NOT a "
            f"permission problem and NOT a missing runtime, so retrying the same "
            f"call is reasonable where raising the ceiling is not. "
            f"({type(exc).__name__}: {exc})"
        )

    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        if code in ("AccessDeniedException", "UnrecognizedClientException"):
            return (
                f"the {where} DENIED the call ({code}): {message}. This is an IAM "
                f"problem and it will not clear on its own -- check that the "
                f"caller may invoke this runtime. Do not wait on it."
            )
        if code == "ResourceNotFoundException":
            return (
                f"the {where} was NOT FOUND ({code}): {message}. The ARN resolved "
                f"from the control plane but the data plane will not serve it -- "
                f"the usual cause is a missing qualifier={DEFAULT_QUALIFIER!r}, "
                f"and the next is an endpoint that has not promoted the current "
                f"version yet."
            )
        if code in ("ThrottlingException", "ServiceQuotaExceededException"):
            return (
                f"the {where} THROTTLED the call ({code}): {message}. A capacity "
                f"limit, not a fault in the payload; this one does clear on its own."
            )
        return (
            f"the {where} refused the call ({code or 'no error code'}): {message}"
        )

    # Deliberately not folded into either branch above. See the docstring.
    return (
        f"the call to the {where} failed with an UNCLASSIFIED error, so it is not "
        f"known whether waiting would help: {type(exc).__name__}: {exc}"
    )


def _invoke(role: str, state: RunState) -> dict:
    """POST the state to `role`'s runtime and hand back the parsed envelope."""
    arn = _resolve_arn(role)

    try:
        response = _agentcore_data_client().invoke_agent_runtime(
            agentRuntimeArn=arn,
            # REQUIRED. Without it this fails ResourceNotFoundException even
            # against a READY runtime with a READY endpoint. Measured, not
            # inferred.
            qualifier=DEFAULT_QUALIFIER,
            # RAW JSON BYTES. boto3 takes bytes here; the CLI takes base64. Same
            # API, different interface -- do not copy the CLI's encoding into
            # boto3 code.
            payload=state.model_dump_json().encode("utf-8"),
            contentType="application/json",
        )
    except Exception as exc:
        # CLASSIFIED, NOT SWALLOWED. Nothing is absorbed here: every branch
        # re-raises. What the classification buys is that "the call timed out"
        # and "the call was denied" stop looking alike -- they are the same rule-4
        # distinction the deploy retry loop got wrong, where an unknown error was
        # treated as the one condition the loop knew how to wait out and it polled
        # for five minutes against a broken value.
        #
        # A raw botocore exception on the projector is a stack trace naming
        # neither the role nor the runtime, and the two failures need opposite
        # responses: a timeout means wait or raise INVOKE_READ_TIMEOUT, a denial
        # means fix the IAM role, and an absent runtime means deploy it.
        raise RuntimeError(_classify_invoke_failure(role, arn, exc)) from exc

    # botocore models `response` as a STREAMING blob, so it arrives as a
    # file-like object rather than bytes -- StreamingBody over a socket, BytesIO
    # under a test harness. Read it before anything else touches the status, so
    # a non-200's own error text is available to put in the exception.
    body = response.get("response")
    raw = body.read() if hasattr(body, "read") else (body or b"")

    # `or 200`: statusCode is modelled as optional, and a missing status is not
    # evidence of failure. The checks below do not depend on it being present.
    status = response.get("statusCode") or 200

    if not raw:
        # A ZERO-BYTE BODY IS NOT AN EMPTY OBJECT. Parsing `b""` as `{}` -- which
        # is what `json.loads(...) if raw else {}` did here -- makes a blank
        # response indistinguishable from a runtime that answered `{}`, and it
        # sends the reader to the wrong place: `{}` has no `agent` key, so the
        # echo check downstream reported "asked the 'planner' runtime and None
        # answered ... check AGENT_ROLE on it". That is rule 4 -- a message that
        # cannot tell "nothing came back" from "the wrong agent came back", and
        # it points at runtime configuration for what is an empty response.
        #
        # Refused here, by name, because this is the emptiest possible form of
        # the reassuring non-answer this module exists to eliminate.
        raise RuntimeError(
            f"the {role!r} runtime answered {status} with a ZERO-BYTE body. "
            f"Nothing came back to validate -- this is not an empty result, it is "
            f"no result. Check the runtime's own logs: agents/server.py always "
            f"writes a {{'agent', 'result'}} envelope, so an empty body means the "
            f"container died mid-response or something other than it answered."
        )

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # NAMES THE REAL CONDITION. The realistic cause is not a broken agent but
        # something in front of it -- an HTML `504 Gateway Timeout` page, an ALB
        # error, an auth redirect. Reporting only "not JSON" would be true and
        # useless; the body's opening bytes are what tell a reader at a glance
        # that they are looking at a gateway error rather than a runtime bug, so
        # a bounded prefix is quoted. Bounded because this string can land on the
        # projector, where a wall of HTML reads as a crash.
        opening = raw[:120].decode("utf-8", errors="replace").strip()
        looks_like_html = opening[:1] == "<"
        hint = (
            " The body opens with '<', so this is very likely an HTML error page "
            "from a proxy or load balancer IN FRONT OF the runtime, not a reply "
            "from agents/server.py."
            if looks_like_html else ""
        )
        raise RuntimeError(
            f"the {role!r} runtime answered {status} with a body that is not JSON "
            f"({exc}).{hint} First {len(opening)} bytes: {opening!r}"
        ) from exc

    # SHAPE BEFORE STATUS, because the status branch below reads `error` and
    # `detail` off this object. server.py always sends a JSON OBJECT; a list,
    # string or number means something other than our handler answered -- a
    # proxy error page, or a runtime serving a different image. Checked here so
    # that case reports itself, rather than raising AttributeError from a .get()
    # three lines later and blaming the wrong thing.
    #
    # RuntimeError, not TypeError, for the reason every other refusal in this
    # module is one: nothing about our types is wrong, a remote service answered
    # something we cannot trust, and call_agent's contract says so.
    envelope = parsed if isinstance(parsed, dict) else None
    if envelope is None:
        raise RuntimeError(
            f"the {role!r} runtime returned a JSON {type(parsed).__name__} "
            f"(status {status}), not the {{'agent', 'result'}} envelope "
            f"agents/server.py sends"
        )

    if status != 200:
        # server.py answers a raising agent with 500 and {"error", "detail"}.
        # Both are carried into the message: without them the caller learns only
        # that something failed, and the container's logs are the only place the
        # cause exists.
        detail = envelope.get("detail") or envelope.get("error") or raw[:200]
        raise RuntimeError(
            f"the {role!r} runtime answered {status}: "
            f"{envelope.get('error', 'no error field')} -- {detail}"
        )

    return envelope


def _validate(role: str, envelope: dict) -> BaseModel:
    """Turn a 200 envelope into the result type `role` is supposed to produce."""
    answered = envelope.get("agent")
    if answered != role:
        # server.py echoes the role for exactly this. A deploy that pointed two
        # roles at one image would otherwise render as five successful agents.
        raise RuntimeError(
            f"asked the {role!r} runtime and {answered!r} answered. The ARN for "
            f"{_runtime_name(role)} resolved to a different agent's runtime; check "
            f"AGENT_ROLE on it."
        )

    result = envelope.get("result")
    if not result:
        # THE REASSURING NON-ANSWER. `{}`, None and a missing key all land here.
        # A runtime serving a half-deployed image, or a handler that caught its
        # own exception, returns exactly this -- and for any result model whose
        # fields all have defaults it would validate into a verdict nobody
        # computed. Refused explicitly rather than left to pydantic, so that
        # giving a field a default later cannot turn an empty body into a
        # successful call.
        raise RuntimeError(
            f"the {role!r} runtime answered 200 with an empty result "
            f"({result!r}). A green response meaning 'the agent did not run' is "
            f"the one answer this pipeline must never accept."
        )

    # Validated against the role's OWN declared return type, so a planner call
    # cannot accept a DevResult. pydantic's error names the missing fields, which
    # is what makes a wrong-runtime deploy diagnosable from the exception alone.
    return _result_type(role).model_validate(result)


def call_agent(role: str, state: RunState, **kwargs) -> BaseModel:
    """Run `role`'s agent -- in this process, or in its AgentCore runtime.

    THE SIGNATURE IS A CONTRACT. graph.py's five call sites and Task 3's
    dispatcher both call `call_agent(role, state, **kwargs)`.

    Returns whatever that agent's `run` returns: PlanResult, DevResult,
    ReviewResult, SecurityResult or SREResult.

    Raises ValueError for an unknown role or an argument that cannot cross the
    wire, and RuntimeError for a remote call that could not be trusted. It does
    NOT fall back to the local path when a remote call fails, and that is
    deliberate: a run that silently ran the planner in-process after failing to
    reach the runtime would report success for the thing it did not do.
    """
    if role not in AGENTS:
        # BEFORE the branch, not inside the local one. A typo'd role in remote
        # mode would otherwise reach ARN resolution and fail as "no runtime named
        # theagentorg_bandit" -- an infrastructure error, a network round trip
        # later, for what is a spelling mistake.
        raise ValueError(
            f"unknown agent role {role!r}; expected one of {sorted(AGENTS)}"
        )

    if not config.REMOTE_AGENTS:
        # The default, and the demo's fallback. The same call this replaced, on
        # the same state object, returning the agent's own instance.
        return AGENTS[role].run(state, **kwargs)

    logging.getLogger(__name__).info(
        "invoking %s on AgentCore runtime %s", role, _runtime_name(role)
    )
    payload_state = _remote_state(role, state, kwargs)
    envelope = _invoke(role, payload_state)

    # THE CONTAINER'S PROVENANCE, RECORDED LOCALLY, because the model call happened
    # over there and `llm.last_source()` on this side would otherwise always be
    # None -- which is exactly what the deployed pipeline printed (`_source=none`)
    # while posting a plan comment no fixture could have produced.
    #
    # Recorded BEFORE _validate, so a container that answered honestly and then
    # failed validation still tells us which path it took. That is the case where
    # the provenance matters most: an unusable answer from a real model call and an
    # unusable answer from a broken fixture load want different fixes.
    #
    # An older container omits the key. `.get(...)` yields None, `_record` is
    # skipped, and the run's provenance stays "" -- unknown, which is the honest
    # answer about a container that could not report.
    source = envelope.get("source") if isinstance(envelope, dict) else None
    if source in (llm.SOURCE_MODEL, llm.SOURCE_FIXTURE):
        llm._record(source)

    return _validate(role, envelope)
