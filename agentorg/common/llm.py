"""Shared model invocation for every agent. OWNER: Sorour.

One rule governs this module: an agent must never crash the pipeline because
a model was slow, absent, or chatty. `structured()` and `text()` return None
on any failure, and the caller falls back to its fixture. That is what keeps
`pytest -q` green in CI, which has no AWS credentials.

Both handlers below catch `Exception` on purpose. The failure set spans boto3,
botocore, strands and the network, and any one of them escaping would take the
run down — the exact outcome this module exists to prevent. They log instead of
swallowing silently, so a run that quietly used its fixture can still be
explained afterwards.

The logger is fetched inline rather than bound to a module-level `_log`, and
the precise reason matters because an earlier version of this note got it
wrong. BLE001 is satisfied when the handler contains a logging call ruff can
statically resolve to the logging module AND that call carries the traceback --
`exc_info=True`, or `.exception()`. The LEVEL is irrelevant: `.debug(...,
exc_info=True)` alone is accepted. What ruff cannot resolve is a module-level
alias, so `_log.exception(...)` turns `ruff check agentorg` red while a
handler-local `log = logging.getLogger(__name__)` is fine. Two consequences
worth knowing: a warning with no `exc_info` still fires the rule, and narrowing
the `except` clause satisfies it with no logging at all -- so lint will bless a
narrow clause that silently drops the failure, which is the worse option here.
Measured across 12 ruff variants, not inferred.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ValidationError

from . import config
from .model import create_model

# The language tag is optional AND arbitrary. A model asked for JSON still
# fences its reply as ```python or ```JSON often enough that matching only the
# literal "json" would hand back "python\n{...}" as the payload, fail to parse,
# and show the fixture with no explanation.
_FENCE = re.compile(r"```(?:\w+)?\s*(.*?)```", re.DOTALL)

# WHICH PATH ANSWERED THE MOST RECENT CALL: the model, or a fixture.
#
# Module-level rather than a return value because `text()` and `structured()`
# already use None to mean "no usable answer", and widening either signature
# would change four agents' call sites for a fact only the pipeline layer needs.
#
# Reset explicitly by the caller rather than at the top of every call. A run
# makes several model calls -- five agents, plus the developer again on every
# revision -- and the question the pipeline asks is about the RUN, not about the
# last agent to speak. A per-call reset would make the last writer win, which is
# precisely the reading the asymmetry in `_record` exists to prevent.
_LAST_SOURCE: str | None = None

# The two answers. Named rather than spelled at eight call sites, because a typo
# in one of them would be a third value nothing reads -- and the field that
# reports it is a plain `str`, so nothing would refuse it.
SOURCE_MODEL = "model"
SOURCE_FIXTURE = "fixture"


def reset_source() -> None:
    """Forget which path answered. Call once before a run, not between agents."""
    global _LAST_SOURCE
    _LAST_SOURCE = None


def last_source() -> str | None:
    """`"model"`, `"fixture"`, or None if no call has been made since the reset."""
    return _LAST_SOURCE


def _record(source: str) -> None:
    """Record which path answered. `fixture` NEVER downgrades to `model`.

    THE ASYMMETRY IS THE MECHANISM, not a tie-break. Five agents share this one
    record, so a run where ANY of them fell back is not a model run. Without the
    guard the last agent to answer decides the label, and one successful call
    papers over four denials -- which is the exact shape of the defect this whole
    field exists to surface: on 2026-08-22 every model-calling agent in the
    deployed pipeline was serving fixtures, every job was green, and nothing said
    so.

    It is one-directional, not write-once: `model` still moves to `fixture`. A
    latch on the first value would report the first agent's luck for the whole
    run, which fails in the optimistic direction just as badly.
    """
    global _LAST_SOURCE
    if _LAST_SOURCE == SOURCE_FIXTURE:
        return
    _LAST_SOURCE = source


def record_fixture_fallback() -> None:
    """Called by an agent that is about to load its fixture.

    PUBLIC BECAUSE THE AGENTS NEED IT, and that is not redundant with the
    recording `text()` does internally. Almost every test in this suite -- and
    `tests/test_agent_fallbacks.py` in particular -- monkeypatches
    `llm.structured` rather than `llm._complete`, so on that path none of this
    module's own code runs and nothing would be recorded at all. The agent's
    fallback branch is the one place the fact cannot be stubbed away, because it
    IS the fact: the agent knows it is serving a fixture.

    It is also what makes the label true for a reason `llm` cannot see. `text()`
    returning a usable string is not the same event as the caller USING it: a
    reply that fails the caller's own validation is a fixture run from the
    caller's side, and the caller is the only one who knows.
    """
    _record(SOURCE_FIXTURE)


def available() -> bool:
    """True when a model call is worth attempting. Cheap; makes no network call."""
    if config.LLM_DISABLED:
        return False
    if config.LLM_BASE_URL:
        return bool(config.LLM_API_KEY) and config.LLM_API_KEY != "not-needed"
    try:
        import boto3

        session = boto3.Session(region_name=config.AWS_REGION)
        return session.get_credentials() is not None
    except Exception:
        # Routine on a machine with no AWS setup, so this stays at debug level.
        logging.getLogger(__name__).debug(
            "credential lookup failed; treating the model as unavailable",
            exc_info=True,
        )
        return False


def extract_json(text_in: str) -> str:
    """Pull a JSON object out of a reply that may be fenced or chatty."""
    text_in = text_in.strip()
    fenced = _FENCE.search(text_in)
    if fenced:
        body = fenced.group(1).strip()
        # Only trust the fence if it actually holds an object. A reply that
        # opens with a ```python block and puts the JSON after it would
        # otherwise hand back the code, fail to parse, and show the fixture
        # with nothing on screen to say so. Fall through to the brace scan.
        if "{" in body:
            return body
    start, end = text_in.find("{"), text_in.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text_in[start : end + 1]
    return text_in


def _complete(system_prompt: str, user_prompt: str) -> str:
    """Raw model call. Separated so tests can substitute it."""
    from strands import Agent

    agent = Agent(model=create_model(), system_prompt=system_prompt)
    return str(agent(user_prompt))


def text(system_prompt: str, user_prompt: str) -> str | None:
    """Plain-text reply, or None if the model is unavailable or failed.

    EVERY None RETURN RECORDS `fixture`, and there are four of them. That is not
    bookkeeping: each one sends the caller to its fixture, so each one is a
    fixture run from the only viewpoint that matters. Missing any single branch
    leaves `last_source()` reading None -- which renders as *unknown*, the answer
    this field exists to replace.
    """
    if not available():
        _record(SOURCE_FIXTURE)
        return None
    try:
        reply = _complete(system_prompt, user_prompt)
    except Exception:
        # A model we expected to answer did not. The caller falls back to its
        # fixture either way, so warn rather than raise.
        #
        # THIS IS THE MEASURED PRODUCTION CASE. `bedrock:InvokeModel` was
        # implicitDeny on the inference profile config.BEDROCK_MODEL names, so
        # every call landed here, every agent served its fixture, and the only
        # trace was this line inside a container log nobody reads during a demo.
        _record(SOURCE_FIXTURE)
        logging.getLogger(__name__).warning(
            "model call failed; the caller will fall back to its fixture",
            exc_info=True,
        )
        return None
    if not isinstance(reply, str):
        # `_complete` is the seam tests substitute, so it can return anything;
        # returning None from it is the natural way to simulate "the model gave
        # nothing". Degrade like any other failure instead of raising into the
        # agent. This guard is also what makes the .strip() below unable to
        # raise -- keep the strip on this side of it.
        _record(SOURCE_FIXTURE)
        logging.getLogger(__name__).warning(
            "model returned %s, not a string; the caller falls back to its fixture",
            type(reply).__name__,
        )
        return None
    reply = reply.strip()
    if not reply:
        # SPLIT OUT OF `return reply or None` DELIBERATELY. That one-liner reaches
        # the success case and the empty case through the same statement, so a
        # single `_record(SOURCE_MODEL)` above it would label a model that said
        # nothing usable a model run -- while the caller loaded its fixture.
        _record(SOURCE_FIXTURE)
        return None
    _record(SOURCE_MODEL)
    return reply


def structured[T: BaseModel](
    model_cls: type[T], system_prompt: str, user_prompt: str
) -> T | None:
    """Reply parsed into model_cls, or None if unavailable/unparseable.

    A reply that arrived and then failed to parse or validate records `fixture`,
    OVERWRITING the `model` that `text()` just recorded on the way through. The
    model spoke; the caller is still about to load its fixture, and the caller's
    experience is what this field reports. Recording `model` here would assert
    that the run used model output.
    """
    raw = text(system_prompt, user_prompt)
    if raw is None:
        # text() has already recorded the reason. Not re-recorded here, because
        # that would be a second writer for one event.
        return None
    try:
        return model_cls.model_validate_json(extract_json(raw))
    except (ValidationError, ValueError):
        # BOTH exception types matter and they arrive from different faults:
        # unparseable text raises ValueError, well-formed JSON of the wrong shape
        # raises ValidationError. Recording on only one branch would miss the
        # other, and pydantic's is the one a chatty-but-valid model produces.
        _record(SOURCE_FIXTURE)
        return None
