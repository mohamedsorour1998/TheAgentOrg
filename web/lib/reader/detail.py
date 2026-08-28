"""One run's detail, cost and scoring. Tasks I3, I6, I7. OWNER: Lane I.

Invoked by `web/lib/pipeline.ts` with a JSON request on stdin. Three actions, all
reads, all tenant-scoped through Lane B's accessors.

WHY THREE ACTIONS AND NOT ONE FAT RESPONSE
==========================================
Three screens ask three questions and one of them is expensive. The detail view is
opened for every run; the cost view is opened rarely and the scoring table rarer
still. Folding them would make every list-to-detail click pay for both.

They share `_load`, so the run's document is read once per request rather than
three times -- and `gates.load` is a file read or a DynamoDB GetItem, which is the
cost worth avoiding.

=========================================================================
OWNERSHIP IS ESTABLISHED BEFORE THE DOCUMENT IS READ, IN EVERY ACTION
=========================================================================
`accessors.get_run(scope, run_id)` runs FIRST and raises for a run this tenant does
not own. Only then is `gates.load(run_id)` called.

That order is the requirement, not the call. `gates.load` is NOT tenant-scoped -- it
reads `runs/<run_id>.state.json` by name, and it cannot be scoped, because the run
document predates multi-tenancy and `RunState.tenant_id` defaults to `""`. So the
index row is the ONLY thing that establishes ownership, and reading the document
first would mean answering a cross-tenant request with real data and then deciding
whether to have done so.

`run_stage.py` inherited `graph.py`'s comment about a hazard but not its test, three
times; the equivalent here would be a future action that reads the document before
the accessor and reads exactly like correct code. `tests/` cannot see this file, so
the ordering is enforced by `_load` being the only reader and taking the scope.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from agentorg import gates, log, queue
from agentorg.cost import record as cost_record
from agentorg.cost import report as cost_report
from agentorg.db import engine
from agentorg.security import scoring
from agentorg.tenancy import accessors, tenant_zero


def _fail(message: str, detail: str = "") -> int:
    json.dump({"error": message, "detail": detail}, sys.stdout)
    return 0


def _database_path() -> str:
    """The tenancy database, or "". Same env var the writer reads -- `TENANT_DB`."""
    return os.environ.get("TENANT_DB", "").strip()


def _load(tenant_id: str, run_id: str):
    """(index row, run document) for a run this tenant owns, or raise.

    THE ONLY READER OF EITHER, so the ordering above holds by construction rather
    than by every caller remembering it.

    The document may be `None` -- absent, truncated, mid-write, or written by an
    older contract. That is reported as null fields rather than as an error, because
    a run indexed for this tenant genuinely exists even when its document cannot be
    read, and hiding it would be the same conflation `approve_server._awaiting`
    avoids by returning its unreadable count.
    """
    path = _database_path()
    if not path:
        raise accessors.NotFound("no run index is configured")

    connection = engine.connect(path)
    with engine.acting_as(tenant_id):
        # OWNERSHIP FIRST. See the module docstring.
        row = accessors.get_run(accessors.scope_for(connection, tenant_id), run_id)

    try:
        state = gates.load(run_id)
    except Exception:
        logging.getLogger(__name__).warning(
            "could not read the state document for an indexed run", exc_info=True)
        state = None
    return row, state


def _stages(run_id: str) -> list[dict]:
    """Every stage of the run, as the queue saw it.

    `reclaimed_from` is CARRIED THROUGH rather than dropped. Non-empty means the job
    was reclaimed from a worker whose lease expired, so THE STAGE MAY HAVE RUN TWICE
    -- the only trace of at-least-once delivery the queue keeps. A UI that hid it
    would make a double invocation invisible.
    """
    return [
        {
            "stage": job.stage,
            "status": job.status,
            "attempt": job.attempt,
            "exit_code": job.exit_code,
            "enqueued_at": job.enqueued_at,
            "updated_at": job.updated_at,
            "reclaimed_from": job.reclaimed_from,
        }
        for job in queue.jobs_for_run(run_id)
    ]


def run_detail(tenant_id: str, run_id: str) -> dict:
    """Everything one run's detail screen needs."""
    row, state = _load(tenant_id, run_id)
    security = getattr(state, "security", None)
    dev = getattr(state, "dev", None)

    return {
        "run_id": row["run_id"],
        "ticket_id": row.get("ticket_id", ""),
        "ticket_text": getattr(state, "ticket_text", "") or "",
        "status": getattr(state, "status", None) or row.get("status", "running"),
        "created_at": row.get("created_at", ""),
        # `""` MEANS NOBODY RECORDED IT, not `model`. `RunState.model_provenance`
        # defaults blank on every run written before the field existed, and the
        # renderer must report unknown rather than guessing.
        "model_provenance": getattr(state, "model_provenance", "") or "",
        "trigger": getattr(state, "trigger", "") or "",
        "poisoned": bool(getattr(state, "poisoned", False)),
        "pr_url": getattr(dev, "pr_url", None) if dev else None,
        "branch": getattr(dev, "branch", None) if dev else None,
        "verdict": getattr(security, "verdict", None) if security else None,
        "scan_provenance": getattr(security, "scan_provenance", "") if security else "",
        "blocking": len(getattr(security, "blocking", [])) if security else None,
        "stages": _stages(run_id),
        "decisions": [
            {
                "gate": d.gate,
                "decision": d.decision,
                "by": d.by,
                "at": d.at,
                "reason": d.reason,
            }
            for d in getattr(state, "decisions", [])
        ],
        "security": None if security is None else {
            "verdict": security.verdict,
            "findings": [f.model_dump(mode="json") for f in security.findings],
            "blocking": [f.model_dump(mode="json") for f in security.blocking],
            "explanation": security.explanation,
            "scan_provenance": security.scan_provenance,
            "scoring": [r.model_dump(mode="json") for r in security.scoring],
        },
        "awaiting_gates": sorted(
            job.awaiting_gate for job in queue.awaiting()
            if job.run_id == run_id and job.awaiting_gate
        ),
        "awaiting_gate": next(
            (job.awaiting_gate for job in queue.awaiting()
             if job.run_id == run_id and job.awaiting_gate), ""),
    }


def run_cost(tenant_id: str, run_id: str) -> dict:
    """What this run cost, per stage, with the cache finding. Task I6.

    `usd` IS `None` FOR NOT-PRICED AND `0.0` FOR PRICED-AND-FREE, and the two are
    NEVER collapsed. Lane E's `CostRecord.usd` draws the distinction because
    defaulting to zero "would make a missing price table look like a free run, which
    is this project's signature defect shape".

    THE DISCRIMINATOR FOR "is cost wired?" IS `stages_priced`, NEVER `usd`. An
    unwired run has ZERO rows and `usd=None`; a wired run has a row per stage even
    when that stage spent nothing. `usd == 0.0` cannot tell them apart.

    THE CACHE FINDINGS ARE `report.render`'s WORDS, not a number this file derives.
    Lane E measured that the alarm must be conditioned on the RENDERED string rather
    than on `rate == 0.0`, because `_pct` formats to one decimal so every rate below
    0.05% renders `0.0%` while comparing unequal to zero -- so a run with one cached
    token in a million printed `0.0%` with no finding beside it. Re-deriving the
    condition here would reintroduce exactly that gap.
    """
    _row, state = _load(tenant_id, run_id)
    cost = getattr(state, "cost", None)
    stages = list(getattr(cost, "stages", [])) if cost else []

    return {
        "usd": getattr(cost, "usd", None) if cost else None,
        "stages_priced": len(stages),
        "stages": [
            {
                "stage": row.stage,
                "model": row.model,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cached_tokens": row.cached_tokens,
                # `False` + 0 tokens means the provider reported no cache field;
                # `True` + 0 means it measured zero. Both render "0.0%" and want
                # different fixes, and a reader could not otherwise tell which it has.
                "cached_reported": row.cached_reported,
            }
            for row in stages
        ],
        # `None` for a zero denominator, never `0.0`.
        "cache_hit_rate": cost_record.cache_hit_rate(stages) if stages else None,
        "findings": _cost_findings(cost),
    }


def _cost_findings(cost) -> list[str]:
    """Lane E's rendered findings, as lines. Its words, not ours.

    `report.render` states the cache situation IN WORDS "rather than leaving a reader
    to infer it from `0.0%`, because nobody reads a percentage as an alarm". So the
    rendered text is split into lines and the finding lines returned, rather than
    this file deciding what counts as a finding.
    """
    if cost is None:
        return ["cost: no model calls recorded for this run"]
    rendered = cost_report.render(cost)
    return [line.strip() for line in rendered.splitlines() if line.strip()]


def run_scoring(tenant_id: str, run_id: str) -> dict:
    """Lane C's scoring artifact: one row per finding. Task I7.

    THE THRESHOLD IS ECHOED AT THE TOP LEVEL as well as on every row, because a run
    with NO findings has no rows and the threshold that produced that empty table is
    still a fact worth rendering -- otherwise a clean run and an unscanned one show
    the same blank table. `scan_provenance` travels for the same reason.

    THE ROWS ARE READ, NEVER RECOMPUTED. `SecurityResult.scoring` is populated by
    `scoring.score_findings` at scan time, and every `blocking` flag in it comes from
    `compute_security_verdict` -- one call per finding, against the same five lines
    the pipeline's verdict comes from. Recomputing here would be a second decision
    path whose only job is to agree with the first, and "an audit artifact that can
    disagree with the decision it describes is worse than none: it reads as proof".
    """
    _row, state = _load(tenant_id, run_id)
    security = getattr(state, "security", None)
    rows = list(getattr(security, "scoring", [])) if security else []

    return {
        "run_id": run_id,
        # The threshold in force. Taken from a row when there is one -- that is what
        # the verdict actually used -- and from the resolved configuration otherwise,
        # which is what an empty table was produced under.
        "threshold": rows[0].threshold if rows else scoring.resolve_threshold(),
        "rows": [row.model_dump(mode="json") for row in rows],
        "scan_provenance": (
            getattr(security, "scan_provenance", "") if security else ""
        ),
    }


_ACTIONS = {
    "run_detail": run_detail,
    "run_cost": run_cost,
    "run_scoring": run_scoring,
}


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except Exception as error:
        logging.getLogger(__name__).warning(
            "the reader could not parse its request", exc_info=True)
        return _fail("the reader could not parse its request", str(error))

    if not isinstance(request, dict):
        return _fail("the reader expects a JSON object")

    tenant_id = request.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        # A blank tenant is REFUSED, never translated to tenant zero. Reading `""` as
        # tenant zero here would let a caller with no session read the original
        # single-tenant deployment's runs.
        return _fail("the reader was given no tenant, so the read has no scope")
    tenant_id = tenant_zero.for_run_state(tenant_id)

    run_id = request.get("run_id")
    if not isinstance(run_id, str) or not log.is_safe_run_id(run_id):
        # REFUSED BEFORE IT REACHES A PATH. `gates.load` builds one from this value,
        # and `gates._state_path` would resolve `../../etc/passwd` outside `runs/`.
        # Answered as "no such run" so a traversal attempt and an absent run are
        # indistinguishable. The value is NOT echoed.
        return _fail("no such run")

    action = _ACTIONS.get(request.get("action", ""))
    if action is None:
        return _fail(f"unknown reader action {request.get('action')!r}")

    try:
        answer = action(tenant_id, run_id)
    except accessors.CrossTenantAccess:
        # THE SAME ANSWER AS AN ABSENT RUN. A run id is an unguessable uuid, so
        # telling them apart discloses that somebody else's run exists.
        return _fail("no such run")
    except accessors.NotFound:
        return _fail("no such run")
    except Exception as error:
        logging.getLogger(__name__).exception("the read failed")
        return _fail("the read failed", f"{type(error).__name__}: {error}")

    json.dump(answer, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
