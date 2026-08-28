"""The database connection, and the tenant binding that makes the guards work.

OWNER: Lane B. See ADR-001-database.md.

WHAT THIS MODULE IS FOR. `schema.py`'s SQLite triggers compare against
`current_tenant()`, which is an APPLICATION-DEFINED function -- SQLite has no such
builtin. So the guards only exist if something registers it, and this is that something.
A connection opened without going through here has no `current_tenant`, and every
scoped write against it fails with "no such function". That failure is deliberate and is
the right one: a write with nothing establishing who is asking must not proceed.

THE BINDING IS A CONTEXT MANAGER, NOT A SETTER. `acting_as(tenant_id)` binds for the
duration of a block and restores the previous value on the way out, including on an
exception. A bare setter would leave the last tenant bound after a failure, and the next
caller -- which may be a different request -- would inherit it. That is not a leak the
type system can catch, so the shape of the API prevents it instead.

Nothing is bound by default. `current_tenant()` returns NULL until a block binds it, and
the ADR measures what NULL does: with `IS NOT` in the trigger, every scoped write is
refused. Fail-closed, and closed is the state the process starts in.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator

from . import schema

# The bound tenant is THREAD-LOCAL. A module-level string would make two threads share
# one identity, and the failure mode is the one this lane exists to prevent: thread A
# binds tenant 1, thread B binds tenant 2, and A's next write lands in 2's scope. A web
# server is the deployment target, so concurrent requests are the normal case, not an
# edge one.
_bound = threading.local()


class TenantNotBound(RuntimeError):
    """Raised when a scoped operation is attempted with no tenant bound.

    Deliberately its own type. `sqlite3` would answer with "no such function:
    current_tenant" or an IntegrityError depending on where the call landed, and neither
    names the actual mistake -- which is that nobody said who was asking.
    """


def current_tenant() -> str | None:
    """The tenant bound on this thread, or None.

    Registered as the SQLite function of the same name, so this is literally what the
    triggers compare against. Returning None (SQL NULL) when nothing is bound is what
    makes the `IS NOT` guards refuse -- see the ADR's measurement of the `!=` fail-open
    case, which is why the operator in schema.py is not the obvious one.
    """
    return getattr(_bound, "tenant_id", None)


def require_tenant() -> str:
    """The bound tenant, or a refusal naming the mistake."""
    tenant_id = current_tenant()
    if not tenant_id:
        raise TenantNotBound(
            "no tenant is bound, so this operation has no scope. Wrap the call in "
            "`with engine.acting_as(tenant_id):`. Refused rather than defaulted to any "
            "tenant: a default scope is how one customer's request reads another's data."
        )
    return tenant_id


@contextlib.contextmanager
def acting_as(tenant_id: str) -> Iterator[str]:
    """Bind `tenant_id` for the duration of the block, then restore what was there.

    RESTORES RATHER THAN CLEARS, so nesting behaves: an inner block for a different
    tenant does not leave the outer block unscoped when it exits. And it restores in a
    `finally`, so an exception inside the block cannot leave a tenant bound for whatever
    runs next on this thread.
    """
    if not tenant_id or not tenant_id.strip():
        raise ValueError(
            "tenant_id may not be blank. \"\" is the single-tenant marker on RunState "
            "and is translated to tenant zero by tenancy.tenant_zero() -- it is never a "
            "scope in its own right, because a blank scope matches a blank column and "
            "that is a row nobody owns."
        )
    previous = getattr(_bound, "tenant_id", None)
    _bound.tenant_id = tenant_id
    try:
        yield tenant_id
    finally:
        _bound.tenant_id = previous


@contextlib.contextmanager
def acting_as_nobody() -> Iterator[None]:
    """Unbind for the duration of the block. For tests that assert the fail-closed path.

    Exists so a test can express "nothing established who is asking" without depending
    on being the first code to touch the thread. Without it, that case is only reachable
    by accident of ordering, which is not a case a suite can pin.
    """
    previous = getattr(_bound, "tenant_id", None)
    _bound.tenant_id = None
    try:
        yield
    finally:
        _bound.tenant_id = previous


def connect(path: str = ":memory:"):
    """A connection with the tenant seam wired, on either dialect.

    A `postgresql://` or `postgres://` value is a DSN and gets a psycopg connection;
    anything else is a filesystem path and gets sqlite3. Dispatching on the STRING rather
    than on a `dialect=` argument is deliberate: every existing caller passes a path and
    would otherwise have to learn a second parameter, and a caller who has a DSN in hand
    has already decided which backend they mean.

    ── WHY THIS FUNCTION HAD TO GROW A SECOND BRANCH ──

    It was `sqlite3.connect(path)` unconditionally, and the whole web stack ran against
    sqlite, so nothing noticed. MEASURED in a podman container with the app pointed at a
    real Postgres:

        File "/repo/agentorg/db/engine.py", line 126, in connect
          connection = sqlite3.connect(path)
        sqlite3.OperationalError: unable to open database file

    A DSN handed to `sqlite3.connect` is read as a FILENAME, so it tried to create a file
    called `postgresql://agentorg:...@postgres:5432/agentorg` and failed on the slashes.
    The error names the file layer and says nothing about a dialect, which is why the
    symptom reads as a permissions problem.

    Lane L recorded the same gap one layer up as limitations §17 — `migrations.migrate`
    accepts `dialect="postgres"` and then calls `connection.executescript`, which psycopg
    has no such method for. Same shape: an interface offering a backend the plumbing
    beneath it could not reach.

    ── THE TENANT SEAM DIFFERS BY DIALECT AND BOTH HALVES ARE REAL ──

    SQLite has no session variables, so `current_tenant()` is registered as a FUNCTION and
    the triggers call it. Postgres has no user-defined function registration from the
    client, so the tenant is a session setting and the RLS policies read
    `current_setting('agentorg.tenant_id', true)`.

    `acting_as` binds thread-local state either way; on Postgres that state has to be
    pushed into the session, which `bind_tenant` below does. A connection made here before
    `acting_as` runs therefore has no tenant bound — the same as sqlite, where
    `current_tenant()` returns None and the `IS NOT` guards refuse.

    AND ON POSTGRES THE POLICIES DO NOT CONSTRAIN A SUPERUSER OR THE TABLE'S OWNER. That
    is not this function's business to fix and it cannot: see `db/schema.py`'s measurement.
    A deployment must connect as a plain role.
    """
    if _is_dsn(path):
        import psycopg

        connection = psycopg.connect(path, autocommit=False, row_factory=_pg_row_factory())
        bind_tenant(connection)
        return connection

    # FOREIGN KEYS ARE OFF BY DEFAULT IN SQLITE -- measured, `PRAGMA foreign_keys` reads 0
    # on a fresh connection. So a `REFERENCES` clause in the schema is decoration until
    # this pragma runs, and a membership row could name an organisation that does not
    # exist. The schema's constraints are only real if every connection enables them,
    # which is why connections come from here rather than from `sqlite3.connect` at each
    # call site. (Postgres enforces them without asking, which is why the branch above
    # has no equivalent line.)
    #
    # `deterministic=False` on the registration is required and not a default: SQLite may
    # otherwise cache the result of a deterministic function within a statement, and this
    # function's whole purpose is to answer differently for different callers.
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.create_function(
        schema.SQLITE_TENANT_FUNCTION, 0, current_tenant, deterministic=False
    )
    return connection


def _is_dsn(value: str) -> bool:
    """Whether `value` names a Postgres server rather than a file.

    Both spellings, because `postgres://` is the older one libpq still accepts and a DSN
    copied from an existing deployment may use it. Matched at the start only: a FILE called
    `my-postgresql://notes.db` is a path, and a substring test would send it to psycopg.
    """
    return value.startswith(("postgresql://", "postgres://"))


def _pg_row_factory():
    """Rows as mappings, matching `sqlite3.Row`.

    Every accessor in `tenancy/` subscripts rows by column name (`row["tenant_id"]`), so a
    psycopg connection returning tuples would raise `TypeError: tuple indices must be
    integers` from inside code that is correct — the failure would name the accessor rather
    than the connection that shaped its input.
    """
    from psycopg.rows import dict_row

    return dict_row


def bind_tenant(connection) -> None:
    """Push the bound tenant into a Postgres session. A no-op for sqlite.

    THE RLS POLICIES READ A SESSION SETTING, so a thread-local nobody transferred is a
    thread-local the database cannot see — and the visible symptom is an empty result set,
    not an error. That is the worst available failure for a scoping mechanism: it looks
    like "this tenant has no rows".

    `set_config(..., false)` rather than `SET LOCAL`, because `false` means session-scoped
    rather than transaction-scoped: a connection is reused across transactions here, and a
    transaction-scoped value would silently lapse after the first commit.

    Called from `connect` and from `acting_as`, because the tenant may be bound before or
    after a connection is made and neither order is wrong.
    """
    if isinstance(connection, sqlite3.Connection):
        return
    tenant = current_tenant()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config(%s, %s, false)",
            (schema.POSTGRES_TENANT_SETTING, tenant or ""),
        )
