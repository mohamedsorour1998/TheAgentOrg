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
    """Plain-text reply, or None if the model is unavailable or failed."""
    if not available():
        return None
    try:
        reply = _complete(system_prompt, user_prompt)
    except Exception:
        # A model we expected to answer did not. The caller falls back to its
        # fixture either way, so warn rather than raise.
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
        logging.getLogger(__name__).warning(
            "model returned %s, not a string; the caller falls back to its fixture",
            type(reply).__name__,
        )
        return None
    reply = reply.strip()
    return reply or None


def structured[T: BaseModel](
    model_cls: type[T], system_prompt: str, user_prompt: str
) -> T | None:
    """Reply parsed into model_cls, or None if unavailable/unparseable."""
    raw = text(system_prompt, user_prompt)
    if raw is None:
        return None
    try:
        return model_cls.model_validate_json(extract_json(raw))
    except (ValidationError, ValueError):
        return None
