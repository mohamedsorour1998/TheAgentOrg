"""Indexing a run against its tenant. The one call site the pipeline was missing.

OWNER: the integrator. Written for Lane I, which found the gap and refused to work
around it.

WHY THIS MODULE EXISTS
======================
Lane B built `tenancy.accessors.record_run` and its whole leak suite around the `run`
table -- and NOTHING ON THE PIPELINE PATH EVER CALLED IT. Measured:

    grep -rn record_run agentorg/ scripts/ tests/
    -> agentorg/tenancy/accessors.py:409  (the definition)
    -> tests/test_tenancy_leak.py         (four uses, all Lane B's own tests)

So the table was correct, tenant-scoped, covered by a suite that attempts the breach on
every accessor, and empty on every real run. Lane I's tenant-scoped run list read it
faithfully and would have shown a judge nothing.

This is the same shape as two defects already found this phase -- Lane C's scoring
library with no caller, and Lane E's usage payload with no wiring -- and it is the
shape worth naming: a correct answer nobody asks for. Every test passes. The feature
does not exist.

WHY A HELPER RATHER THAN A CALL IN EACH STAGE
=============================================
Two paths create runs (`graph.run_pipeline` and `scripts/run_stage.py:_stage_plan`) and
two more change their status. Four call sites, each needing to open a connection, resolve
the marker tenant, build a scope and swallow the right failures, is four chances to get
the tenancy translation wrong -- and the wrong version writes a row under a blank tenant,
which `scope_for` refuses precisely because "a blank scope matches a blank tenant column,
which is a row nobody owns".

THIS MODULE NEVER RAISES, AND THAT IS A DELIBERATE TRADE
=======================================================
An index is not the run's record. `gates.save` is the one place a `RunState` is
serialized, and this table deliberately does not store the document. So a failure to
index must not fail a pipeline that has already done its work: a poisoned run that
correctly blocked and then died writing an index row would report a crash where the
demo's whole point was a clean refusal.

The cost is that a silent indexing failure leaves the UI's list short, which is exactly
the "did not run versus passed" ambiguity this repository exists to prevent. So every
failure is LOGGED at warning with the run id, and `record_run` returns a bool the caller
may assert on in tests. Silent in production, observable in the log, checkable in a test.

TENANCY IS OPTIONAL AND STAYS THAT WAY
======================================
`config.TENANT_MODE` defaults to `single` and `RunState.tenant_id` defaults to `""`. This
module is a no-op when there is no database to write to -- `QUEUE_DSN`/`TENANT_DB` unset
means the single-tenant deployment behaves exactly as it did, which is the property every
knob in `config.py` is chosen to preserve.
"""

from __future__ import annotations

import logging
import os

from ..state import RunState

# WHERE THE TENANCY DATABASE LIVES, read at CALL time through `os.environ` rather than
# bound at import. Same rule as every knob in `config.py`: a value bound at import is
# fixed before any fixture runs, so the setting would ignore both the tests and the
# deployed environment.
#
# NOT a `config.py` addition, deliberately. `config` is imported by 36 modules and this
# is one optional path's location; adding a knob there for it would be the fifteenth
# field arriving mid-phase that the Phase 0 batch exists to prevent. If tenancy becomes
# the default deployment, it moves there in one batch with everything else.
_DB_ENV = "TENANT_DB"


def _database_path() -> str:
    """The tenancy database, or "" when there is none. Blank means "do not index"."""
    return os.environ.get(_DB_ENV, "").strip()


def record_run(state: RunState) -> bool:
    """Index `state` against its tenant. Returns whether a row was written.

    False means "not indexed", for any reason: no database configured, the schema is
    absent, the row already exists, or the write failed. The caller does not branch on
    it -- it exists so a test can assert the write happened rather than inferring it
    from a green run, which is what let this gap exist in the first place.
    """
    path = _database_path()
    if not path:
        return False

    try:
        from ..db import engine
        from ..tenancy import accessors, tenant_zero

        tenant_id = tenant_zero.for_run_state(state.tenant_id)
        connection = engine.connect(path)
        try:
            with engine.acting_as(tenant_id):
                scope = accessors.scope_for(connection, tenant_id)
                accessors.record_run(
                    scope,
                    state.run_id,
                    state.ticket_id,
                    state.status,
                    # The path a reader would open, formatted the way `gates.StateRef`
                    # formats itself. Not the document: one writer, and it is `gates.save`.
                    state_ref=str(state.run_id),
                )
            connection.commit()
        finally:
            connection.close()
    except Exception:
        # BROAD ON PURPOSE, and the logger is fetched INLINE -- CLAUDE.md records that
        # ruff's BLE001 cannot resolve a module-level alias, so `_log.exception(...)`
        # turns `ruff check agentorg` red, and that narrowing the except satisfies the
        # rule with NO logging at all, which is the worse option.
        #
        # Every failure lands here: a missing schema, a duplicate run id, a locked
        # database. None of them may fail a run that has already done its work.
        logging.getLogger(__name__).warning(
            "could not index run %s against tenant %r; the UI's run list will be "
            "short by one row. The run itself is unaffected -- gates.save holds the "
            "record.", state.run_id, state.tenant_id or "(tenant zero)",
            exc_info=True,
        )
        return False
    return True


def update_status(state: RunState) -> bool:
    """Update an indexed run's status. Returns whether a row was updated.

    Separate from `record_run` because the index is written once at `plan` and revised at
    every ending -- and `accessors.update_run_status` calls `_require` first, so it
    refuses a run this tenant does not own rather than inserting one. An upsert here
    would turn a wrong-tenant update into a new row under the caller's tenant, which is
    the cross-tenant write Lane B's leak suite exists to catch.
    """
    path = _database_path()
    if not path:
        return False

    try:
        from ..db import engine
        from ..tenancy import accessors, tenant_zero

        tenant_id = tenant_zero.for_run_state(state.tenant_id)
        connection = engine.connect(path)
        try:
            with engine.acting_as(tenant_id):
                scope = accessors.scope_for(connection, tenant_id)
                accessors.update_run_status(scope, state.run_id, state.status)
            connection.commit()
        finally:
            connection.close()
    except Exception:
        logging.getLogger(__name__).warning(
            "could not update the index for run %s (status %r); the UI will show a "
            "stale status. The run's own record is correct.",
            state.run_id, state.status, exc_info=True,
        )
        return False
    return True
