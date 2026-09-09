"""The two-role model: migrate as the OWNER, connect as somebody else.

OWNER: Lane R. CLAUDE.md open item 1. This is the database half; the identity half was
closed from the other end when Lane P put a signature-verified `custom:tenant` claim on
the Cognito ID token, so nothing has to read `membership` to discover a tenant any more.

=============================================================================
WHY A MODULE AND NOT THREE LINES IN A RUNBOOK
=============================================================================
`db/schema.py` emits `ENABLE` and `FORCE ROW LEVEL SECURITY` and one policy per scoped
table, and all of it is decoration unless the connection is a role Postgres actually
applies policies to. Postgres skips RLS for a **superuser**, for any role with
**BYPASSRLS**, and for the table's **OWNER**; `FORCE` closes only the third. Nothing in
SQL can constrain a superuser.

MEASURED 2026-09-09 against the shipped container (PostgreSQL 17.11), two tenants, one
run each, the same six policies, two connections. Probe 1 goes through
`accessors.list_runs`, which carries its own `WHERE tenant_id = ?`; probes 2-4 are the
raw SQL any non-accessor writes -- the queue, a reader, a psql session, or the next
accessor somebody writes without the predicate:

    as the superuser that owns the tables (agentorg, the DSN the container ships)
      1. through accessors.list_runs        ['run-t-attacker']
      2. RAW SELECT, t-attacker bound       ['run-t-attacker', 'run-t-victim']   <- LEAK
      3. RAW SELECT, no tenant bound        2 rows                               <- LEAK
      4. WRITE a row into t-victim          ACCEPTED                             <- BREACH

    as a plain LOGIN role, USAGE + SELECT/INSERT/UPDATE/DELETE granted
      1. through accessors.list_runs        ['run-t-attacker']
      2. RAW SELECT, t-attacker bound       ['run-t-attacker']
      3. RAW SELECT, no tenant bound        0 rows
      4. WRITE a row into t-victim          REFUSED -- new row violates row-level
                                            security policy for table "run"

**PROBE 1 IS THE SAME ON BOTH ROLES, AND THAT IS THE FINDING.** The accessor's own
predicate MASKS the leak, so a breach attempt driven only through the accessors reports
identical, healthy answers for a configuration that leaks everything to anything else in
the process. `tests/test_tenancy_leak.py` drives the accessors and is right to; it simply
cannot see this. Only the raw read separates the two roles.

=============================================================================
THE TWO GRANT TRAPS, AND WHY THIS DOES NOT RENDER `GRANT ... ON ALL TABLES`
=============================================================================
1. `GRANT ... ON ALL TABLES IN SCHEMA public` applies only to the tables that exist AT
   THAT MOMENT. Granting before a second schema file runs leaves the later tables
   invisible, and the error is `relation "sessions" does not exist` -- which reads as a
   missing migration rather than a missing grant. So `provision` READS `pg_tables`,
   refuses when a table it was told to expect is absent, and returns the list it actually
   granted. The trap becomes a report an operator can compare.
2. Table grants are useless without `GRANT USAGE ON SCHEMA public`. CLAUDE.md records
   `current_schemas(true)` answering `{pg_catalog}` for the app role. That measurement
   does NOT reproduce on PostgreSQL 17.11: `public` still carries `=U/pg_database_owner`
   -- USAGE to PUBLIC -- so `current_schemas(true)` reads `{pg_catalog,public}` with or
   without an explicit grant. The statement stays in the sequence because a hardened
   deployment revokes that PUBLIC grant, and then the trap is exactly as recorded.

CREATE IS DELIBERATELY NOT GRANTED, and one measured consequence follows from it:
`CREATE TABLE IF NOT EXISTS` checks the PRIVILEGE BEFORE the existence. Measured on the
same server, against a table that already existed:

    ERROR:  permission denied for schema public
    LINE 1: CREATE TABLE IF NOT EXISTS probe_target (x integer);

So any module that re-runs its own DDL on every connection cannot run as this role.
`queue/_sql.py:_ensure_schema` does exactly that. See `QUEUE_NOTE`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from . import schema
from ._dialect import Connection, column, query, require_matching_driver, run_script
from .migrations import LEDGER_TABLE

# The role the application connects as. A name, not a secret: the password belongs to the
# operator's secret store and is deliberately absent from this module -- `provision`
# REFUSES a role that does not exist rather than creating one, so no password is ever
# built into a string here, logged by a caller, or typed into a test fixture.
APP_ROLE = "agentorg_app"

# The tables a full deployment must hold before the grants are worth running. Ours are
# derived from `schema.TABLES`; the rest are named by the CALLER, because they come from
# files this module does not own -- `web/lib/schema.sql` and `queue/_sql.py:_SCHEMA`.
# Naming them in the call is what makes trap 1 visible: a table nobody listed is a table
# nobody granted, and `provision` says which.
WEB_TABLES: tuple[str, ...] = (
    "users", "accounts", "sessions", "verification_token", "web_identity",
)
QUEUE_TABLES: tuple[str, ...] = ("queue_jobs",)

QUEUE_NOTE = (
    "queue/_sql.py:_ensure_schema runs CREATE TABLE IF NOT EXISTS and two CREATE INDEX "
    "IF NOT EXISTS on EVERY SqlQueue() construction. Postgres checks the privilege "
    "before the existence, so those statements fail for a role without CREATE on schema "
    "public even though the table is already there -- and CREATE INDEX IF NOT EXISTS "
    "fails a second way, with 'must be owner of table queue_jobs'. Granting CREATE to "
    "the app role would give it a way to own objects, and an owner is exempt from its "
    "own table's policies. The fix belongs in that module: create the queue schema as "
    "the owner during provisioning, and let _ensure_schema skip the DDL when the table "
    "already exists. agentorg/queue/** is Lane A's file."
)


@dataclass(frozen=True)
class Escapes:
    """The three ways Postgres skips RLS, measured for one role.

    A DATACLASS AND NOT A BOOLEAN, because "RLS does not bind for this role" is three
    different facts with three different fixes, and a single `False` sends an operator
    looking at the wrong one. `pg_policies` lists every policy under all three.
    """

    role: str
    superuser: bool
    bypassrls: bool
    owns: tuple[str, ...]

    @property
    def binds(self) -> bool:
        return not (self.superuser or self.bypassrls or self.owns)

    def why_not(self) -> str:
        """Every reason RLS does not bind, or a sentence saying it does."""
        if self.binds:
            return f"row-level security binds for {self.role!r}"
        reasons = []
        if self.superuser:
            reasons.append("it is a SUPERUSER, and nothing in SQL can constrain one")
        if self.bypassrls:
            reasons.append("it holds BYPASSRLS")
        if self.owns:
            reasons.append(
                f"it OWNS {len(self.owns)} of the scoped tables ({', '.join(self.owns)}); "
                f"FORCE ROW LEVEL SECURITY is emitted and closes this one, but only this one"
            )
        return (
            f"row-level security does NOT bind for {self.role!r}: "
            + "; ".join(reasons)
            + ". Every policy is still listed in pg_policies."
        )


def _quote_ident(name: str) -> str:
    """A SQL identifier. Refuses anything that would need escaping.

    Role and table names here come from `pg_tables` and from this module's own constants,
    never from a request -- so the honest move is to REFUSE an unexpected shape rather
    than to build an escaping routine nobody exercises.

    `isascii()` IS LOAD-BEARING AND IT WAS A TEST THAT SAID SO. `'ô'.isalnum()` is True in
    Python -- the same Unicode-awareness that makes `github_ops._ISSUE_REF` spell its
    character class `[0-9]` rather than `\\d`, because `\\d` matches Arabic-Indic digits.
    A first draft accepted `rôle` here, and the next thing this function's output does is
    become DDL.
    """
    if not name or not all(c.isascii() and (c.isalnum() or c == "_") for c in name):
        raise ValueError(
            f"{name!r} is not a plain identifier. This module interpolates identifiers "
            f"into DDL -- GRANT takes no parameters -- so it accepts only "
            f"[A-Za-z0-9_]."
        )
    return f'"{name}"'


def render_create_role(role: str = APP_ROLE) -> str:
    """The one statement an operator runs by hand, with the password left as a placeholder.

    NOT EXECUTED BY THIS MODULE. A function that took a password would put one into a
    call stack, a log line and eventually a test fixture; `provision` refuses an absent
    role and prints this instead.
    """
    return (
        f"CREATE ROLE {_quote_ident(role)} LOGIN PASSWORD '<from your secret store>';"
        f"  -- NOT superuser, NOT createdb, NOT bypassrls, and NOT the migrator"
    )


def render_grants(role: str, tables: Sequence[str]) -> str:
    """USAGE on the schema, then DML on each named table. One statement per table.

    PER TABLE AND NOT `ON ALL TABLES`, for trap 1: the plural form silently covers only
    what exists when it runs, so the set it granted is unrecoverable afterwards. Written
    out, the grant IS the list.

    DELETE is included because two accessors need it -- `accessors.remove_member` and
    `accessors.delete_secret` -- and the RLS policy is what stops either reaching another
    tenant's row. TRUNCATE and REFERENCES are not: neither is used, and TRUNCATE is not
    constrained by a row policy at all.
    """
    lines = [f"GRANT USAGE ON SCHEMA public TO {_quote_ident(role)};"]
    lines += [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_quote_ident(t)} TO {_quote_ident(role)};"
        for t in tables
    ]
    return "\n".join(lines) + "\n"


def tables_present(connection: Connection, dialect: str = schema.POSTGRES) -> tuple[str, ...]:
    """Every table in `public`, in name order. Read, never assumed."""
    require_matching_driver(connection, dialect)
    rows = query(
        connection,
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename",
        (),
        dialect,
    )
    # BY POSITION, NOT BY NAME, and that is a measured fix rather than a style
    # choice. `row["tablename"]` requires psycopg's `dict_row` factory, which
    # `engine.connect` sets — but this function takes ANY connection, and the
    # provisioning sequence is run by an operator holding a plain
    # `psycopg.connect(...)`. Measured, doing exactly that:
    #
    #     TypeError: tuple indices must be integers or slices, not str
    #
    # raised from inside this module, naming the row rather than the row factory
    # nobody set. The query selects one column, so position 0 is unambiguous and
    # works under every factory including sqlite3.Row.
    return tuple(column(row, "tablename", 0) for row in rows)


def escapes_for(
    connection: Connection, role: str, dialect: str = schema.POSTGRES
) -> Escapes:
    """Which of the three RLS escapes `role` holds. Ownership is checked per scoped table."""
    require_matching_driver(connection, dialect)
    rows = query(
        connection,
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = ?",
        (role,),
        dialect,
    )
    if not rows:
        raise LookupError(
            f"no role named {role!r} exists in this database. Create it first:\n  "
            f"{render_create_role(role)}"
        )
    owned = query(
        connection,
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public' AND tableowner = ? ORDER BY tablename",
        (role,),
        dialect,
    )
    scoped = {t.name for t in schema.SCOPED_TABLES}
    return Escapes(
        role=role,
        # `column`, not `row["name"]`. See its docstring: an operator runs this with a
        # plain `psycopg.connect(...)`, which returns tuples, and the subscript raises
        # TypeError from inside this module while naming the row.
        superuser=bool(column(rows[0], "rolsuper", 0)),
        bypassrls=bool(column(rows[0], "rolbypassrls", 1)),
        owns=tuple(t for t in (column(r, "tablename", 0) for r in owned) if t in scoped),
    )


def provision(
    connection: Connection,
    *,
    role: str = APP_ROLE,
    required: Iterable[str] = (),
    dialect: str = schema.POSTGRES,
) -> dict:
    """Grant `role` DML on every table that exists, and refuse if the schema is incomplete.

    RUN AS THE OWNER, AFTER EVERY SCHEMA FILE. The refusal is trap 1 made loud: a table
    named in `required` and absent from `pg_tables` stops the grants, because granting
    now and creating later leaves that table ungranted with `relation "sessions" does not
    exist` as the only symptom.

    THE MIGRATION LEDGER IS DELIBERATELY NOT GRANTED. `applied_migration` describes the
    database rather than anything the application owns, and a role that can rewrite the
    version history can make a database claim a shape it does not have -- which is the
    one thing the checksum guard exists to catch.

    Returns what it did rather than None: the granted list is the answer to "which tables
    does this role reach", and an operator comparing it against `pg_tables` is how the
    next missing grant gets found before a run does.
    """
    require_matching_driver(connection, dialect)
    escapes = escapes_for(connection, role, dialect)
    if escapes.superuser or escapes.bypassrls:
        raise ValueError(
            f"refusing to provision {role!r} as the application role: {escapes.why_not()} "
            f"Grants would succeed and every policy in this database would still be "
            f"decoration. Use a role created with {render_create_role(role)}"
        )

    present = tables_present(connection, dialect)
    expected = tuple(t.name for t in schema.TABLES) + tuple(required)
    missing = tuple(t for t in expected if t not in present)
    if missing:
        raise LookupError(
            f"these tables do not exist yet: {', '.join(missing)}. Run every schema file "
            f"BEFORE the grants -- a grant covers only what exists when it runs, and the "
            f"symptom of granting early is `relation \"{missing[0]}\" does not exist`, "
            f"which reads as a missing migration rather than a missing grant."
        )

    granted = tuple(t for t in present if t != LEDGER_TABLE)
    run_script(connection, render_grants(role, granted), dialect)
    connection.commit()

    can_create = query(
        connection,
        "SELECT has_schema_privilege(?, 'public', 'CREATE') AS c",
        (role,),
        dialect,
    )[0]["c"]
    return {
        "role": role,
        "granted": granted,
        "not_granted": (LEDGER_TABLE,),
        "escapes": escapes_for(connection, role, dialect),
        "can_create_in_public": bool(can_create),
        "queue_note": QUEUE_NOTE,
    }


def render_sequence(role: str = APP_ROLE, required: Iterable[str] = ()) -> str:
    """The whole operator sequence as text, in the order it must run. See the module head."""
    names = tuple(t.name for t in schema.TABLES) + tuple(required)
    return "\n".join([
        "-- 1. as the OWNER (the role that will run the migrations):",
        ("--    python -c 'from agentorg.db import connect, migrate, POSTGRES; "
         "c=connect(OWNER_DSN); migrate(c, POSTGRES); c.commit()'"),
        "--    then every other schema file: web/lib/schema.sql, and the queue's own DDL",
        "-- 2. still as the owner, create the application role by hand:",
        f"      {render_create_role(role)}",
        "-- 3. still as the owner, AFTER every table exists:",
        render_grants(role, names).rstrip(),
        "-- 4. point the application's DSN at the role from step 2, never at the owner.",
        f"--    Verify with escapes_for(connection, {role!r}).binds  -> must be True",
    ]) + "\n"


__all__ = [
    "APP_ROLE",
    "QUEUE_NOTE",
    "QUEUE_TABLES",
    "WEB_TABLES",
    "Escapes",
    "escapes_for",
    "provision",
    "render_create_role",
    "render_grants",
    "render_sequence",
    "tables_present",
]
