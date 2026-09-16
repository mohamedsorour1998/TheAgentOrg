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
module is a no-op when there is no table to write to -- `TENANCY_TABLE` unset means the
single-tenant deployment behaves exactly as it did, which is the property every knob in
`config.py` is chosen to preserve.

IT WROTE TO POSTGRES UNTIL 2026-09-15, AND THAT MADE THE WHOLE UI DARK
=====================================================================
Step 8 of `docs/design/dynamodb-migration.md` repointed `web/lib/reader/*.py` at
DynamoDB and step 6 gave them a tenant-scoped credential. **Nobody moved the writer.**
This module still opened `engine.connect(TENANT_DB)` -- a Postgres DSN -- so the half
that WRITES a run row and the half that READS one were pointed at two different
databases, and under the DynamoDB-only decision the DSN is never set at all.

The symptom was perfect silence. `record_run` returns False for "no database
configured" and never raises, by the design above, so every run indexed nothing and
every gate stayed green. Measured 2026-09-15 against the live table:

    aws dynamodb scan --table-name theagentorg-tenancy   ->  "count": 0

Zero rows after every run this project has ever done. The same `TENANT_DB` gate was
declared in FOUR places -- here and in three readers -- so one Postgres-shaped flag
decided whether the entire tenancy UI had anything to show.

**THE GATE IS NOW `TENANCY_TABLE`, READ WITH NO DEFAULT**, and the no-default part is
the load-bearing half. `config.TENANCY_TABLE` carries `theagentorg-tenancy` as a
convenience for building a client; using that here would make indexing unconditional,
and `graph.run_pipeline` is driven by hundreds of hermetic tests -- so every one of them
would reach DynamoDB. `tests/conftest.py` has guards for the model, GitHub, git, the
terminal, the scanner cache and the repo clone, and none for AWS, which this repository
measured the hard way on the same day. Blank therefore still means "not configured", and
the deployed environments set it explicitly; `tests/test_run_index_is_reachable.py`
asserts both of them do, because an unset variable that silently indexes nothing is the
exact defect being fixed here.
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
_TABLE_ENV = "TENANCY_TABLE"


def _index_table_name() -> str:
    """The tenancy table, or "" when there is none. Blank means "do not index".

    NO DEFAULT HERE, deliberately, and `config.TENANCY_TABLE` is not consulted -- see
    the module docstring. That constant exists so a client can be built without
    repeating a literal; reading it here would make every hermetic test that drives
    `graph.run_pipeline` open a boto3 client and write to the real account.
    """
    return os.environ.get(_TABLE_ENV, "").strip()


def _table(name: str):
    """A boto3 Table for the pipeline's own credential.

    AMBIENT, NOT TENANT-SCOPED, and that is the same ruling `queue/_dynamo.dynamo_queue`
    records. This runs on the GitHub Actions runner (and, when it is deployed, the
    worker), which legitimately spans tenants -- it indexes whichever run just executed.
    `dynamodb:LeadingKeys` cannot constrain a principal that does not know the tenant
    until after it reads the state, so the isolation here is the `tenant_id` this module
    resolves through `tenant_zero`, exactly as §4 of the migration plan admits for the
    pipeline half.

    Imported inside the function so the package imports with no boto3 call.
    """
    import boto3

    from ..common import config

    return boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(name)


def _ci_run_id() -> str:
    """GitHub's own run id, from the Actions environment. "" anywhere else.

    **IT IS A DIFFERENT NUMBER FROM `RunState.run_id`**, and the distinction is the
    point: `run_id` is the pipeline's uuid4, and this is the handle GitHub needs to
    release an Environment gate. Without it the web application can SEE that a run
    is waiting for a person and has no way to tell GitHub who decided.

    Read from the environment at CALL time. Empty off Actions -- the in-process
    path, the worker, and every test -- which is correct: there is no Actions run to
    approve, and an empty string is what `record_run` skips rather than writes.
    """
    return os.environ.get("GITHUB_RUN_ID", "").strip()


def _state_json(state: RunState) -> str:
    """The run's state as JSON, for the web application to read.

    **THE DEPLOYED UI COULD NOT REACH THE RUN'S RECORD AT ALL.** `gates.save` writes
    it to a JSONL file on whichever Actions runner ran the stage, handed forward as
    an artifact -- and an Amplify SSR Lambda cannot read an Actions artifact. So
    `/runs/<id>` showed the index row and nothing else, rendering every stage as
    `NOT STARTED` for a run whose `plan` had genuinely succeeded. A screen that says
    a stage did not run when it did is the exact conflation this repository exists
    to refuse.

    `mode="json"` because `model_dump()` alone returns objects `json.dumps` cannot
    encode -- the same requirement `agents/server.py` records.

    NEVER RAISES. It is called from inside `record_run`'s try block, whose whole
    contract is that indexing may not fail a run that has already done its work; a
    serialisation error here would be caught there, but returning `""` keeps the
    index row itself intact rather than losing it along with the document.
    """
    try:
        return state.model_dump_json()
    except Exception:
        logging.getLogger(__name__).warning(
            "could not serialise run %s for the read model; the index row is still "
            "written and the UI will show the run without its stages.",
            state.run_id, exc_info=True,
        )
        return ""


def record_run(state: RunState) -> bool:
    """Index `state` against its tenant. Returns whether a row was written.

    False means "not indexed", for any reason: no table configured, no credential, the
    row already exists, or the write failed. The caller does not branch on it -- it
    exists so a test can assert the write happened rather than inferring it from a green
    run, which is what let this gap exist in the first place.
    """
    name = _index_table_name()
    if not name:
        return False

    try:
        from . import _dynamo_accessors, tenant_zero

        tenant_id = tenant_zero.for_run_state(state.tenant_id)
        _dynamo_accessors.record_run(
            _table(name),
            tenant_id,
            state.run_id,
            state.ticket_id,
            state.status,
            # The reference a reader would resolve, formatted the way `gates.StateRef`
            # formats itself. `gates.save` remains the one WRITER of the run's record;
            # `state_json` below is a denormalised copy for one screen.
            state_ref=str(state.run_id),
            state_json=_state_json(state),
            ci_run_id=_ci_run_id(),
        )
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
    name = _index_table_name()
    if not name:
        return False

    try:
        from . import _dynamo_accessors, tenant_zero

        tenant_id = tenant_zero.for_run_state(state.tenant_id)
        # `update_run_status` calls `_require` first, so it refuses a run this tenant
        # does not own rather than inserting one -- the reason this is not an upsert.
        _dynamo_accessors.update_run_status(
            _table(name), tenant_id, state.run_id, state.status,
            state_json=_state_json(state),
            ci_run_id=_ci_run_id(),
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "could not update the index for run %s (status %r); the UI will show a "
            "stale status. The run's own record is correct.",
            state.run_id, state.status, exc_info=True,
        )
        return False
    return True
