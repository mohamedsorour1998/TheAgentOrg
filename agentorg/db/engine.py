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


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """A connection with `current_tenant()` registered and foreign keys ON.

    FOREIGN KEYS ARE OFF BY DEFAULT IN SQLITE -- measured, `PRAGMA foreign_keys` reads 0
    on a fresh connection. So a `REFERENCES` clause in the schema is decoration until
    this pragma runs, and a membership row could name an organisation that does not
    exist. The schema's constraints are only real if every connection enables them,
    which is why connections come from here rather than from `sqlite3.connect` at each
    call site.

    `deterministic=False` on the registration is required and not a default: SQLite may
    otherwise cache the result of a deterministic function within a statement, and this
    function's whole purpose is to answer differently for different callers.
    """
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.create_function(
        schema.SQLITE_TENANT_FUNCTION, 0, current_tenant, deterministic=False
    )
    return connection
