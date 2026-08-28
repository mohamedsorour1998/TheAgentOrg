"""The tenancy schema, the engine, and the migrations. Lane B, tasks B1/B2/B8.

OWNER: Lane B. The leak suite is tests/test_tenancy_leak.py; this file covers the
SHAPE of the schema and the mechanisms the leak suite depends on.

WHY THE DIALECT ASSERTIONS ARE NOT DECORATION. SQLite is the tested path and Postgres is
the deployed one, so anything true of only one of them is a defect nobody local can see.
Every structural assertion below runs over BOTH renderings, and the two tests that pin
the `IS NOT` operator and `FORCE ROW LEVEL SECURITY` exist because both are silent
fail-open bugs: the first admits every write when no tenant is bound, the second exempts
the table owner -- which is who the application connects as -- from the table's own
policies.

A NOTE ON WHAT THESE TESTS DO NOT CLAIM. Nothing here connects to Postgres. The Postgres
assertions are over emitted DDL, which proves the policies are WRITTEN, not that they are
ENFORCED -- exactly the distinction CLAUDE.md draws between a green terraform apply and
`simulate-principal-policy`. The ADR states it in those terms and so does this docstring,
because a reader who takes these for execution has been misled by a passing test.
"""

import sqlite3

import pytest

from agentorg import db
from agentorg.db import engine, migrations, schema

# GUARD AGAINST A VACUOUS FILE. Every test below iterates one of these, so an empty
# collection would make this whole file pass while asserting nothing -- the operational
# form of "a test whose matcher can match nothing must say so", which CLAUDE.md records
# as having cost this repository nineteen-plus assertions that pinned nothing.
assert schema.TABLES, "schema.TABLES is empty; every test in this file would pin nothing"
assert schema.SCOPED_TABLES, "no scoped tables; the isolation tests would pin nothing"
assert schema.DIALECTS, "no dialects; the rendering tests would pin nothing"


@pytest.fixture()
def database():
    """A migrated in-memory database. No file, no server, no network."""
    connection = db.connect()
    migrations.migrate(connection)
    return connection


# ──────────────────────────────────────────────────────────────────────────────
# B1 -- the schema, and the seven tables the brief names
# ──────────────────────────────────────────────────────────────────────────────

def test_the_seven_tables_the_brief_names_all_exist():
    expected = {
        "organisation", "app_user", "membership",
        "repository", "run", "secret", "budget",
    }
    assert expected <= set(schema.TABLES_BY_NAME), (
        f"missing: {expected - set(schema.TABLES_BY_NAME)}"
    )


def test_every_table_renders_in_every_dialect():
    """The property that makes one schema definition safer than two .sql files.

    A table cannot exist in SQLite and not in Postgres, because both come off the same
    `Table`. This test is not a restatement of that -- it fails if a `Column` gains a
    type for one dialect only, which is the realistic way the guarantee breaks.
    """
    for dialect in schema.DIALECTS:
        rendered = schema.render_schema(dialect)
        for table in schema.TABLES:
            assert f'CREATE TABLE IF NOT EXISTS "{table.name}"' in rendered, (
                f"{table.name} is missing from the {dialect} rendering"
            )


def test_an_unknown_dialect_raises_rather_than_rendering_nothing():
    """A typo'd dialect must not render an empty script that applies cleanly."""
    with pytest.raises(ValueError, match="unknown dialect"):
        schema.render_schema("postgresql")  # the plausible near-miss, not gibberish


def test_every_table_either_declares_a_tenant_column_or_says_why_not():
    """The refusal that stops a table becoming unscoped by omission.

    This is the whole reason `tenant_column` is required rather than defaulted. A table
    added without thinking about tenancy is the shape the one fatal defect arrives in --
    not as a decision anybody made, as a question nobody was forced to answer.
    """
    for table in schema.TABLES:
        if table.tenant_column is None:
            assert table.unscoped_reason.strip(), (
                f"{table.name} is unscoped with no reason given"
            )
        else:
            assert table.tenant_column in [c.name for c in table.columns]


def test_a_table_naming_a_tenant_column_that_does_not_exist_is_refused():
    """A scope column that does not exist renders a guard comparing against nothing."""
    with pytest.raises(ValueError, match="is not one of its columns"):
        schema.Table(
            name="bad",
            columns=(schema.Column("id", "TEXT", "TEXT", primary_key=True),),
            tenant_column="tenant_id",
        )


def test_a_table_with_no_tenant_column_and_no_reason_is_refused():
    with pytest.raises(ValueError, match="must be written down"):
        schema.Table(
            name="bad",
            columns=(schema.Column("id", "TEXT", "TEXT", primary_key=True),),
            tenant_column=None,
        )


def test_a_table_cannot_be_scoped_and_also_explain_why_it_is_not():
    with pytest.raises(ValueError, match="One or the other"):
        schema.Table(
            name="bad",
            columns=(schema.Column("tenant_id", "TEXT", "TEXT", primary_key=True),),
            tenant_column="tenant_id",
            unscoped_reason="contradictory",
        )


def test_app_user_is_the_only_unscoped_table():
    """If a second table goes unscoped, that is a decision someone must defend here.

    Written as an equality rather than a membership check on purpose: `app_user in
    UNSCOPED_TABLES` would keep passing as tables were added to that set, which is the
    direction that matters.
    """
    assert [t.name for t in schema.UNSCOPED_TABLES] == ["app_user"]


def test_money_is_stored_as_an_integer_in_both_dialects():
    """Cents, never a float. A float ceiling against a float spend is a rounding bug."""
    budget = schema.TABLES_BY_NAME["budget"]
    money = {c.name: c for c in budget.columns if c.name.endswith("_cents")}
    assert money, "no *_cents columns on budget; this test would pin nothing"
    for name, column in money.items():
        assert column.sqlite_type == "INTEGER", f"{name} is not INTEGER on sqlite"
        assert column.postgres_type == "BIGINT", f"{name} is not BIGINT on postgres"


def test_the_budget_ceiling_is_not_nullable_so_absent_cannot_read_as_unlimited():
    """`unlimited` is its own column, set explicitly, for the reason config.py gives.

    A NULL ceiling meaning unlimited makes "nobody configured this" and "may spend
    without bound" the same value -- the same trap as reading a blank
    `ci_status_measured` as `unknown`.
    """
    budget = schema.TABLES_BY_NAME["budget"]
    columns = {c.name: c for c in budget.columns}
    assert "unlimited" in columns, "no explicit unlimited column"
    assert not columns["ceiling_cents"].null, "a NULL ceiling would read as unlimited"


def test_repository_and_secret_names_are_unique_per_tenant_and_not_globally():
    """Two customers may both connect `acme/auth-service`.

    A global UNIQUE would fail one customer's onboarding with a message about a
    repository they cannot see -- which is itself a cross-tenant information leak, in the
    error text rather than in a row.
    """
    for table_name, column in (("repository", "full_name"), ("secret", "name")):
        table = schema.TABLES_BY_NAME[table_name]
        combinations = [set(c) for c in table.unique_together]
        assert {"tenant_id", column} in combinations, (
            f"{table_name}.{column} is not unique per tenant"
        )
        assert not any(
            c.unique for c in table.columns if c.name == column
        ), f"{table_name}.{column} carries a GLOBAL unique constraint"


def test_the_secret_table_has_no_plaintext_column():
    """There must be no column a careless write could put a token in."""
    secret = schema.TABLES_BY_NAME["secret"]
    names = {c.name for c in secret.columns}
    assert {"nonce", "ciphertext", "mac", "cipher"} <= names
    for forbidden in ("value", "plaintext", "secret", "token", "password"):
        assert forbidden not in names, (
            f"secret.{forbidden} is a plaintext-shaped column"
        )


# ──────────────────────────────────────────────────────────────────────────────
# B8 -- enforcement at the database layer, not only in application code
# ──────────────────────────────────────────────────────────────────────────────

def test_every_scoped_table_gets_three_sqlite_triggers():
    """Insert, update AND delete. A missing delete trigger is a silent destroy path."""
    ddl = schema.render_schema(schema.SQLITE)
    for table in schema.SCOPED_TABLES:
        for verb in ("insert", "update", "delete"):
            assert f'"{table.name}_no_cross_tenant_{verb}"' in ddl, (
                f"{table.name} has no {verb} guard"
            )


def test_the_sqlite_guards_use_IS_NOT_and_never_bare_inequality():
    """THE MEASURED FAIL-OPEN CASE. `'t2' != NULL` is NULL, which does not fire a WHEN.

    So a `!=` guard is absent exactly when no tenant is bound -- the case it most needs
    to catch -- while still refusing an ordinary mismatch, so no hand test reveals it.
    Asserted over the rendered DDL because that is what SQLite reads.
    """
    ddl = schema.render_schema(schema.SQLITE)
    guard_lines = [
        line for line in ddl.splitlines()
        if line.startswith("WHEN ") and schema.SQLITE_TENANT_FUNCTION in line
    ]
    assert guard_lines, "no WHEN clauses found; this test would pin nothing"
    for line in guard_lines:
        assert " IS NOT " in line, f"guard does not use IS NOT: {line}"
        assert "!=" not in line, f"guard uses the fail-open operator: {line}"


def test_the_update_guard_tests_the_old_row_and_the_new_row():
    """OLD alone lets a tenant push its own row into somebody else's scope.

    A write that gives data away rather than taking it, and still a breach of the same
    invariant.
    """
    ddl = schema.render_schema(schema.SQLITE)
    for table in schema.SCOPED_TABLES:
        marker = f'"{table.name}_no_cross_tenant_update"'
        clause = ddl.split(marker, 1)[1].split("BEGIN", 1)[0]
        assert "OLD." in clause and "NEW." in clause, (
            f"{table.name}'s update guard does not test both OLD and NEW: {clause}"
        )


def test_postgres_forces_row_level_security_and_does_not_merely_enable_it():
    """FORCE is not decoration: without it the table OWNER is exempt from its policies.

    The application connects as the owner by default, so ENABLE alone is a policy the
    connection ignores -- protection that reads as present and is not.
    """
    ddl = schema.render_schema(schema.POSTGRES)
    for table in schema.SCOPED_TABLES:
        assert f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY;' in ddl
        assert f'ALTER TABLE "{table.name}" FORCE ROW LEVEL SECURITY;' in ddl, (
            f"{table.name} enables RLS without forcing it; the owner is exempt"
        )


def test_every_postgres_policy_constrains_reads_and_writes():
    """USING scopes the SELECT, WITH CHECK scopes the INSERT and UPDATE.

    USING alone would let a tenant WRITE a row it could not then read -- data planted in
    another tenant's scope, which is the breach running outward.
    """
    ddl = schema.render_schema(schema.POSTGRES)
    for table in schema.SCOPED_TABLES:
        marker = f'CREATE POLICY "{table.name}_tenant_isolation"'
        assert marker in ddl, f"{table.name} has no policy"
        policy = ddl.split(marker, 1)[1].split(";", 1)[0]
        assert "USING" in policy, f"{table.name}'s policy does not scope reads"
        assert "WITH CHECK" in policy, f"{table.name}'s policy does not scope writes"
        assert schema.POSTGRES_TENANT_SETTING in policy


def test_unscoped_tables_get_no_isolation_ddl_in_either_dialect():
    """Not an omission -- app_user is unreachable from a scope, which the ADR explains.

    A policy on a global identity table would have to compare against a column that does
    not exist, and the plausible fix (inventing one) makes "the same person in two
    organisations" unrepresentable.
    """
    for table in schema.UNSCOPED_TABLES:
        for dialect in schema.DIALECTS:
            assert table.render_isolation(dialect) == ""


# ──────────────────────────────────────────────────────────────────────────────
# The engine -- the binding the guards compare against
# ──────────────────────────────────────────────────────────────────────────────

def test_nothing_is_bound_until_a_block_binds_it(database):
    """The process starts fail-closed, which is the state a guard needs to be useful."""
    with engine.acting_as_nobody():
        assert engine.current_tenant() is None
        with pytest.raises(engine.TenantNotBound):
            engine.require_tenant()


def test_the_binding_is_restored_when_the_block_exits(database):
    with engine.acting_as("outer"):
        with engine.acting_as("inner"):
            assert engine.current_tenant() == "inner"
        assert engine.current_tenant() == "outer", (
            "the inner block left the outer scope changed"
        )


def test_an_exception_inside_the_block_does_not_leave_a_tenant_bound():
    """Otherwise the next caller on this thread inherits a scope nobody granted it."""
    with engine.acting_as_nobody():
        with pytest.raises(RuntimeError, match="deliberate"), engine.acting_as("t1"):
            raise RuntimeError("deliberate")
        assert engine.current_tenant() is None


def test_a_blank_tenant_id_cannot_be_bound():
    """"" is the single-tenant MARKER, never a scope: a blank scope matches a blank
    column, which is a row nobody owns."""
    for blank in ("", "   ", "\t"):
        with pytest.raises(ValueError, match="may not be blank"), engine.acting_as(
            blank
        ):
            pass


def test_the_tenant_function_is_registered_on_every_connection(database):
    """Without it the guards reference a function SQLite does not have.

    Read through the DATABASE rather than by inspecting the module, because what matters
    is what a statement sees.
    """
    with engine.acting_as("t-probe"):
        value = database.execute(
            f"SELECT {schema.SQLITE_TENANT_FUNCTION}()"
        ).fetchone()[0]
    assert value == "t-probe"


def test_foreign_keys_are_enabled_so_references_are_more_than_decoration(database):
    """SQLite's PRAGMA foreign_keys reads 0 on a fresh connection -- measured.

    So every REFERENCES clause in the schema is inert unless the connection enables it,
    which is why connections come from `db.connect` rather than `sqlite3.connect`.
    """
    assert database.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    with engine.acting_as("t1"):
        database.execute(
            'INSERT INTO "organisation" VALUES (?,?,?)', ("t1", "T1", "now")
        )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            database.execute(
                'INSERT INTO "membership" VALUES (?,?,?,?,?)',
                ("m1", "t1", "no-such-user", "admin", "now"),
            )


# ──────────────────────────────────────────────────────────────────────────────
# B2 -- migrations, forward-only, runnable against an empty database
# ──────────────────────────────────────────────────────────────────────────────

def test_migrate_runs_against_an_empty_database_and_reports_what_it_applied():
    connection = db.connect()
    applied = migrations.migrate(connection)
    assert applied == [m.version for m in migrations.MIGRATIONS]


def test_migrate_is_idempotent_and_says_it_did_nothing(database):
    """The list return is what makes "nothing to do" distinguishable from "did work".

    A count would make both `0` and both events the same integer.
    """
    assert migrations.migrate(database) == []


def test_every_table_is_queryable_after_migrating(database):
    """The migration produced the schema, not merely a ledger row saying it did."""
    for table in schema.TABLES:
        database.execute(f'SELECT * FROM "{table.name}" LIMIT 1').fetchall()


def test_migrations_have_unique_ascending_versions():
    versions = [m.version for m in migrations.MIGRATIONS]
    assert versions == sorted(set(versions)), f"duplicate or unordered: {versions}"


def test_there_are_no_down_migrations():
    """A down migration for customer data is a delete script kept beside the creator.

    Asserted over the dataclass fields rather than the file text: a comment saying "no
    down migrations" would satisfy a grep while a `down` attribute existed.
    """
    for migration in migrations.MIGRATIONS:
        for attribute in ("down", "downgrade", "revert", "rollback"):
            assert not hasattr(migration, attribute), (
                f"migration {migration.version} exposes {attribute!r}"
            )


def test_an_edited_applied_migration_is_refused_rather_than_skipped(database):
    """Two databases with the same version and different shapes is the failure.

    The version column cannot see it; the checksum can. Simulated by corrupting the
    LEDGER, which is equivalent to and cleaner than mutating the module's tuple.
    """
    database.execute(
        f'UPDATE "{migrations.LEDGER_TABLE}" SET "checksum" = ? WHERE "version" = ?',
        ("0" * 64, migrations.MIGRATIONS[0].version),
    )
    with pytest.raises(RuntimeError, match="different definition"):
        migrations.migrate(database)


def test_the_first_migration_is_generated_from_the_schema_not_transcribed():
    """A transcribed copy is a second declaration of the schema, free to drift silently.

    Compared by VALUE against the renderer rather than by grepping the module's source: a
    comment claiming the SQL is generated would satisfy a substring check while a
    hand-written string sat beside it. CLAUDE.md records that exact gap being found twice
    in one lane.
    """
    first = migrations.MIGRATIONS[0]
    assert first.sqlite == schema.render_schema(schema.SQLITE)
    assert first.postgres == schema.render_schema(schema.POSTGRES)


def test_a_migration_checksum_differs_between_dialects():
    """One checksum over both renderings would change when either changed.

    And it could not say which -- so the refusal above would name the wrong database.
    """
    first = migrations.MIGRATIONS[0]
    assert first.checksum(schema.SQLITE) != first.checksum(schema.POSTGRES)


def test_the_ledger_is_not_in_the_set_of_tenant_tables():
    """It describes the database, not anything a tenant owns.

    In `schema.TABLES` it would be a permanent exception in every isolation test the leak
    suite derives from that set -- and a permanent exception is where the next real
    exception hides.
    """
    assert migrations.LEDGER_TABLE not in schema.TABLES_BY_NAME


# ── THE POSTGRES DIALECT, EXECUTED — added by the integrator on 2026-08-28 ────
#
# Lane B wrote the Postgres DDL and could only assert it structurally, because no Postgres
# existed on any machine that ran this suite. It was executed for the first time against a
# real PostgreSQL 16.15 and FAILED ON ITS FIRST STATEMENT SET:
#
#     DatatypeMismatch: column "unlimited" is of type boolean but default expression is
#     of type integer
#
# `Column("unlimited", "INTEGER", "BOOLEAN", default="0")` is correct for sqlite, which has
# no boolean type and stores 0/1, and rejected by Postgres, which will not coerce. The
# class carried per-dialect TYPES and one shared DEFAULT, so it could not express a value
# that differs between them -- and every structural test passed for as long as nobody ran
# the Postgres half.
#
# These tests do not need a database. They read the rendered DDL, which is what the earlier
# tests could not do only because nobody thought to compare a default against its own
# column type.

def test_no_boolean_column_carries_a_numeric_default_in_the_postgres_ddl():
    """A BOOLEAN column with a `0` default is refused by Postgres at CREATE TABLE.

    Asserted over the rendered DDL rather than over the Column objects, because the
    rendering is where the two halves meet: a column may legitimately hold `default="0"`
    for sqlite as long as `postgres_default` overrides it here.
    """
    import re

    ddl = schema.render_schema(schema.POSTGRES)
    offenders = [
        line.strip() for line in ddl.splitlines()
        if re.search(r"\bBOOLEAN\b", line) and re.search(r"DEFAULT\s+-?\d+\b", line)
    ]

    assert not offenders, (
        f"the Postgres DDL declares a BOOLEAN column with a numeric DEFAULT: {offenders}. "
        f"Postgres refuses this at CREATE TABLE with DatatypeMismatch -- it will not "
        f"coerce an integer literal into a boolean column. Use `postgres_default=\"FALSE\"`."
    )


def test_the_sqlite_ddl_still_uses_a_numeric_default_for_that_column():
    """The other half. Without this, the fix could be 'use TRUE/FALSE everywhere'.

    SQLite has no boolean type: it stores 0/1, and `DEFAULT FALSE` there is a bareword
    that sqlite accepts and then treats as a string. So the two dialects genuinely need
    different literals, which is the whole reason `postgres_default` exists rather than
    the default simply being changed.
    """
    import re

    ddl = schema.render_schema(schema.SQLITE)
    unlimited = [line for line in ddl.splitlines() if '"unlimited"' in line]

    assert unlimited, "no `unlimited` column in the sqlite DDL; this test would pin nothing"
    assert re.search(r"DEFAULT\s+0\b", unlimited[0]), (
        f"the sqlite DDL no longer defaults `unlimited` to 0: {unlimited[0].strip()!r}. "
        f"SQLite has no boolean type, so a TRUE/FALSE bareword is stored as text."
    )


def test_a_dialect_specific_default_only_affects_that_dialect():
    """`postgres_default` must not leak into the sqlite rendering, or the fix is a swap.

    Exercised on a synthetic column rather than on the schema, so it keeps testing the
    mechanism after `unlimited` is the only real user of it -- or stops being one.
    """
    column = schema.Column("flag", "INTEGER", "BOOLEAN", default="0",
                           postgres_default="FALSE")

    assert "DEFAULT 0" in column.render(schema.SQLITE)
    assert "DEFAULT FALSE" in column.render(schema.POSTGRES)
    assert "DEFAULT FALSE" not in column.render(schema.SQLITE), (
        "the Postgres default leaked into the sqlite rendering"
    )


def test_force_row_level_security_is_emitted_AND_the_superuser_caveat_is_recorded():
    """FORCE RLS fixes ONE of the three ways Postgres skips a policy. Measured.

    Postgres skips row-level security for a superuser, for any role with BYPASSRLS, and
    for the table's OWNER. `FORCE ROW LEVEL SECURITY` addresses only the third.

    MEASURED against a real PostgreSQL 16.15, same database and the same six policies,
    two different connections:

        as the superuser owning the tables   attacker saw BOTH tenants' runs, unscoped
                                             read returned 2 rows
        as a plain LOGIN role                attacker saw only its own, unscoped read
                                             returned 0 rows

    So the isolation guarantee is a property of the CONNECTION, not of this schema. A DSN
    pointing at a superuser turns every policy into decoration while `pg_policies` still
    lists all six -- a check present, enumerable, and enforcing nothing.

    This test cannot verify the connection (there is no database in the suite). It pins
    that the DDL does its half and that the caveat is written down where somebody
    configuring a DSN will find it.
    """
    import pathlib

    ddl = schema.render_schema(schema.POSTGRES)
    assert schema.SCOPED_TABLES, "SCOPED_TABLES is empty; this test would pin nothing"

    for table in schema.SCOPED_TABLES:
        assert f'ALTER TABLE "{table.name}" FORCE ROW LEVEL SECURITY;' in ddl, (
            f"{table.name} enables RLS without FORCE, so the table's owner -- which is "
            f"whatever role ran the migration -- bypasses every policy on it"
        )

    source = pathlib.Path(schema.__file__).read_text(encoding="utf-8")
    assert "superuser" in source.lower(), (
        "schema.py does not mention that a superuser bypasses RLS regardless of FORCE. "
        "That is the one deployment mistake which leaves all six policies listed in "
        "pg_policies and none of them enforcing anything."
    )
