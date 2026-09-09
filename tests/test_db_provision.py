"""The two-role model, hermetically — what it renders and what it REFUSES.

LANE R, CLAUDE.md open item 1. `tests/test_db_postgres.py` is the half that needs a
database and attempts the breach; this half needs none.

THE DOUBLE HERE CAN EXPRESS THE FAILING CASE, which is the property this repository's
named pattern is about. `_Catalogue` answers `pg_roles` and `pg_tables` from values the
test chooses, so "the role is a superuser" and "a table is missing" are both reachable —
the two refusals that matter are the ones a real database would only produce by being
misconfigured.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from agentorg.db import provision, schema
from agentorg.db.migrations import LEDGER_TABLE

# THE THREE ESCAPES, AS A LITERAL. Derived from `Escapes`'s own fields, the
# parametrisation below would shrink silently when one was deleted -- Lane H's twelfth
# instance, where dropping a name took a file from 32 passed to 31 passed and nothing
# failed. The anchor test asserts the literal and the dataclass agree in BOTH directions.
_ESCAPES = ("superuser", "bypassrls", "owns")


def _only(name: str) -> dict:
    """`Escapes` kwargs with exactly one escape held and the other two clear."""
    clean = {"superuser": False, "bypassrls": False, "owns": ()}
    return {**clean, name: ("run",) if name == "owns" else True}


class _Catalogue:
    """A connection that answers only the catalogue reads `provision` performs.

    Not a sqlite3.Connection, so `_dialect.driver_dialect` classifies it as Postgres --
    which is what a real psycopg connection does on a host where this suite never imports
    psycopg.
    """

    def __init__(self, *, superuser=False, bypassrls=False, tables=(), owned=()):
        self.role_row = {"rolsuper": superuser, "rolbypassrls": bypassrls}
        self.tables = tuple(tables)
        self.owned = tuple(owned)
        self.executed: list[str] = []
        self.commits = 0

    def execute(self, statement, params=()):
        self.executed.append(statement)
        if "FROM pg_roles" in statement:
            return _Rows([self.role_row])
        if "tableowner" in statement:
            return _Rows([{"tablename": t} for t in self.owned])
        if "FROM pg_tables" in statement:
            return _Rows([{"tablename": t} for t in self.tables])
        if "has_schema_privilege" in statement:
            return _Rows([{"c": False}])
        return _Rows([])

    def commit(self):
        self.commits += 1


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _full_catalogue(**kwargs):
    return _Catalogue(tables=tuple(t.name for t in schema.TABLES) + (LEDGER_TABLE,), **kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# WHAT IT RENDERS
# ──────────────────────────────────────────────────────────────────────────────


def test_the_grants_are_per_table_and_never_ON_ALL_TABLES():
    """`ON ALL TABLES` covers only what exists when it runs, and says so nowhere.

    Granting before a second schema file leaves the later tables ungranted, and the
    symptom is `relation "sessions" does not exist` -- which reads as a missing migration.
    Written per table, the grant IS the list.
    """
    names = tuple(t.name for t in schema.TABLES)
    assert names, "schema.TABLES is empty; this test would pin nothing"

    rendered = provision.render_grants(provision.APP_ROLE, names)

    assert "ON ALL TABLES" not in rendered.upper()
    for name in names:
        assert f'ON "{name}" TO' in rendered, f"{name} was not granted by name"


def test_usage_on_the_schema_comes_first_because_table_grants_alone_do_nothing():
    """A hardened deployment revokes USAGE from PUBLIC, and then every table 'vanishes'."""
    rendered = provision.render_grants(provision.APP_ROLE, ("run",))

    assert rendered.splitlines()[0] == 'GRANT USAGE ON SCHEMA public TO "agentorg_app";'


def test_the_migration_ledger_is_never_granted_to_the_application_role():
    """A role that can rewrite the version history can make a database claim a shape it
    does not have -- which is the one thing the checksum guard exists to catch."""
    sequence = provision.render_sequence(required=provision.WEB_TABLES)

    assert LEDGER_TABLE not in sequence
    assert '"run"' in sequence, "no table reached the sequence; this test would pin nothing"


def test_the_sequence_names_every_schema_file_before_the_grants():
    """The ORDER is the trap. A sequence that grants first reads perfectly correct."""
    sequence = provision.render_sequence(required=provision.WEB_TABLES)
    lines = sequence.splitlines()

    migrate_at = next(i for i, line in enumerate(lines) if "migrate(c, POSTGRES)" in line)
    grant_at = next(i for i, line in enumerate(lines) if line.startswith("GRANT"))
    assert migrate_at < grant_at, "the sequence grants before the schema exists"
    assert any("web/lib/schema.sql" in line for line in lines), (
        "the sequence does not name the second schema file, which is the whole trap"
    )


def test_an_identifier_that_would_need_escaping_is_refused():
    """GRANT takes no parameters, so identifiers are interpolated. Refused, not escaped:
    an escaping routine nothing exercises is worse than a shape nothing accepts."""
    for hostile in ('run"; DROP TABLE run; --', "", "public.run", "rôle"):
        with pytest.raises(ValueError, match="not a plain identifier"):
            provision.render_grants(hostile, ("run",))


def test_nothing_in_this_module_accepts_a_password():
    """A function taking one puts it in a call stack, a log line, and then a fixture.

    `provision` refuses an absent role and prints the CREATE ROLE for an operator to run
    by hand, which is why no password is ever built into a string here.
    """
    offenders = []
    for name, function in vars(provision).items():
        if not callable(function) or not inspect.isfunction(function):
            continue
        parameters = inspect.signature(function).parameters
        offenders += [
            f"{name}({p})" for p in parameters
            if any(word in p.lower() for word in ("password", "passwd", "secret", "pwd"))
        ]

    assert not offenders, f"these take a credential: {offenders}"
    assert "<from your secret store>" in provision.render_create_role()


# ──────────────────────────────────────────────────────────────────────────────
# THE THREE ESCAPES
# ──────────────────────────────────────────────────────────────────────────────


def test_the_escape_literal_and_the_dataclass_agree_in_both_directions():
    """The anchor. Missing from the literal is an untested escape; present and not on the
    dataclass is a stale name whose parametrised cases assert nothing."""
    declared = {f.name for f in dataclasses.fields(provision.Escapes)} - {"role"}

    assert declared == set(_ESCAPES), (
        f"Escapes declares {sorted(declared)} and this file parametrises over "
        f"{sorted(_ESCAPES)}. Postgres skips RLS for a superuser, for BYPASSRLS and for "
        f"the table owner; a fourth would need a case here."
    )


# What each escape must be NAMED as in `why_not`. A separate literal on purpose: the
# reason text is what an operator acts on, so "binds is False" without the right word in
# it sends them to the wrong fix.
_ESCAPE_WORD = {"superuser": "SUPERUSER", "bypassrls": "BYPASSRLS", "owns": "OWNS"}


@pytest.mark.parametrize("escape", _ESCAPES)
def test_each_escape_alone_is_enough_to_stop_rls_binding(escape):
    """Any ONE of the three is sufficient, so `binds` must be a conjunction of all three.

    The clean case is asserted separately below, so this is not merely "binds is always
    False" -- which every one of these cases would satisfy.
    """
    escapes = provision.Escapes(role="r", **_only(escape))

    assert not escapes.binds, f"{escape} alone did not stop RLS binding"
    assert _ESCAPE_WORD[escape] in escapes.why_not().upper(), (
        f"why_not() does not name {escape}: {escapes.why_not()}"
    )


def test_why_not_names_every_reason_rather_than_the_first():
    """Three reasons with three different fixes. Reporting one sends an operator to the
    wrong one and the configuration still leaks after they apply it."""
    escapes = provision.Escapes(role="r", superuser=True, bypassrls=True, owns=("run",))
    reason = escapes.why_not()

    assert "SUPERUSER" in reason
    assert "BYPASSRLS" in reason
    assert "OWNS" in reason
    assert "pg_policies" in reason, (
        "the reason does not say the policies are still listed, which is what makes the "
        "misconfiguration read as healthy"
    )


def test_a_clean_role_binds_and_says_so():
    escapes = provision.Escapes(role="agentorg_app", superuser=False, bypassrls=False, owns=())

    assert escapes.binds
    assert "binds" in escapes.why_not()


# ──────────────────────────────────────────────────────────────────────────────
# THE REFUSALS
# ──────────────────────────────────────────────────────────────────────────────


def test_provisioning_a_superuser_is_refused_before_a_single_grant_runs():
    """Grants against a superuser succeed, and every policy stays decoration."""
    catalogue = _full_catalogue(superuser=True)

    with pytest.raises(ValueError, match="refusing to provision"):
        provision.provision(catalogue, role="agentorg_app")

    assert not [s for s in catalogue.executed if s.startswith("GRANT")], (
        "grants ran against a superuser before the refusal"
    )


def test_provisioning_a_BYPASSRLS_role_is_refused_too():
    """The second escape. A role can hold it without being a superuser, and `pg_policies`
    lists every policy either way."""
    with pytest.raises(ValueError, match="BYPASSRLS"):
        provision.provision(_full_catalogue(bypassrls=True), role="agentorg_app")


def test_a_missing_table_stops_the_grants_and_names_it():
    """Trap 1, as a refusal. Granting now and creating later leaves that table ungranted
    with `relation "sessions" does not exist` as the only symptom."""
    catalogue = _full_catalogue()

    with pytest.raises(LookupError, match="sessions"):
        provision.provision(catalogue, role="agentorg_app", required=("sessions",))

    assert not [s for s in catalogue.executed if s.startswith("GRANT")]


def test_a_role_that_does_not_exist_is_refused_with_the_statement_that_creates_it():
    catalogue = _Catalogue(tables=("run",))
    catalogue.role_row = None
    catalogue.execute = lambda statement, params=(): _Rows([])  # no role rows at all

    with pytest.raises(LookupError, match="CREATE ROLE"):
        provision.escapes_for(catalogue, "nobody")


def test_a_complete_schema_grants_every_table_except_the_ledger():
    catalogue = _full_catalogue()

    report = provision.provision(catalogue, role="agentorg_app")

    assert LEDGER_TABLE in catalogue.tables, "the ledger was absent; this test is vacuous"
    assert LEDGER_TABLE not in report["granted"]
    assert set(report["granted"]) == {t.name for t in schema.TABLES}
    assert catalogue.commits == 1
