"""Shared model invocation for every agent. OWNER: Sorour.

One rule governs this module: an agent must never crash the pipeline because
a model was slow, absent, or chatty. `structured()` and `text()` return None
on any failure, and the caller falls back to its fixture. That is what keeps
`pytest -q` green in CI, which has no AWS credentials.

Both handlers below catch `Exception` on purpose. The failure set spans boto3,
botocore, strands and the network, and any one of them escaping would take the
run down — the exact outcome this module exists to prevent. They log instead of
swallowing silently, so a run that quietly used its fixture can still be
explained afterwards. The logger is fetched inline rather than bound to a
module-level `_log`: ruff's BLE001 only recognises the `logging.getLogger(...)`
form at the call site, and a module-level alias turns `ruff check agentorg` red.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ValidationError

from . import config
from .model import create_model

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


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
        return fenced.group(1).strip()
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
