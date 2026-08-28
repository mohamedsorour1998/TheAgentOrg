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
from dataclasses import dataclass, replace

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
    recording `text()` does internally. Almost every test in this suite --
    and `tests/test_agent_fallbacks.py` in particular -- monkeypatches
    `llm.structured` rather than `llm._complete`, so on that path none of this
    module's own code runs and nothing would be recorded at all. The agent's
    fallback branch is the one place the fact cannot be stubbed away, because it
    IS the fact: the agent knows it is serving a fixture.

    It is also what makes the label true for a reason `llm` cannot see. `text()`
    returning a usable string is not the same event as the caller USING it: a
    reply that fails the caller's own validation is a fixture run from the
    caller's side, and the caller is the only one who knows.

    IT ALSO RECORDS A ZERO-TOKEN USAGE ROW, and that is the whole of E7's
    "a fixture fallback records zero rather than nothing". A stage that fell back
    must not be indistinguishable from a stage nobody measured -- the same
    requirement `scan_provenance` answers for the security verdict. With a row
    present and zero, a reader can tell that the stage ran and spent nothing; with
    no row at all, the honest reading is that nothing was instrumented.

    Recorded here rather than in the five agents because this function is already
    the one call every agent's fallback branch makes, and `agentorg/agents/` is
    another lane's file. Convenient, and also correct: one writer for one event.

    The row is ADDED, never substituted for a real one. When the model genuinely
    answered and the CALLER then rejected the reply, both rows stand -- the tokens
    were really spent and a fixture was really served, and a version that dropped
    either would misreport one of those two facts.
    """
    _record(SOURCE_FIXTURE)
    _record_usage(Usage(fixture=True))


# ── WHAT THE MODEL CALLS CONSUMED ────────────────────────────────────────────
#
# THERE WAS NO USAGE RECORDING AT ALL BEFORE THIS. Measured on the pre-final
# baseline: this module called the model and threw the token counts away on the
# `str(agent(...))` line, so nobody could answer "what did that run cost".
#
# The shape mirrors `_LAST_SOURCE` above ON PURPOSE, and the choice is not
# stylistic. `_complete` is the seam the whole suite substitutes -- conftest.py
# guard 1 replaces it, and dozens of tests hand back a bare string -- so
# widening its `-> str` return type to carry usage would make every one of those
# stubs a shape mismatch. This repository has a named pattern for that: a test
# double that cannot express the new shape produces confidence that cannot be
# falsified. Module state costs those stubs nothing; they simply record no usage,
# which is the honest answer for a stub that never called a model.


@dataclass(frozen=True)
class Usage:
    """One model call's token counts. Frozen, so an accumulator cannot edit history.

    `cached` IS SEPARATE FROM `input` because Bedrock prices it separately -- a
    Nova 2.0 Lite cache read is $0.0825/1M against $0.33/1M fresh, measured from
    the AWS Pricing API (see `agentorg/cost/prices.py`). It is also the number
    that says whether caching is working at all: the five agents each re-send a
    repository snapshot on every call, so a zero across a whole run means the
    largest cost in the design is being paid in full, every time, silently.

    `cached_reported` IS NOT THE SAME QUESTION AS `cached_tokens > 0`, and
    conflating them is how an unmeasured cache would come to read as a measured
    miss. strands' `Usage` TypedDict declares `cacheReadInputTokens` as an OPTIONAL
    key (`strands/types/event_loop.py:8-23`, `total=False`), and its accumulator
    only creates that key `if "cacheReadInputTokens" in source`
    (`strands/telemetry/metrics.py:338-353`) -- so a provider that does not report
    caching leaves the key genuinely ABSENT, not zero. The OpenAI-compatible path
    makes this sharper still: `strands/models/openai.py:585-594` sets the key
    behind `if cached := ...`, so a real zero is falsy and omitted there too.
    Reporting an absent key as `cached_tokens=0` would state that the cache was
    measured and missed, when the truth is that nothing was measured -- the same
    distinction `SecurityResult.scan_provenance` exists for, and the same one
    `CostRecord.usd` draws between "not priced" and "priced, and free".
    """

    stage: str = ""              # filled by the caller; "" when nobody said
    model: str = ""              # the model id that answered
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cached_reported: bool = False   # did the provider report caching AT ALL?
    # A FIXTURE STOOD IN FOR THIS CALL. Zero tokens, and the zero is a measurement
    # rather than an absence -- which is what makes "the stage fell back" different
    # from "nobody instrumented the stage". See `record_fixture_fallback`.
    fixture: bool = False


_USAGE: list[Usage] = []


def reset_usage() -> None:
    """Forget what the model calls consumed. Call once before a run.

    Mirrors `reset_source`, and for the same reason: a laptop can run several
    stages in one process, and a run inheriting the previous run's token counts
    is worse than reporting nothing, because it looks like a measurement.
    """
    _USAGE.clear()


def usage() -> list[Usage]:
    """Every model call since the reset, in order. A COPY, so a caller cannot append.

    A list rather than a running total, because the caller knows something this
    module does not: which stage it is in. The developer/reviewer loop calls the
    model an unknown number of times per stage, and a total computed here could
    not be split back apart afterwards.
    """
    return list(_USAGE)


def _record_usage(entry: Usage) -> None:
    """Append one call's counts. Never replaces -- a stage may call the model twice."""
    _USAGE.append(entry)


def _usage_from_metrics(metrics: object, model_id: str) -> Usage:
    """Read strands' accumulated usage off an AgentResult's metrics.

    DEFENSIVE ON PURPOSE, AND THIS FUNCTION MUST NOT RAISE. It runs inside
    `_complete`, on the pipeline's model path, to record a number nobody's verdict
    depends on. A `getattr` chain that raised here would take down a run over
    bookkeeping -- turning an instrument into an outage, which is strictly worse
    than the missing measurement it replaced.

    `accumulated_usage` is a plain dict with CAMELCASE keys, not an object with
    attributes: strands defines it as a TypedDict carrying Bedrock's own key names
    (`strands/types/event_loop.py`). Read with `.get`, never subscripted -- the two
    cache keys are optional and their absence is the fact `cached_reported` carries.
    """
    counts = getattr(metrics, "accumulated_usage", None)
    if not isinstance(counts, dict):
        # No metrics at all -- a substituted `_complete`, or a strands version that
        # moved the attribute. Recorded as a zero-token call rather than skipped:
        # a call that happened is a call that happened, and dropping it would make
        # the stage row vanish from the cost record entirely.
        return Usage(model=model_id)

    return Usage(
        model=model_id,
        input_tokens=int(counts.get("inputTokens", 0) or 0),
        output_tokens=int(counts.get("outputTokens", 0) or 0),
        cached_tokens=int(counts.get("cacheReadInputTokens", 0) or 0),
        # PRESENCE, not truthiness. See the Usage docstring.
        cached_reported="cacheReadInputTokens" in counts,
    )


def attribute_usage_to(stage: str) -> None:
    """Stamp `stage` onto every call recorded so far that has none.

    THE STAGE IS KNOWN BY THE CALLER AND NOT BY THIS MODULE, which is the whole
    reason this is a separate call rather than a parameter on `text()`. Four agents
    reach the model through `structured()` without ever naming their stage, and
    threading one through would change every agent's call site for a fact only the
    pipeline layer reads -- the same argument that keeps `last_source()` module
    state instead of a return value.

    Only BLANK stages are filled, and that asymmetry is load-bearing: the
    developer/reviewer loop runs both agents repeatedly within one `develop` stage,
    so a version that overwrote every row would relabel earlier calls on every
    later pass. Already-attributed rows are settled history.
    """
    for i, entry in enumerate(_USAGE):
        if not entry.stage:
            _USAGE[i] = replace(entry, stage=stage)


# ── CROSSING THE REMOTE SEAM ─────────────────────────────────────────────────
#
# THE SAME PROBLEM `source` ALREADY SOLVED, ON THE SAME SEAM. Under
# REMOTE_AGENTS=true the model call happens INSIDE an AgentCore container, so the
# token counts exist only over there and `llm.usage()` on the runner is always
# empty -- exactly as `llm.last_source()` was always None before the provenance
# fix, which is how the deployed pipeline came to print `_source=none` beside
# plainly model-written output.
#
# So usage travels back on the 200 response envelope, the way `source` does. This
# is deliberately NOT a second mechanism: `agents/server.py` already builds that
# envelope and `common/agent_client.py` already reads it, and the two functions
# below are the serialise/absorb pair those two call sites need.
#
# THE WIRING IS TWO LINES IN FILES LANE E DOES NOT OWN:
#
#   agents/server.py, beside `"source": llm.last_source() or ""`:
#       "usage": llm.usage_payload(),
#
#   common/agent_client.py, beside the `llm._record(source)` call:
#       llm.absorb_usage_payload(envelope.get("usage"))
#
# Both are additive and backward compatible in both directions -- an older
# container omits the key and `absorb_usage_payload(None)` is a no-op, and an older
# runner ignores a key it does not read. The mechanism, its serialisation and its
# refusal behaviour are all here and all tested; only those two lines are pending
# an owner.


def usage_payload() -> list[dict]:
    """Every recorded call as JSON-serialisable dicts, for the response envelope.

    Plain dicts rather than the dataclass, because `json.dumps` cannot encode a
    dataclass and `agents/server.py` is standard-library only -- the same reason
    that file already needs `model_dump(mode="json")` rather than `model_dump()`.

    Field names are the dataclass's own, NOT strands' camelCase. The wire format
    here is between two copies of THIS repository, so it should speak this
    repository's vocabulary; `cacheReadInputTokens` is a detail of the provider
    that `_usage_from_metrics` has already translated away.
    """
    return [
        {
            "stage": entry.stage,
            "model": entry.model,
            "input_tokens": entry.input_tokens,
            "output_tokens": entry.output_tokens,
            "cached_tokens": entry.cached_tokens,
            "cached_reported": entry.cached_reported,
            "fixture": entry.fixture,
        }
        for entry in _USAGE
    ]


def absorb_usage_payload(payload: object) -> int:
    """Record a container's usage rows locally. Returns how many were accepted.

    NEVER RAISES, and that is a hard requirement rather than defensiveness. It runs
    on the runner's side of a network call, and `agent_client` is explicit that the
    provenance is recorded BEFORE validation so a container that answered honestly
    and then failed validation still reports which path it took. A bookkeeping
    function that raised there would convert a cost-reporting gap into a failed
    stage -- an instrument becoming an outage.

    A malformed or absent payload records NOTHING rather than a zero row. An older
    container omits the key entirely, and inventing a zero-token row for it would
    assert that the stage spent nothing when the truth is that the container could
    not report -- the `""`-versus-`0` distinction this whole lane turns on. The
    return count is what lets a caller (or a test) tell "nothing was sent" from
    "rows were sent and rejected".
    """
    if not isinstance(payload, list):
        return 0

    accepted = 0
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            _record_usage(Usage(
                stage=str(row.get("stage", "") or ""),
                model=str(row.get("model", "") or ""),
                input_tokens=int(row.get("input_tokens", 0) or 0),
                output_tokens=int(row.get("output_tokens", 0) or 0),
                cached_tokens=int(row.get("cached_tokens", 0) or 0),
                cached_reported=bool(row.get("cached_reported", False)),
                fixture=bool(row.get("fixture", False)),
            ))
        except (TypeError, ValueError):
            # A row whose numbers are not numbers. Skipped rather than raised, and
            # counted out of `accepted` so the loss is observable.
            logging.getLogger(__name__).warning(
                "a usage row from the remote agent could not be read; "
                "this stage's cost will be understated"
            )
            continue
        accepted += 1
    return accepted





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
    """Raw model call. Separated so tests can substitute it.

    THE TOKEN COUNTS ARE READ HERE AND NOWHERE ELSE, because this is the only
    line in the repository that holds an `AgentResult`. `str(result)` concatenates
    the text blocks and discards `result.metrics` -- which is exactly what the
    pre-instrumentation version did, and why no run could report its cost.

    The signature stays `-> str`. See the `Usage` note above: widening it would
    break every string-returning stub in the suite, and a double that cannot
    express the new shape is this repository's named recipe for unfalsifiable
    confidence.
    """
    from strands import Agent

    model = create_model()
    agent = Agent(model=model, system_prompt=system_prompt)
    result = agent(user_prompt)

    # `get_config()["model_id"]` rather than a config read, so the recorded id is
    # the one that ANSWERED rather than the one the environment asked for. The two
    # differ whenever `create_model` is passed an override, and a cost record
    # naming the wrong model prices the run against the wrong table.
    model_id = ""
    try:
        model_id = str(model.get_config().get("model_id", "") or "")
    except Exception:
        # Bookkeeping must not break a run. See _usage_from_metrics.
        logging.getLogger(__name__).debug(
            "could not read the model id for the cost record", exc_info=True
        )

    _record_usage(_usage_from_metrics(getattr(result, "metrics", None), model_id))
    return str(result)


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
