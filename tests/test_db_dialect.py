"""The execute boundary — what it splits, what it refuses, and what it cannot do.

LANE R. `agentorg/db/_dialect.py` exists because `migrations.migrate` accepted
`dialect="postgres"` and then called `connection.executescript`, which psycopg has no
such method for. These tests need no database; `tests/test_db_postgres.py` is the half
that does.

TWO OF THESE ASSERT A FAILURE RATHER THAN A SUCCESS, deliberately. The `E'...'` limit is
stated as a test following `test_the_synonym_limit_is_real_and_this_test_records_it`, so
adding backslash handling turns it red rather than letting a docstring quietly stop being
true. And the `executescript` check is over the **AST**, because this module's own
docstring says the word eleven times -- a substring check would be satisfied by the
sentence explaining the defect, which is the failure CLAUDE.md records twice in one lane.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3

import pytest

from agentorg.db import _dialect, migrations, schema

# ──────────────────────────────────────────────────────────────────────────────
# THE SPLITTER
# ──────────────────────────────────────────────────────────────────────────────


def test_a_semicolon_inside_a_string_literal_does_not_end_the_statement():
    """The naive `script.split(";")` is wrong here, and this is the shape that proves it."""
    script = "INSERT INTO t VALUES ('a;b'); SELECT 1;"

    assert script.split(";") != ["INSERT INTO t VALUES ('a;b')", " SELECT 1", ""][:2], (
        "the naive split does NOT break this input; this test would pin nothing"
    )
    assert _dialect.split_statements(script) == [
        "INSERT INTO t VALUES ('a;b')",
        "SELECT 1",
    ]


def test_a_doubled_quote_is_an_escape_and_not_a_close_then_open():
    """`'it''s'` read the naive way leaves the scanner believing it is outside a string.

    Every semicolon after that point is then misread, so the failure is not local to the
    literal -- it corrupts the rest of the script.
    """
    script = "SELECT 'it''s; still one string'; SELECT 2;"

    assert _dialect.split_statements(script) == [
        "SELECT 'it''s; still one string'",
        "SELECT 2",
    ]


def test_a_dollar_quoted_body_is_one_statement():
    """`provision.py`'s SECURITY DEFINER lookup is written as a `$$ ... $$` body.

    Its body contains a `;` and a naive split cuts the function in half, producing a
    syntax error that names the second fragment rather than the splitter.
    """
    script = (
        "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $$ SELECT 1; SELECT 2; $$;"
        " SELECT 3;"
    )
    statements = _dialect.split_statements(script)

    assert len(script.split(";")) > 3, "the naive split does not cut this body; vacuous"
    assert statements == [
        "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $$ SELECT 1; SELECT 2; $$",
        "SELECT 3",
    ]


def test_a_tagged_dollar_quote_is_honoured_and_a_bare_placeholder_is_not():
    """`$body$` opens a quote; `$1` is a parameter and opens nothing.

    Treating `$1` as an opener swallows the rest of the script into one statement, which
    reads as "the splitter matched nothing" -- a selection that silently emptied.
    """
    tagged = "CREATE FUNCTION g() RETURNS int AS $body$ SELECT 1; $body$; SELECT 4;"
    placeholder = "SELECT * FROM t WHERE id = $1; SELECT 5;"

    assert len(_dialect.split_statements(tagged)) == 2
    assert _dialect.split_statements(placeholder) == [
        "SELECT * FROM t WHERE id = $1",
        "SELECT 5",
    ]


def test_comment_only_fragments_are_dropped_rather_than_executed():
    """psycopg raises on an empty query, so a trailing `;` would fail the last statement."""
    script = "SELECT 1;\n-- a trailing comment\n/* and a block one */\n"

    assert _dialect.split_statements(script) == ["SELECT 1"]


def test_a_semicolon_inside_a_comment_does_not_end_the_statement():
    script = "SELECT 1 -- ; not a terminator\n, 2;"

    assert _dialect.split_statements(script) == ["SELECT 1 -- ; not a terminator\n, 2"]


def test_the_rendered_postgres_schema_splits_into_whole_ddl_statements():
    """Structural, not a count: a count would be a second declaration of the schema.

    Every table gets exactly one CREATE, and every scoped table exactly one ENABLE, one
    FORCE and one POLICY. A splitter that cut a statement in half would leave a fragment
    that matches none of these and the totals would disagree.
    """
    statements = _dialect.split_statements(schema.render_schema(schema.POSTGRES))

    assert statements, "the postgres schema rendered nothing; this test would pin nothing"
    assert schema.SCOPED_TABLES, "SCOPED_TABLES is empty; this test would pin nothing"

    for table in schema.TABLES:
        creates = [s for s in statements if s.startswith(f'CREATE TABLE IF NOT EXISTS "{table.name}"')]
        assert len(creates) == 1, f"{table.name}: {len(creates)} CREATE statements"

    for table in schema.SCOPED_TABLES:
        for verb in ("ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY"):
            matching = [s for s in statements if s == f'ALTER TABLE "{table.name}" {verb}']
            assert len(matching) == 1, f"{table.name}: {verb} appears {len(matching)} times"
        policies = [s for s in statements if s.startswith(f'CREATE POLICY "{table.name}_tenant_isolation"')]
        assert len(policies) == 1, f"{table.name}: {len(policies)} policies"

    assert all(";" not in s for s in statements if "'" not in s and "$" not in s), (
        "a split statement still carries a terminator"
    )


def test_the_E_string_backslash_limit_is_real_and_this_test_records_it():
    """THIS ASSERTS THE FAILURE. `E'\\''` does not close where the scanner thinks it does.

    Postgres ships `standard_conforming_strings = on`, so a backslash in an ordinary
    literal is a backslash and the scanner is correct for it. `E''` is the one shape where
    it is not. No DDL in this repository uses one, and the honest way to say so is a test
    that goes RED the day somebody adds backslash handling -- not a sentence in a
    docstring that stops being true with nothing to notice.
    """
    script = r"SELECT E'\'; still inside'; SELECT 9;"

    assert _dialect.split_statements(script) != [
        r"SELECT E'\'; still inside'",
        "SELECT 9",
    ], (
        "the splitter now handles backslash escapes in E'' strings. Good -- delete this "
        "test and the paragraph in _dialect.py's docstring that states the limit."
    )


# ──────────────────────────────────────────────────────────────────────────────
# THE REFUSALS
# ──────────────────────────────────────────────────────────────────────────────


class _NotSqlite:
    """A stand-in for a psycopg connection. It is not one, and that is the point.

    `driver_dialect` classifies by `isinstance` against `sqlite3.Connection`, so anything
    else reads as Postgres -- which is exactly what must happen when a real psycopg
    connection arrives on a host where this suite may not import psycopg at all.
    """


def test_a_sqlite_dialect_over_a_non_sqlite_connection_is_refused():
    """The original defect, as a message. It used to be an AttributeError from psycopg."""
    with pytest.raises(TypeError, match="sqlite.* DDL over a .*postgres.* connection"):
        _dialect.require_matching_driver(_NotSqlite(), schema.SQLITE)


def test_a_postgres_dialect_over_a_sqlite_connection_is_refused():
    """The other direction, which used to be a syntax error from inside the DDL."""
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(TypeError, match="postgres.* DDL over a .*sqlite.* connection"):
            _dialect.require_matching_driver(connection, schema.POSTGRES)
    finally:
        connection.close()


def test_an_unknown_dialect_is_refused_before_the_connection_is_looked_at():
    """`config.STATE_BACKEND`'s rule: a typo raises rather than falling back."""
    with pytest.raises(ValueError, match="unknown dialect"):
        _dialect.require_matching_driver(_NotSqlite(), "postgre")


def test_the_placeholder_rewrite_applies_to_postgres_and_only_to_postgres():
    statement = 'SELECT "x" FROM "t" WHERE "a" = ? AND "b" = ?'

    assert _dialect.sql(statement, schema.SQLITE) == statement
    assert _dialect.sql(statement, schema.POSTGRES).count("%s") == 2
    assert "?" not in _dialect.sql(statement, schema.POSTGRES)


def test_scalar_reads_every_row_shape_the_two_drivers_produce():
    """Three shapes, and neither `row[0]` nor `row[name]` alone is correct for all three."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        sqlite_row = connection.execute("SELECT 7 AS version").fetchone()
        assert _dialect.scalar(sqlite_row) == 7
    finally:
        connection.close()

    assert _dialect.scalar({"version": 7}) == 7, "psycopg's dict_row shape"
    assert _dialect.scalar((7,)) == 7, "a psycopg connection with no row factory"


def test_run_script_reports_how_many_statements_it_executed():
    """A script that executed nothing and one that executed nine are different events."""
    connection = sqlite3.connect(":memory:")
    try:
        ran = _dialect.run_script(
            connection, "CREATE TABLE a (x INTEGER); CREATE TABLE b (y INTEGER);",
            schema.SQLITE,
        )
        assert ran == 2
        names = {r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"a", "b"} <= names
    finally:
        connection.close()


# ──────────────────────────────────────────────────────────────────────────────
# THE CALL SITES — over the AST, because the prose names the method repeatedly
# ──────────────────────────────────────────────────────────────────────────────


def _executescript_calls(module) -> list[int]:
    """Line numbers of every `<something>.executescript(...)` CALL in `module`.

    Over the AST rather than over the text: `migrations.py` and `_dialect.py` between them
    say `executescript` more than a dozen times in prose explaining why psycopg has none,
    so `"executescript" in source` is satisfied by the explanation. CLAUDE.md records that
    exact gap being found twice in one lane.
    """
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "executescript"
    ]


def test_the_only_executescript_call_in_the_db_package_is_behind_the_boundary():
    """Three call sites used to sit in `migrations.py`, and the parameter offered a
    dialect that could reach none of them."""
    assert _executescript_calls(_dialect), (
        "no executescript call found in _dialect.py -- the AST matcher is broken, so the "
        "assertion below would pass against a module that calls it everywhere"
    )
    assert _executescript_calls(migrations) == [], (
        "migrations.py calls executescript directly again. psycopg has no such method, "
        "so that line is unreachable on the dialect the parameter offers. Route it "
        "through _dialect.run_script."
    )
