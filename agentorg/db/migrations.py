"""Forward-only migrations, runnable against an empty database.

OWNER: Lane B. B2.

FORWARD ONLY, AND NO DOWN MIGRATIONS. A down migration for a schema carrying customer
data is a script that deletes it, kept next to the script that creates it, and the only
time anyone reaches for one is under pressure. Rolling back a bad migration is a restore
plus a new forward migration -- slower, and it cannot destroy a tenant's runs by being
run in the wrong direction.

IDEMPOTENT BY CONSTRUCTION. Every statement is `IF NOT EXISTS`, and `applied_migration`
records what has run, so `migrate()` on an up-to-date database is a no-op that says so.
Both halves are needed: the ledger without `IF NOT EXISTS` breaks if the ledger is lost,
and `IF NOT EXISTS` without the ledger cannot tell "already applied" from "never needed".

WHY THE LEDGER STORES A CHECKSUM. A migration edited after it was applied is the failure
that produces two databases with the same version number and different shapes -- and
nothing about the ledger's version column can see it. The checksum can, so `migrate()`
refuses rather than continuing against a schema it cannot describe.

IT RUNS ON POSTGRES NOW, AND UNTIL 2026-09-09 IT COULD NOT (Lane R)
==================================================================
This module accepted `dialect="postgres"` and then called `connection.executescript` at
three sites. **psycopg has no such method** -- measured on this host against psycopg
3.3.4:

    sqlite3 Connection has executescript: True
    psycopg Connection has executescript: False

So everything above -- the forward-only ledger, the checksum guard, the idempotency --
had never executed against PostgreSQL. Every Postgres verification in this repository
piped `schema.render_schema(POSTGRES)` into `psql` and bypassed the runner entirely, so a
Postgres deployment got the schema and no version history: `applied_migration` did not
exist, and nothing could say which shape that database was.

The fix is at the EXECUTE BOUNDARY and not at the three call sites -- `db/_dialect.py`,
following `queue/_sql.py:_sql`, "because every method would otherwise carry the same
conditional, and one of them would eventually be written with the wrong placeholder and
fail only against the dialect nobody was testing". The parameter is also now REFUSED when
it disagrees with the connection, which is the original defect turned into a message.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from . import schema
from ._dialect import Connection, query, run_script, scalar, sql

LEDGER_TABLE = "applied_migration"

# The ledger is NOT tenant-scoped and does not go through `schema.Table`: it describes
# the database itself rather than anything a tenant owns. Kept here rather than in
# schema.py so that `schema.TABLES` stays exactly "the tables tenants have data in",
# which is the set the leak suite enumerates. A bookkeeping table in that set would be a
# permanent exception in every isolation test.
_LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS "{LEDGER_TABLE}" (
  "version" INTEGER PRIMARY KEY,
  "name" TEXT NOT NULL,
  "checksum" TEXT NOT NULL,
  "applied_at" TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sqlite: str
    postgres: str

    def sql(self, dialect: str) -> str:
        if dialect == schema.SQLITE:
            return self.sqlite
        if dialect == schema.POSTGRES:
            return self.postgres
        raise ValueError(
            f"unknown dialect {dialect!r}; expected one of "
            f"{', '.join(schema.DIALECTS)}"
        )

    def checksum(self, dialect: str) -> str:
        """A digest of the SQL this migration will run, per dialect.

        Per-dialect deliberately: the same version renders differently for SQLite and
        Postgres, so one checksum over both would change when either changed and could
        not say which. The ledger lives in one database and records that database's
        dialect.
        """
        return hashlib.sha256(self.sql(dialect).encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# THE MIGRATIONS.
#
# 0001 is the whole schema, generated from `schema.py` rather than transcribed. A
# transcribed copy is a second declaration of the schema and the two drift silently --
# the first migration says one thing, `schema.py` says another, and the tests read
# whichever one the author was thinking about.
#
# Migration 0002 onward will be hand-written ALTERs, because that is what a change to a
# live table is. Only the initial creation can honestly be generated.
# ──────────────────────────────────────────────────────────────────────────────

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="tenancy_schema",
        sqlite=schema.render_schema(schema.SQLITE),
        postgres=schema.render_schema(schema.POSTGRES),
    ),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def applied_versions(
    connection: Connection, dialect: str = schema.SQLITE
) -> list[int]:
    """Which migrations this database has already run, in order.

    `dialect` is additive with the sqlite default, so every existing caller is unchanged.
    It is not optional in effect: `run_script` refuses a dialect the connection cannot
    honour, so a psycopg connection left on the default fails by name here rather than
    with an `AttributeError` about `executescript`.
    """
    run_script(connection, _LEDGER_DDL, dialect)
    rows = query(
        connection,
        f'SELECT "version" FROM "{LEDGER_TABLE}" ORDER BY "version"',
        (),
        dialect,
    )
    return [scalar(row) for row in rows]


def migrate(connection: Connection, dialect: str = schema.SQLITE) -> list[int]:
    """Apply every unapplied migration. Returns the versions applied, possibly empty.

    RETURNS THE LIST RATHER THAN A COUNT, so a caller and a test can tell "nothing to do"
    from "applied one" from "applied three" -- a count of 0 and an empty run are the same
    integer but not the same event, and the migration path is one where "did nothing" must
    be distinguishable from "did the work".
    """
    run_script(connection, _LEDGER_DDL, dialect)
    already = set(applied_versions(connection, dialect))
    applied: list[int] = []

    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        expected = migration.checksum(dialect)
        if migration.version in already:
            rows = query(
                connection,
                f'SELECT "checksum" FROM "{LEDGER_TABLE}" WHERE "version" = ?',
                (migration.version,),
                dialect,
            )
            recorded = scalar(rows[0]) if rows else None
            if recorded is not None and recorded != expected:
                # REFUSED RATHER THAN SKIPPED. A migration whose text changed after it
                # ran means this database's shape is not the one the code describes, and
                # continuing would build later migrations on an unknown base. The
                # honest recovery is a new forward migration, which is why there is no
                # flag to force past this.
                raise RuntimeError(
                    f"migration {migration.version} ({migration.name}) was applied with "
                    f"a different definition than the one in this build: recorded "
                    f"{recorded[:12]}, now {expected[:12]}. This database's "
                    f"shape is not the one the code describes. Add a NEW forward "
                    f"migration rather than editing an applied one."
                )
            continue

        run_script(connection, migration.sql(dialect), dialect)
        connection.execute(
            sql(
                f'INSERT INTO "{LEDGER_TABLE}" '
                f'("version", "name", "checksum", "applied_at") VALUES (?, ?, ?, ?)',
                dialect,
            ),
            (migration.version, migration.name, expected, _now()),
        )
        applied.append(migration.version)

    connection.commit()
    return applied
