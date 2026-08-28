"""Read a run's real state, tenant-scoped, and print it as JSON. Lane I's reader.

OWNER: Lane I (`web/lib/**`). Invoked by `web/lib/pipeline.ts` as a subprocess with
a JSON request on stdin; prints one JSON object on stdout and exits 0.

=========================================================================
WHY THIS IS PYTHON AND NOT A NODE DATABASE CLIENT
=========================================================================
Because Lane B's isolation lives in Python and cannot be reached any other way.

`db/engine.connect()` registers `current_tenant()` as an application-defined
SQLite function, and every isolation trigger in `db/schema.py` compares against
it. A connection opened from Node has no such function -- so every scoped WRITE
fails with "no such function", and every scoped READ succeeds **unscoped**,
because SQLite has no mechanism that constrains a SELECT against a base table.
Lane B's ADR states that plainly: on the tested path a read is only as scoped as
its accessor's `WHERE tenant_id = ?`.

So a Node-side reader would be a second, weaker copy of the one predicate whose
removal fails 13 named Python tests. This file exists so there is exactly one.

`engine.acting_as(tenant_id)` wraps every read below. It is a context manager and
not a setter deliberately -- it restores the previous value in a `finally`, so an
exception cannot leave a tenant bound for whatever runs next on the thread.

=========================================================================
WHAT IT REFUSES, AND HOW A REFUSAL IS REPORTED
=========================================================================
A refusal is printed as `{"error": ..., "detail": ...}` with exit 0, and
`pipeline.ts` turns that into a `PipelineError`. Deliberately not a non-zero exit:
a cross-tenant attempt and an absent run are ANSWERS, not crashes, and a traceback
on stderr would make them indistinguishable from a broken reader.

A CROSS-TENANT READ AND AN ABSENT RUN GET THE SAME ANSWER. `accessors.get_run`
raises `CrossTenantAccess` for one and `NotFound` for the other, and both are
reported here as "no such run" -- because a run id is an unguessable uuid, so
telling them apart discloses that somebody else's run exists. `web/lib/authz.ts`
makes the same choice at the same boundary and its tests assert the two answers
are byte-identical.
"""

from __future__ import annotations

import json
import logging
import os
import sys

# `agentorg` resolves through PYTHONPATH, which `pipeline.ts` sets to the
# repository root. Not a `sys.path` insertion here: CLAUDE.md records `cf5cb83`,
# where a subprocess whose path was left to the editable install resolved
# `agentorg` to a DIFFERENT checkout, and three lanes each lost time to it. One
# place sets it, and that place is the caller.
from agentorg import gates, log, queue
from agentorg.db import engine
from agentorg.tenancy import accessors, tenant_zero

# The five statuses `RunState.status` may hold -- `state.py:307`. Restated rather than
# imported because importing `RunState` to read a Literal's members costs pydantic at
# reader startup, and this list is checked against the contract by a test rather than
# trusted. A status this reader does not recognise becomes `failed`, which REFUSES.
_RUN_STATUSES = frozenset({"running", "blocked", "rejected", "promoted", "failed"})


def _fail(message: str, detail: str = "") -> int:
    """Print a refusal and exit 0. See the module docstring."""
    json.dump({"error": message, "detail": detail}, sys.stdout)
    return 0


def _summary(row: dict, state: object | None) -> dict:
    """One `RunSummary`, from the tenant's own index row plus the run's document.

    THE INDEX ROW IS THE AUTHORITY ON OWNERSHIP; the state document is the
    authority on the verdict. Two sources because they answer different questions
    and are written by different code -- `accessors.record_run` indexes a run
    against a tenant, and `gates.save` is "the one place a RunState is serialized".

    A run indexed for this tenant whose document cannot be read is reported with a
    null verdict rather than skipped. Absent and unreadable are different facts
    from "scanned and clean", and `approve_server._awaiting` returns its unreadable
    count for exactly this reason: rendering them identically is the silent
    conflation this codebase keeps paying for.
    """
    security = getattr(state, "security", None) if state is not None else None
    return {
        "run_id": row["run_id"],
        "ticket_id": row.get("ticket_id", ""),
        # The document's status is preferred: the index row is written at enqueue
        # and updated separately, so it can lag. `gates.save` writes the document
        # at every gate AND as the run exits, which is why a finished run's
        # document is trustworthy -- before it did, "every finished run still read
        # status='running'".
        "status": getattr(state, "status", None) or row.get("status", "running"),
        "created_at": row.get("created_at", ""),
        "verdict": getattr(security, "verdict", None) if security else None,
        "scan_provenance": getattr(security, "scan_provenance", "") if security else "",
        "blocking": len(getattr(security, "blocking", [])) if security else None,
        "awaiting_gate": "",
    }


def _state_for(run_id: str) -> object | None:
    """The run's document, or None when it cannot be read.

    Broad `except` on purpose, matching `approve_server._awaiting`: a truncated,
    mid-write, absent or older-contract document must not blank the whole screen.
    The caller reports a null verdict rather than a clean one.
    """
    try:
        return gates.load(run_id)
    except Exception:
        # Logged with the traceback, inline. TWO REASONS, and the second is
        # mechanical: an unreadable document is a real fact an operator needs, and
        # ruff's BLE001 is satisfied ONLY by a logging call it can statically
        # resolve to the logging module, carrying the traceback, INSIDE the handler
        # -- CLAUDE.md measured that a module-level `_log` alias defeats the
        # resolution and that narrowing the `except` satisfies the rule with no
        # logging at all, which is the worse option. There is no noqa to spend.
        #
        # The run id is NOT interpolated: it is untrusted and this reaches a log.
        logging.getLogger(__name__).warning(
            "could not read the state document for a listed run", exc_info=True)
        return None


def _awaiting_by_run() -> dict[str, list[str]]:
    """Which gates each run is paused at, from the queue's own rows.

    A READ, NOT AN INFERENCE. `approve_server._awaiting` derives this from pause
    markers in the log minus recorded decisions, because nothing recorded the pause
    directly. On the queue a paused job IS the record -- `queue.awaiting()` returns
    them -- so this is the stronger source and is the one used here.
    """
    paused: dict[str, list[str]] = {}
    for job in queue.awaiting():
        gate = job.awaiting_gate
        if gate:
            paused.setdefault(job.run_id, []).append(gate)
    return paused


def list_runs(tenant_id: str) -> dict:
    """Every run this tenant owns, newest first, and whether anything indexes them.

    `indexed: false` with an empty list is NOT the same fact as `indexed: true` with
    an empty list. See `_database_path`.
    """
    path = _database_path()
    if not path:
        return {"runs": [], "indexed": False}

    connection = engine.connect(path)
    with engine.acting_as(tenant_id):
        rows = accessors.list_runs(accessors.scope_for(connection, tenant_id))

    paused = _awaiting_by_run()
    runs = []
    for row in rows:
        summary = _summary(row, _state_for(row["run_id"]))
        gates_open = paused.get(row["run_id"], [])
        summary["awaiting_gate"] = gates_open[0] if gates_open else ""
        runs.append(summary)
    return {"runs": runs, "indexed": True}


def run_facts(tenant_id: str, run_id: str) -> dict:
    """The facts an approval is decided over. Feeds `web/lib/authz.ts`.

    FOUR FACTS, MEASURED HERE SO THE DECIDER CANNOT BE HANDED THEM BY A CALLER: which
    tenant owns the run, which repository it targets, its status, and which gates it is
    paused at right now.

    `accessors.get_run` RAISES for a run this tenant does not own -- `CrossTenantAccess`
    -- and for one that does not exist -- `NotFound`. `main` reports BOTH as "no such
    run", because a run id is an unguessable uuid so telling them apart discloses that
    somebody else's run exists.

    TWO RECORDS ARE READ, AND THEY CAN DISAGREE. The run's own document carries
    `status`; the queue's paused rows carry the open gates. `gates.resume` sets
    `status="rejected"` and never un-sets it, so a run can be paused-at-a-gate
    according to the queue and already-ended according to its state -- which is exactly
    the gap `authz.decide` refuses. Both are returned; neither is derived from the
    other.

    AN UNREADABLE DOCUMENT YIELDS `failed`, NOT `running`. `failed` is terminal, so it
    REFUSES the approval; `running` would permit one over a run whose state nobody
    could read. Fail-closed, and the same direction `_summary` takes with a null
    verdict.
    """
    path = _database_path()
    if not path:
        # No index means no ownership record, so ownership cannot be established --
        # and an approval must not proceed on a run whose tenant is unknown. Reported
        # as "no such run" rather than as a configuration error, deliberately: the
        # caller learns only what they already tried.
        raise accessors.NotFound("no run index is configured")

    connection = engine.connect(path)
    with engine.acting_as(tenant_id):
        row = accessors.get_run(accessors.scope_for(connection, tenant_id), run_id)
        repositories = accessors.list_repositories(
            accessors.scope_for(connection, tenant_id))

    state = _state_for(run_id)
    status = getattr(state, "status", "") or ""
    if status not in _RUN_STATUSES:
        status = "failed"

    return {
        "run_id": row["run_id"],
        "tenant_id": row["tenant_id"],
        # WHICH REPOSITORY THIS RUN TARGETS. `RunState` carries no repository field --
        # `state.py` is frozen and the deployment is single-repository today -- so the
        # honest answer is the tenant's single connected repository when there is
        # exactly one, and "" otherwise. "" fails `authz.decide`'s scope check, which
        # REFUSES; a guess would permit an approval against a repository nobody named.
        #
        # This is a real limit, not a workaround: closing it needs an optional
        # `RunState.repository` field, which is the frozen contract and another lane's
        # file.
        "repository_full_name": (
            repositories[0]["full_name"] if len(repositories) == 1 else ""
        ),
        "status": status,
        "awaiting_gates": _awaiting_by_run().get(run_id, []),
    }


def list_repositories(tenant_id: str) -> dict:
    """Every repository this tenant has in scope. Feeds I5's per-repository check.

    Reads Lane B's `repository` table through its SCOPED accessor -- the table that
    already carries the isolation triggers and the Postgres RLS policy, and
    `UNIQUE (tenant_id, full_name)` so two customers may both connect
    `acme/auth-service`.

    NO SECOND TABLE, deliberately: a `web_repository_scope` here would be a second
    answer to "is this repository in scope", and that is the answer an approval reads.
    Two tables that can disagree means an approval permitted by one and refused by the
    other, with nothing recording which was consulted.

    An empty list when nothing is configured, NOT an error, for `list_runs`' reason --
    and `authz.decide` refuses against an empty list, so a tenant that has connected
    nothing cannot approve anything.
    """
    path = _database_path()
    if not path:
        return {"repositories": [], "indexed": False}

    connection = engine.connect(path)
    with engine.acting_as(tenant_id):
        rows = accessors.list_repositories(accessors.scope_for(connection, tenant_id))
    return {
        "repositories": [{"full_name": row["full_name"]} for row in rows],
        "indexed": True,
    }


def _database_path() -> str:
    """The tenancy database, or "" when there is none.

    THE SAME ENV VAR THE WRITER READS -- `TENANT_DB`, matching
    `agentorg/tenancy/run_index.py:70`. One name, so a deployment that indexes runs
    and a reader that lists them cannot be pointed at different files. Read at CALL
    time, never bound at import, for the reason every knob in `config.py` gives.

    NOT in `config.py`, for the reason the writer states there: that module has 36
    importers and this is one optional path's location.

    A BLANK IS A LEGITIMATE STATE, NOT AN ERROR, and this is the correction the
    integrator flagged. `run_index.record_run` is a **no-op** when `TENANT_DB` is
    unset, so the single-tenant deployment writes no rows at all -- and a reader that
    raised there would turn the normal single-tenant configuration into a 500 on
    every screen.

    So the reader distinguishes three states rather than two, because they want
    different fixes and a reader that conflated them would be the "did not run versus
    passed" defect:

        TENANT_DB unset        -> `indexed: false`, an EMPTY list. Nothing indexes
                                  runs here; a UI says so rather than showing zero.
        TENANT_DB set, no rows -> `indexed: true`, an empty list. This tenant has
                                  genuinely had no runs.
        TENANT_DB set, rows    -> `indexed: true`, the rows.

    An empty list ALONE cannot tell the first two apart, which is why `indexed`
    travels beside it.
    """
    return os.environ.get("TENANT_DB", "").strip()


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
        # A BLANK TENANT IS A REFUSAL, never a default. `engine.acting_as` refuses
        # one too: "a blank scope matches a blank column and that is a row nobody
        # owns". Reading `""` as tenant zero HERE would let a caller with no
        # session read the original single-tenant deployment's runs.
        return _fail(
            "the reader was given no tenant, so the read has no scope",
            "a blank tenant is refused rather than translated to tenant zero",
        )

    # TRANSLATED, NEVER REWRITTEN. `RunState.tenant_id` defaults to "" and every run
    # on disk carries it, so tenant zero is what a blank becomes -- in one place,
    # `tenancy.tenant_zero`. The blank arriving from a SESSION is refused above;
    # this handles a session whose tenant IS tenant zero.
    tenant_id = tenant_zero.for_run_state(tenant_id)

    action = request.get("action")
    try:
        if action == "list_runs":
            answer = list_runs(tenant_id)
        elif action == "list_repositories":
            answer = list_repositories(tenant_id)
        elif action == "run_facts":
            run_id = request.get("run_id")
            if not isinstance(run_id, str) or not log.is_safe_run_id(run_id):
                # REFUSED BEFORE IT REACHES A PATH. `log.is_safe_run_id` is the same
                # guard `queue.enqueue` and `adopt_run_id` apply, and it is applied
                # here for the same reason: `gates.load` builds a path from this value.
                # `approve_server` records that `gates._state_path` would happily
                # resolve `../../etc/passwd` OUTSIDE runs/. The value is NOT echoed.
                return _fail("no such run")
            answer = run_facts(tenant_id, run_id)
        else:
            return _fail(f"unknown reader action {action!r}")
    except accessors.CrossTenantAccess:
        # Same answer as an absent run. See the module docstring.
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

