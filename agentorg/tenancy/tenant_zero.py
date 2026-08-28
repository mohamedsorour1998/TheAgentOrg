"""Tenant zero: today's single-tenant deployment, migrated in, losing nothing. B6.

OWNER: Lane B.

THE CORE IDEA, AND IT IS DELIBERATELY NOT A MIGRATION OF THE RUNS. `RunState.tenant_id`
defaults to `""`, and every run document on disk carries that blank. Rewriting them would
mean touching tens of thousands of files -- and `state.py` is frozen, so the blank cannot
be redefined. Instead the blank is TRANSLATED, in exactly one place: `for_run_state`
below maps `""` to tenant zero's real id.

WHY A REAL NON-BLANK ID RATHER THAN LETTING `""` BE A TENANT. A blank scope would match a
blank tenant column, which is a row nobody owns -- and `engine.acting_as("")` refuses for
that reason. Worse, allowing it would create a code path only tenant zero exercises: the
one tenant whose behaviour differs from every other is the one whose bugs are found last.
Tenant zero is an ordinary row, subject to every guard, and the only thing special about
it is the translation.

THE PROPERTY THIS MODULE MUST HAVE, in the brief's words: tenant zero's existing runs are
readable. So `adopt` is idempotent and never overwrites -- running it twice on a live
database must not reset a budget somebody has since configured, and must not fail either,
because a startup path that refuses on second boot is a startup path somebody disables.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..db import engine, schema

# Re-exported from the schema so there is ONE spelling of tenant zero's id in the
# codebase. A second literal here would be a second declaration of the fact the
# translation depends on, and both copies would keep working as they drifted.
TENANT_ZERO_ID = schema.TENANT_ZERO_ID

# THE MARKER, NOT A SCOPE. This is what `RunState.tenant_id` carries for every run written
# before tenancy existed. It appears in this module and nowhere else, because the
# translation is the only place it means anything.
SINGLE_TENANT_MARKER = ""


def for_run_state(tenant_id: str) -> str:
    """The database tenant id for a `RunState.tenant_id`. The ONE translation point.

    Called with the blank every existing run carries, it answers tenant zero. Called with
    a real tenant id it answers that id unchanged -- so a multi-tenant run is not
    silently reassigned, which would be the inverse defect and would look identical in
    the data.

    Whitespace is treated as the marker too. A `" "` tenant id is not a tenant anybody
    created; it is the marker with a typo, and `engine.acting_as` would refuse it two
    frames later with a message about blankness that named no run.
    """
    if not tenant_id or not tenant_id.strip():
        return TENANT_ZERO_ID
    return tenant_id


def is_tenant_zero(tenant_id: str) -> bool:
    """Whether this id -- marker or real -- denotes tenant zero."""
    return for_run_state(tenant_id) == TENANT_ZERO_ID


def adopt(connection, *, ceiling_cents: int | None = None) -> bool:
    """Create tenant zero if it is absent. Returns whether it was created.

    IDEMPOTENT, AND IT NEVER OVERWRITES. Called on every startup, so a second call must
    not reset a budget an operator has since raised, and must not raise either -- a
    startup step that fails on the second boot is a step somebody comments out.

    `ceiling_cents=None` means UNLIMITED, and it is the right default for exactly this
    tenant: the existing deployment has been running without a ceiling, so inventing one
    at adoption would refuse the runs that were working yesterday. `budgets.check`
    refuses a tenant with NO row, so the row must exist -- unlimited is set explicitly
    here rather than left absent, which is the distinction budgets.py is built on.

    Returns a bool rather than the row because the caller's only question is "did this
    just get created", which is the line a startup log wants.
    """
    now = datetime.now(UTC).isoformat()
    with engine.acting_as(TENANT_ZERO_ID):
        existing = connection.execute(
            'SELECT "id" FROM "organisation" WHERE "id" = ?', (TENANT_ZERO_ID,)
        ).fetchone()
        if existing is not None:
            return False

        connection.execute(
            'INSERT INTO "organisation" ("id", "name", "created_at") VALUES (?, ?, ?)',
            (TENANT_ZERO_ID, schema.TENANT_ZERO_NAME, now),
        )
        connection.execute(
            'INSERT INTO "budget" '
            '("tenant_id", "ceiling_cents", "spent_cents", "unlimited", "updated_at") '
            "VALUES (?, ?, ?, ?, ?)",
            (
                TENANT_ZERO_ID,
                # A ceiling is stored even when unlimited is set, because the column is
                # NOT NULL -- deliberately, so that "absent" cannot read as "unlimited"
                # anywhere. When unlimited is on, the number is not consulted.
                0 if ceiling_cents is None else ceiling_cents,
                0,
                1 if ceiling_cents is None else 0,
                now,
            ),
        )
    connection.commit()
    return True
