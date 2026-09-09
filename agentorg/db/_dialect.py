"""The execute boundary: one place that knows which driver is underneath.

OWNER: Lane R. Written because `migrations.migrate` accepted `dialect="postgres"` and
then called `connection.executescript`, which psycopg has no such method for. Measured
on this host, psycopg 3.3.4:

    sqlite3 Connection has executescript: True
    psycopg Connection has executescript: False

So the forward-only ledger, its checksum guard and its idempotency had never run against
PostgreSQL at all -- every Postgres verification in this repository applied
`schema.render_schema(POSTGRES)` through `psql` and bypassed the runner. A parameter
offering a backend the plumbing beneath it cannot reach.

WHY A MODULE AND NOT A CONDITIONAL AT EACH CALL SITE. `queue/_sql.py:_sql` reached the
same conclusion for the same reason and its wording is worth copying: every method would
otherwise carry the same conditional, "and one of them would eventually be written with
the wrong placeholder and fail only against the dialect nobody was testing". There are
three `executescript` sites and two parameterised statements in `migrations.py` alone,
and `provision.py` adds more.

THE DIALECT IS DERIVED FROM THE CONNECTION, AND A MISMATCH IS REFUSED. `driver_dialect`
reads the object; `require_matching_driver` refuses when the caller's `dialect=` argument
disagrees with it. That refusal is the whole defect turned into an error message: a caller
asking for Postgres DDL over a sqlite3 connection used to get a syntax error from inside
the DDL, and a caller asking for sqlite DDL over psycopg used to get `AttributeError:
'Connection' object has no attribute 'executescript'` -- neither of which names the
mistake, which is that the text and the driver disagree.

WHAT THE SPLITTER DOES NOT HANDLE, stated rather than discovered later: backslash escapes
inside `E'...'` strings. Postgres ships `standard_conforming_strings = on`, so a backslash
in an ordinary literal is a backslash and the state machine below is correct for it; an
`E''` literal is the one shape where `\\'` does not close the string.
`tests/test_db_dialect.py` asserts that limit as a FAILING case rather than leaving the
docstring to quietly stop being true. No DDL in this repository uses one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from . import schema

# A connection from either driver. Deliberately `Any` with this comment rather than a
# Protocol: the two drivers do not share `executescript`, so a Protocol declaring the
# methods this module calls would have to omit the very method whose absence is the bug --
# and a type that cannot express the failing case is this repository's named pattern.
Connection = Any

_IDENTIFIER_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
_IDENTIFIER_REST = _IDENTIFIER_START + "0123456789"


def driver_dialect(connection: Connection) -> str:
    """Which dialect this CONNECTION speaks, read off the object.

    `isinstance` against `sqlite3.Connection` rather than a capability check
    (`hasattr(connection, "executescript")`), because a test double answers every
    `hasattr` and would be classified as sqlite while behaving as neither. The negative
    branch is "not sqlite3", not "psycopg": this module never imports psycopg -- see
    `queue/_sql.py`'s note on `test_requirements_covers_every_third_party_import_in_the_package`,
    which would make a driver import here a pinned dependency of all five arm64 agent
    images.
    """
    return schema.SQLITE if isinstance(connection, sqlite3.Connection) else schema.POSTGRES


def require_matching_driver(connection: Connection, dialect: str) -> None:
    """Refuse a `dialect=` argument the connection cannot honour.

    THIS IS THE ORIGINAL DEFECT AS AN ERROR MESSAGE. `migrate(psycopg_connection)` took
    the default `dialect="sqlite"`, rendered SQLite DDL -- triggers calling a
    `current_tenant()` function that exists only in sqlite -- and died inside
    `executescript` with an `AttributeError` naming neither half.
    """
    if dialect not in schema.DIALECTS:
        raise ValueError(
            f"unknown dialect {dialect!r}; expected one of {', '.join(schema.DIALECTS)}"
        )
    actual = driver_dialect(connection)
    if actual != dialect:
        raise TypeError(
            f"asked for {dialect!r} DDL over a {actual!r} connection "
            f"({type(connection).__module__}.{type(connection).__name__}). The dialect "
            f"selects the SQL TEXT and the connection selects the DRIVER, and they must "
            f"agree: SQLite DDL registers triggers against a `current_tenant()` function "
            f"Postgres does not have, and Postgres DDL creates policies SQLite cannot "
            f"parse. Pass dialect={actual!r}, or open the other connection."
        )


def sql(statement: str, dialect: str) -> str:
    """`statement` written with `?` placeholders, rewritten for the dialect.

    Statements are authored with sqlite's `?` because that is the dialect the suite runs,
    and rewritten here. Same trade `queue/_sql.py` made, and the same reason it is one
    function: psycopg's `%s` in a hand-written string is the placeholder somebody
    eventually gets wrong in the one direction no test exercises.
    """
    return statement if dialect == schema.SQLITE else statement.replace("?", "%s")


def split_statements(script: str) -> list[str]:
    """A multi-statement script as individual statements, semicolons inside quoted text
    left alone.

    NEEDED ONLY BECAUSE PSYCOPG HAS NO `executescript`. A naive `script.split(";")` is
    what a first attempt reaches for and it is wrong twice over: it breaks a literal
    containing a semicolon, and it breaks a `$$ ... $$` function body, which is exactly
    what `provision.py`'s SECURITY DEFINER lookup is written as.

    Comment-only and blank fragments are DROPPED rather than passed on: psycopg raises on
    an empty query, so a trailing `;` would turn a correct script into a failure at the
    last statement.
    """
    statements: list[str] = []
    current: list[str] = []
    index = 0
    length = len(script)
    while index < length:
        char = script[index]
        pair = script[index:index + 2]

        if pair == "--":
            end = script.find("\n", index)
            end = length if end == -1 else end
            current.append(script[index:end])
            index = end
            continue

        if pair == "/*":
            end = script.find("*/", index + 2)
            end = length if end == -1 else end + 2
            current.append(script[index:end])
            index = end
            continue

        if char == "'":
            end = _closing_quote(script, index, "'")
            current.append(script[index:end])
            index = end
            continue

        if char == '"':
            end = _closing_quote(script, index, '"')
            current.append(script[index:end])
            index = end
            continue

        if char == "$":
            tag = _dollar_tag(script, index)
            if tag is not None:
                end = script.find(tag, index + len(tag))
                end = length if end == -1 else end + len(tag)
                current.append(script[index:end])
                index = end
                continue

        if char == ";":
            statements.append("".join(current))
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    statements.append("".join(current))
    return [s.strip() for s in statements if _carries_sql(s)]


def _closing_quote(script: str, start: int, quote: str) -> int:
    """The index just past the quote that closes the one at `start`.

    A DOUBLED QUOTE IS AN ESCAPE, not a close followed by an open, and the difference
    matters here: `'it''s'` scanned the naive way leaves the scanner believing it is
    OUTSIDE a string for the rest of the script, so every later semicolon is misread.
    """
    index = start + 1
    while index < len(script):
        if script[index] == quote:
            if script[index + 1:index + 2] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return len(script)


def _dollar_tag(script: str, start: int) -> str | None:
    """`$$` or `$tag$` beginning at `start`, or None if this `$` opens nothing.

    A bare `$1` is a placeholder and a bare `$` may be arithmetic; neither opens a quoted
    body, so returning None for them is the difference between splitting a script and
    swallowing the rest of it.
    """
    index = start + 1
    if index < len(script) and script[index] == "$":
        return "$$"
    if index < len(script) and script[index] in _IDENTIFIER_START:
        index += 1
        while index < len(script) and script[index] in _IDENTIFIER_REST:
            index += 1
        if index < len(script) and script[index] == "$":
            return script[start:index + 1]
    return None


def _carries_sql(fragment: str) -> bool:
    """Whether `fragment` holds anything a driver could execute.

    Whitespace and comments only is not a statement. Checked by stripping both forms of
    comment rather than by testing `fragment.strip()`, because a script ending in a
    trailing comment after its last `;` produces exactly that fragment.
    """
    text = fragment
    while True:
        start = text.find("--")
        if start == -1:
            break
        end = text.find("\n", start)
        text = text[:start] + ("" if end == -1 else text[end:])
    while True:
        start = text.find("/*")
        if start == -1:
            break
        end = text.find("*/", start + 2)
        text = text[:start] + ("" if end == -1 else text[end + 2:])
    return bool(text.strip())


def run_script(connection: Connection, script: str, dialect: str) -> int:
    """Execute a multi-statement DDL script. Returns how many statements ran.

    RETURNS A COUNT rather than None for `migrate`'s reason one layer up: a script that
    executed nothing and a script that executed nine statements are different events, and
    a caller that cannot tell them apart cannot notice a splitter that quietly matched
    nothing -- which is `pytest -k` misspelled, in a different place.

    SQLite keeps `executescript` because it HAS one and its trigger bodies carry
    semicolons inside `BEGIN ... END`, which no splitter should have to reason about.
    """
    require_matching_driver(connection, dialect)
    if dialect == schema.SQLITE:
        connection.executescript(script)
        return len(split_statements(script))
    statements = split_statements(script)
    for statement in statements:
        connection.execute(statement)
    return len(statements)


def query(connection: Connection, statement: str, params: tuple, dialect: str) -> list:
    """One parameterised read, on either driver. Rows come back in the driver's shape."""
    return connection.execute(sql(statement, dialect), params).fetchall()


def scalar(row: Any) -> Any:
    """The single column of a ONE-COLUMN row, whatever shape the driver returned.

    Three shapes reach this: `sqlite3.Row` (indexable, not a Mapping), a `dict` from
    psycopg's `dict_row` factory -- which `engine.connect` installs -- and a plain tuple
    from a psycopg connection opened without one. `row["version"]` works for the first
    two and raises for the third; `row[0]` works for the first and third and raises for
    the second. Neither spelling alone is correct, which is why this exists rather than
    the accessors picking one and the other configuration failing at the call site.
    """
    if isinstance(row, Mapping):
        return next(iter(row.values()))
    return row[0]
