"""Tenant-scoped accessors. Every read and write takes a tenant. Lane B, B3.

OWNER: Lane B. See `ADR-001-database.md`.

THE ONE STRUCTURAL RULE: `scope` IS THE FIRST POSITIONAL PARAMETER OF EVERY ACCESSOR AND
HAS NO DEFAULT. A default tenant argument is how cross-tenant access happens by accident
-- the caller who forgets does not get an error, they get somebody else's data, and the
call site reads as correct. With no default, forgetting is a `TypeError` from Python
itself, at the call, naming the missing argument. That is a stronger guarantee than any
runtime check in here could be, because it cannot be reached around.

BOTH LAYERS, DELIBERATELY. Every accessor binds the tenant (so `schema.py`'s triggers are
live) AND carries an explicit `WHERE tenant_id = ?`. That is not belt-and-braces for its
own sake -- it is the ADR's measurement: SQLite cannot constrain a `SELECT`, so on the
tested path the predicate is the ONLY read defence, while Postgres RLS covers reads at the
database. Removing either half must fail a test, and
`tests/test_tenancy_leak.py` removes them one at a time to prove it.

A CROSS-TENANT READ RAISES; IT DOES NOT RETURN None. The ADR gives the reasoning at
length. In short: "assert the data is absent" passes when isolation works and equally when
the row was never written, the fixture is wrong, or the query is broken -- it cannot fail
for the right reason because it cannot tell those apart. A raised refusal can only come
from the code that refused.

THE REGISTRY IS DERIVED, NOT MAINTAINED. `@accessor(...)` registers each function as it is
defined, so `ACCESSORS` cannot fall behind the module: a new accessor is registered by the
decorator it needs anyway to be scoped. A hand-written list would drift, and the drift
would be silent in the worst direction -- the leak suite iterates this registry, so an
accessor missing from it is an accessor nobody attempts to breach.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from ..db import engine

READ = "read"
WRITE = "write"


class CrossTenantAccess(PermissionError):
    """A tenant named a resource that belongs to a different tenant.

    THE LOAD-BEARING PROPERTY IS THAT THIS IS RAISED AT ALL. Returning None here would be
    the untestable alternative -- see the module docstring.

    The message names only the id the CALLER supplied. It never carries a field from the
    other tenant's row, and never names the owning tenant: that would turn a refusal into
    the disclosure it exists to prevent, in the error text rather than in the data.
    """


class NotFound(LookupError):
    """The resource does not exist under any tenant.

    A different fact from `CrossTenantAccess`, and kept distinct on purpose. Collapsing
    the two would remove the leak suite's discriminator: a test could no longer tell "the
    guard refused me" from "there was nothing there", which is precisely the distinction
    that makes an attempted breach meaningful. The ADR costs this trade -- it is a narrow
    existence oracle over unguessable ids.
    """


@dataclass(frozen=True)
class TenantScope:
    """Who is asking, and over which connection.

    The two field names -- `connection` and `tenant_id` -- are the contract the rest of
    `agentorg/tenancy/` reads (`budgets.py` takes this shape), so they are not renamed
    casually.
    """

    connection: sqlite3.Connection
    tenant_id: str

    def __repr__(self) -> str:
        """Explicit, so a scope in a log line is a deliberate act.

        The default dataclass repr would embed the connection object's address and the
        tenant id, and `logging.debug("%s", scope)` is an easy line to add. The tenant id
        is not a secret, but a tenant identifier appearing in a shared log -- or on a pull
        request comment -- is a small disclosure nobody chose.
        """
        return f"TenantScope(tenant_id={self.tenant_id!r})"

    __str__ = __repr__


def scope_for(connection: sqlite3.Connection, tenant_id: str) -> TenantScope:
    """Build a scope, refusing a blank tenant.

    Blank is refused HERE as well as in `engine.acting_as`, because this is where a caller
    holding a `RunState.tenant_id` arrives. `""` is the single-tenant marker and must go
    through `tenant_zero.for_run_state` first; reaching this function with it means that
    translation was skipped, and a blank scope matches a blank column -- a row nobody owns.
    """
    if not tenant_id or not tenant_id.strip():
        raise ValueError(
            "tenant_id may not be blank. \"\" is the marker every pre-tenancy RunState "
            "carries; translate it with tenancy.tenant_zero.for_run_state() before "
            "building a scope. A blank scope matches a blank tenant column, which is a "
            "row nobody owns."
        )
    return TenantScope(connection=connection, tenant_id=tenant_id)


@dataclass(frozen=True)
class Accessor:
    """One registered accessor, described well enough to be driven generically."""

    name: str
    function: Callable
    table: str
    kind: Literal["read", "write"]


ACCESSORS: dict[str, Accessor] = {}


def accessor(*, table: str, kind: str):
    """Register a function as a tenant-scoped accessor.

    Registration happens at import, by the decorator the function needs anyway. So a new
    accessor appears in `ACCESSORS` automatically -- and the leak suite, which iterates
    this dict, covers it without anybody remembering to add it there.
    """
    if kind not in (READ, WRITE):
        raise ValueError(f"kind must be {READ!r} or {WRITE!r}, not {kind!r}")

    def register(function):
        if function.__name__ in ACCESSORS:
            raise ValueError(
                f"two accessors are named {function.__name__!r}; the second would "
                f"replace the first in ACCESSORS and would then never be breach-tested"
            )
        ACCESSORS[function.__name__] = Accessor(
            name=function.__name__, function=function, table=table, kind=kind
        )
        return function

    return register


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _one(scope: TenantScope, sql: str, params: tuple) -> sqlite3.Row | None:
    with engine.acting_as(scope.tenant_id):
        return scope.connection.execute(sql, params).fetchone()


def _all(scope: TenantScope, sql: str, params: tuple) -> list[dict]:
    with engine.acting_as(scope.tenant_id):
        return [dict(row) for row in scope.connection.execute(sql, params).fetchall()]


def _write(scope: TenantScope, sql: str, params: tuple) -> None:
    """Run a scoped write. The tenant is bound, so the triggers are live.

    WHAT A TRIGGER REFUSAL LOOKS LIKE FROM PYTHON, MEASURED: `RAISE(ABORT, ...)` surfaces
    as `sqlite3.IntegrityError`, NOT `OperationalError` --

        exception type: IntegrityError
        is IntegrityError: True
        is OperationalError: False

    That matters at this layer because it is the one that could grow a `try/except` around
    the execute. Catching `OperationalError` here would look like handling a refusal and
    would catch nothing, so every cross-tenant write would raise straight through a handler
    written to manage it; catching `IntegrityError` and returning quietly would be worse --
    it would turn a refused breach into a silent no-op the caller reads as success.

    Deliberately NEITHER is caught. A refused write must reach the caller. The distinct
    `no such function: current_tenant` (an `OperationalError`) is what a connection built
    outside `engine.connect` produces, and that too must not be swallowed: it means the
    guards are absent entirely.
    """
    with engine.acting_as(scope.tenant_id):
        scope.connection.execute(sql, params)
    scope.connection.commit()


def _require(
    scope: TenantScope,
    table: str,
    key: str,
    value: str,
    tenant_column: str = "tenant_id",
    *,
    oracle_safe: bool = True,
) -> dict:
    """Fetch one row in scope, or say which refusal applies.

    THE SCOPED QUERY RUNS FIRST. Only when it finds nothing does an unscoped existence
    probe run, and its result is used solely to choose between two refusals -- never to
    return data. Written the other way round, the unscoped read would be the primary path
    and the scope a filter applied afterwards, which is one careless `return` away from
    handing over the row.

    `oracle_safe` DECIDES WHETHER THE CALLER MAY LEARN THAT THE ID EXISTS ELSEWHERE, and
    it is a per-accessor decision because the ADR's trade is only sound for unguessable
    ids. A run id and a repository id are UUID-shaped, so `CrossTenantAccess` tells an
    attacker nothing they could enumerate. A secret NAME is not: `GITHUB_TOKEN` will exist
    for every tenant, so distinguishing the two cases would answer "does that tenant hold
    this credential" for any name somebody can guess. A `user_id` is worse -- it answers a
    question about a PERSON, namely whether they belong to another organisation.

    So `oracle_safe=False` collapses both cases into `NotFound`: the caller learns only
    that they cannot see it, which is the honest answer and carries no signal. The cost is
    that the leak suite cannot use the exception TYPE as its discriminator on those
    accessors, which is why it asserts a refusal was raised and separately asserts the
    rightful owner CAN read the same row -- a positive control, not a type check.
    """
    row = _one(
        scope,
        f'SELECT * FROM "{table}" WHERE "{key}" = ? AND "{tenant_column}" = ?',
        (value, scope.tenant_id),
    )
    if row is not None:
        return dict(row)

    if not oracle_safe:
        raise NotFound(
            f"no {table} with {key}={value!r} in this tenant's scope. Whether one exists "
            f"under another tenant is deliberately NOT distinguished here: {key} is "
            f"guessable, so the distinction would itself be the disclosure."
        )

    exists = scope.connection.execute(
        f'SELECT 1 FROM "{table}" WHERE "{key}" = ?', (value,)
    ).fetchone()
    if exists is None:
        raise NotFound(
            f"no {table} with {key}={value!r} exists. (Distinct from a cross-tenant "
            f"refusal on purpose -- see accessors.NotFound.)"
        )
    raise CrossTenantAccess(
        # THE KEY IS ECHOED, AND FOR TWO TABLES THAT KEY IS ITSELF A TENANT ID.
        # `organisation` and `budget` are keyed BY tenant, so naming the row necessarily
        # names its owner -- there is no version of this message that identifies the
        # resource without doing so. That is not a disclosure: the caller supplied the
        # value, so they already had it, and learning "the tenant I named is not me" tells
        # them nothing they did not type.
        #
        # An earlier draft of this message asserted the owning tenant was NOT named, which
        # was false for exactly those two tables. The leak suite caught the contradiction;
        # the fix is an honest message rather than a hidden id, because a refusal that
        # misdescribes itself is worse than one that says more than it must.
        f"{table} {value!r} is outside this tenant's scope. No FIELD of that row is "
        f"included here -- only the identifier the caller supplied, which for a "
        f"tenant-keyed table is necessarily a tenant id."
    )


# ──────────────────────────────────────────────────────────────────────────────
# organisation -- self-scoped: a row IS a tenant
# ──────────────────────────────────────────────────────────────────────────────

@accessor(table="organisation", kind=READ)
def get_organisation(scope: TenantScope, organisation_id: str) -> dict:
    """The organisation, which for a tenant is only ever its own row.

    Takes the id explicitly rather than reading `scope.tenant_id`, and that is not
    redundancy: it is what makes the accessor BREACHABLE by a test. An accessor that can
    only ever be asked about itself cannot express the attempt, so the leak suite could
    not attempt it -- and an untestable guard is the pattern this repository records nine
    times over.
    """
    return _require(scope, "organisation", "id", organisation_id, tenant_column="id")


@accessor(table="organisation", kind=WRITE)
def update_organisation_name(scope: TenantScope, organisation_id: str, name: str) -> None:
    _require(scope, "organisation", "id", organisation_id, tenant_column="id")
    _write(
        scope,
        'UPDATE "organisation" SET "name" = ? WHERE "id" = ? AND "id" = ?',
        (name, organisation_id, scope.tenant_id),
    )


# ──────────────────────────────────────────────────────────────────────────────
# membership -- and the only route to app_user
# ──────────────────────────────────────────────────────────────────────────────

@accessor(table="membership", kind=READ)
def list_members(scope: TenantScope) -> list[dict]:
    """The tenant's members, joined to the global identity table.

    THE JOIN IS WHY `app_user` NEEDS NO SCOPE OF ITS OWN. `app_user` is a global identity
    -- one person may belong to several organisations -- so a tenant column there would be
    a lie. Instead there is no accessor that reads it directly, and this one filters on
    `membership.tenant_id`, so the reachable set is exactly this tenant's people. That
    closes the enumeration surface without pretending a global table is scoped.

    THE COLUMNS ARE NAMED, NEVER `SELECT *`, AND THAT IS NOT STYLE. MEASURED:
    `dict(sqlite3.Row)` SILENTLY COLLAPSES DUPLICATE COLUMN NAMES, keeping the first of
    each pair, with nothing raised. `membership` and `app_user` both carry `id` and
    `created_at`, so an unaliased join gives:

        row.keys() : ['id','tenant_id','user_id','created_at','id','email','created_at']
                     -> 7 entries
        dict(row)  : {'id':'m1','tenant_id':'t1','user_id':'u1',
                      'created_at':'2026-01-01','email':'a@example.com'}
                     -> 5 entries

    The user's `id` is simply gone, and the dict that comes back is plausible -- it has an
    `id`, it has an `email`, it looks like a member. A wrong answer that looks like a right
    one is the failure shape this project documents most, and here it would hollow out the
    isolation assertion above: a listing that lost `user_id` makes
    "the victim's user is not in the attacker's list" true for the wrong reason.

    `tests/test_tenancy_leak.py::test_the_member_listing_does_not_lose_columns_to_a_name_collision`
    pins the returned key set, so restoring a `SELECT *` here fails by name.
    """
    return _all(
        scope,
        'SELECT m."id", m."user_id", m."role", u."email" '
        'FROM "membership" m JOIN "app_user" u ON u."id" = m."user_id" '
        'WHERE m."tenant_id" = ? ORDER BY u."email"',
        (scope.tenant_id,),
    )


@accessor(table="membership", kind=READ)
def get_member(scope: TenantScope, membership_id: str) -> dict:
    """One membership row.

    `oracle_safe=False`: a membership names a PERSON, so distinguishing "outside your
    scope" from "does not exist" would answer whether that person belongs to another
    organisation -- information about a human being, not about a resource.
    """
    return _require(scope, "membership", "id", membership_id, oracle_safe=False)


@accessor(table="membership", kind=WRITE)
def add_member(scope: TenantScope, membership_id: str, user_id: str, role: str) -> None:
    _write(
        scope,
        'INSERT INTO "membership" ("id", "tenant_id", "user_id", "role", "created_at") '
        "VALUES (?, ?, ?, ?, ?)",
        (membership_id, scope.tenant_id, user_id, role, _now()),
    )


@accessor(table="membership", kind=WRITE)
def remove_member(scope: TenantScope, membership_id: str) -> None:
    _require(scope, "membership", "id", membership_id)
    _write(
        scope,
        'DELETE FROM "membership" WHERE "id" = ? AND "tenant_id" = ?',
        (membership_id, scope.tenant_id),
    )


# ──────────────────────────────────────────────────────────────────────────────
# repository
# ──────────────────────────────────────────────────────────────────────────────

@accessor(table="repository", kind=READ)
def list_repositories(scope: TenantScope) -> list[dict]:
    return _all(
        scope,
        'SELECT * FROM "repository" WHERE "tenant_id" = ? ORDER BY "full_name"',
        (scope.tenant_id,),
    )


@accessor(table="repository", kind=READ)
def get_repository(scope: TenantScope, repository_id: str) -> dict:
    return _require(scope, "repository", "id", repository_id)


@accessor(table="repository", kind=WRITE)
def add_repository(scope: TenantScope, repository_id: str, full_name: str) -> None:
    _write(
        scope,
        'INSERT INTO "repository" ("id", "tenant_id", "full_name", "created_at") '
        "VALUES (?, ?, ?, ?)",
        (repository_id, scope.tenant_id, full_name, _now()),
    )


# ──────────────────────────────────────────────────────────────────────────────
# run -- an INDEX by tenant, not a third writer of RunState
# ──────────────────────────────────────────────────────────────────────────────

@accessor(table="run", kind=READ)
def list_runs(scope: TenantScope) -> list[dict]:
    return _all(
        scope,
        'SELECT * FROM "run" WHERE "tenant_id" = ? ORDER BY "created_at" DESC',
        (scope.tenant_id,),
    )


@accessor(table="run", kind=READ)
def get_run(scope: TenantScope, run_id: str) -> dict:
    return _require(scope, "run", "run_id", run_id)


@accessor(table="run", kind=WRITE)
def record_run(
    scope: TenantScope,
    run_id: str,
    ticket_id: str,
    status: str,
    state_ref: str | None = None,
) -> None:
    """Index a run against this tenant.

    `state_ref` records WHERE the run's state document lives -- a path or a
    `dynamodb://table/run_id` -- the way `gates.StateRef` formats itself. This table does
    not store the document: `gates.save` is deliberately the one place a RunState is
    serialized, and a second writer is how that guarantee quietly ends.
    """
    _write(
        scope,
        'INSERT INTO "run" '
        '("run_id", "tenant_id", "ticket_id", "status", "created_at", "state_ref") '
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, scope.tenant_id, ticket_id, status, _now(), state_ref),
    )


@accessor(table="run", kind=WRITE)
def update_run_status(scope: TenantScope, run_id: str, status: str) -> None:
    _require(scope, "run", "run_id", run_id)
    _write(
        scope,
        'UPDATE "run" SET "status" = ? WHERE "run_id" = ? AND "tenant_id" = ?',
        (status, run_id, scope.tenant_id),
    )


# ──────────────────────────────────────────────────────────────────────────────
# secret -- ENCRYPTED COLUMNS ONLY. No crypto happens in this module.
# ──────────────────────────────────────────────────────────────────────────────

@accessor(table="secret", kind=READ)
def list_secret_names(scope: TenantScope) -> list[str]:
    """The NAMES only. Deliberately not the rows.

    Listing a secret's ciphertext to answer "what secrets do I have" would put encrypted
    material through every layer above this one -- logs, API responses, a UI's state --
    for a question that never needed it.
    """
    rows = _all(
        scope,
        'SELECT "name" FROM "secret" WHERE "tenant_id" = ? ORDER BY "name"',
        (scope.tenant_id,),
    )
    return [row["name"] for row in rows]


@accessor(table="secret", kind=READ)
def get_secret_row(scope: TenantScope, name: str) -> dict:
    """The four encrypted columns for one secret. Never a plaintext.

    This module does no crypto: `tenancy.crypto` decrypts, and it is the caller's job to
    ask it to. Keeping the two apart means a change to storage cannot quietly change what
    is encrypted, and `EncryptedRecord`'s field names already match these columns, so
    there is no mapping layer free to drift.
    """
    row = _one(
        scope,
        'SELECT "nonce", "ciphertext", "mac", "cipher" FROM "secret" '
        'WHERE "name" = ? AND "tenant_id" = ?',
        (name, scope.tenant_id),
    )
    if row is not None:
        return dict(row)

    exists = scope.connection.execute(
        'SELECT 1 FROM "secret" WHERE "name" = ?', (name,)
    ).fetchone()
    if exists is None:
        raise NotFound(f"no secret named {name!r} exists for any tenant")
    # A SECRET NAME IS GUESSABLE, which makes this the one refusal that must NOT
    # distinguish the two cases. `DEMO_GITHUB_TOKEN` will exist for every tenant, so
    # answering "that name is outside your scope" would confirm, for any name somebody can
    # think of, whether another tenant holds that credential. Both cases therefore raise
    # NotFound and the caller learns only that they cannot see it.
    raise NotFound(
        f"no secret named {name!r} in this tenant's scope. Whether another tenant holds "
        f"a secret of this name is deliberately NOT distinguished: names are unique per "
        f"tenant rather than globally, and a common name like a CI token's would "
        f"otherwise be an oracle over every tenant."
    )


@accessor(table="secret", kind=WRITE)
def put_secret(
    scope: TenantScope,
    secret_id: str,
    name: str,
    nonce: str,
    ciphertext: str,
    mac: str,
    cipher: str,
) -> None:
    """Store the encrypted parts. There is no parameter for a plaintext.

    The signature is the guard: a caller cannot hand this function a raw token even by
    mistake, because no argument accepts one. Encrypt first, then store what comes back.
    """
    _write(
        scope,
        'INSERT INTO "secret" '
        '("id", "tenant_id", "name", "nonce", "ciphertext", "mac", "cipher", '
        '"created_at") VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (secret_id, scope.tenant_id, name, nonce, ciphertext, mac, cipher, _now()),
    )


@accessor(table="secret", kind=WRITE)
def delete_secret(scope: TenantScope, name: str) -> None:
    get_secret_row(scope, name)
    _write(
        scope,
        'DELETE FROM "secret" WHERE "name" = ? AND "tenant_id" = ?',
        (name, scope.tenant_id),
    )


# ──────────────────────────────────────────────────────────────────────────────
# budget -- the arithmetic lives in budgets.py; these are the rows
# ──────────────────────────────────────────────────────────────────────────────

@accessor(table="budget", kind=READ)
def get_budget(scope: TenantScope, tenant_id: str) -> dict:
    """The budget row. Takes the tenant id explicitly so the breach is expressible.

    Same reasoning as `get_organisation`: an accessor that can only be asked about itself
    cannot be attacked by a test, and therefore cannot be shown to defend anything.
    """
    return _require(scope, "budget", "tenant_id", tenant_id)


@accessor(table="budget", kind=WRITE)
def set_budget(
    scope: TenantScope,
    tenant_id: str,
    ceiling_cents: int,
    unlimited: bool = False,
) -> None:
    if ceiling_cents < 0:
        raise ValueError(
            f"ceiling_cents={ceiling_cents} is negative. A negative ceiling refuses "
            f"every run, which reads on a dashboard as an outage rather than as a "
            f"misconfiguration."
        )
    existing = scope.connection.execute(
        'SELECT 1 FROM "budget" WHERE "tenant_id" = ?', (tenant_id,)
    ).fetchone()
    if existing is None:
        _write(
            scope,
            'INSERT INTO "budget" ("tenant_id", "ceiling_cents", "spent_cents", '
            '"unlimited", "updated_at") VALUES (?, ?, ?, ?, ?)',
            (tenant_id, ceiling_cents, 0, 1 if unlimited else 0, _now()),
        )
        return

    _require(scope, "budget", "tenant_id", tenant_id)
    _write(
        scope,
        'UPDATE "budget" SET "ceiling_cents" = ?, "unlimited" = ?, "updated_at" = ? '
        'WHERE "tenant_id" = ? AND "tenant_id" = ?',
        (ceiling_cents, 1 if unlimited else 0, _now(), tenant_id, scope.tenant_id),
    )


@accessor(table="budget", kind=WRITE)
def add_spend(scope: TenantScope, tenant_id: str, cents: int) -> None:
    """Record spend. Refuses a negative amount.

    A negative spend is a credit, and a credit applied through the same path as a charge
    is how a tenant's total drifts below what it actually owes -- silently, since both
    read as ordinary updates.
    """
    if cents < 0:
        raise ValueError(
            f"cents={cents} is negative; a refund is not a spend and must not travel "
            f"through the same path as a charge."
        )
    _require(scope, "budget", "tenant_id", tenant_id)
    _write(
        scope,
        'UPDATE "budget" SET "spent_cents" = "spent_cents" + ?, "updated_at" = ? '
        'WHERE "tenant_id" = ? AND "tenant_id" = ?',
        (cents, _now(), tenant_id, scope.tenant_id),
    )


def reads() -> list[Accessor]:
    return [a for a in ACCESSORS.values() if a.kind == READ]


def writes() -> list[Accessor]:
    return [a for a in ACCESSORS.values() if a.kind == WRITE]
