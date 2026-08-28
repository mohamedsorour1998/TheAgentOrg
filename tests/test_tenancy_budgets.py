"""Budgets fail closed, and tenant zero loses nothing. Lane B, tasks B5 and B6.

OWNER: Lane B. Split from tests/test_tenancy.py so the schema file stays about the
schema; the leak suite is tests/test_tenancy_leak.py.

THE TWO PROPERTIES WORTH THE MOST HERE, both of which are a DIRECTION rather than a
behaviour, and both of which pass a casual reading either way round:

  * A tenant with no budget row is REFUSED. Written the other way the system spends
    without bound and every run reports success -- the failure arrives as an invoice.
  * Tenant zero's `""` is TRANSLATED, never rewritten. `state.py` is frozen and every run
    on disk carries the blank, so a design that migrated the runs could not be built at
    all; one that reassigned a real tenant id to tenant zero would look identical in the
    data and be a cross-tenant write.
"""

import pytest

from agentorg import db
from agentorg.db import engine, migrations
from agentorg.tenancy import budgets, tenant_zero


@pytest.fixture()
def database():
    connection = db.connect()
    migrations.migrate(connection)
    return connection


class _Scope:
    """The minimal scope shape budgets.py reads: a connection and a tenant id.

    Deliberately NOT importing accessors.TenantScope. This file tests budget arithmetic
    and the tenant-zero translation, and coupling it to the accessor module's constructor
    would mean a change there breaks tests about neither. The two fields are the contract
    and it is asserted below rather than assumed.
    """

    def __init__(self, connection, tenant_id):
        self.connection = connection
        self.tenant_id = tenant_id


def _organisation(database, tenant_id):
    with engine.acting_as(tenant_id):
        database.execute(
            'INSERT INTO "organisation" VALUES (?,?,?)', (tenant_id, tenant_id, "now")
        )


def _budget(database, tenant_id, ceiling, spent=0, unlimited=0):
    with engine.acting_as(tenant_id):
        database.execute(
            'INSERT INTO "budget" VALUES (?,?,?,?,?)',
            (tenant_id, ceiling, spent, unlimited, "now"),
        )


# ──────────────────────────────────────────────────────────────────────────────
# B5 -- the ceiling, checked before a run starts
# ──────────────────────────────────────────────────────────────────────────────

def test_a_tenant_with_no_budget_row_is_refused_not_admitted(database):
    """THE DIRECTION THAT MATTERS. Absent must not read as unlimited.

    A tenant onboarded by a path that forgot to write a budget row would otherwise spend
    without bound, with every run reporting success and the first signal an invoice.
    """
    _organisation(database, "t1")
    decision = budgets.check(_Scope(database, "t1"), 100)
    assert not decision.allowed
    assert decision.reason == budgets.NO_BUDGET


def test_the_refusal_for_a_missing_budget_says_it_is_deliberate(database):
    """The explanation reaches a human, and "no budget" reads like a bug otherwise.

    Someone told a run was refused for a missing ceiling needs to know that is the
    designed answer, or they will look for the defect that is not there.
    """
    _organisation(database, "t1")
    text = budgets.check(_Scope(database, "t1"), 1).explain()
    assert "deliberately" in text, text


def test_a_run_within_the_ceiling_is_allowed(database):
    _organisation(database, "t1")
    _budget(database, "t1", ceiling=1000, spent=100)
    decision = budgets.check(_Scope(database, "t1"), 200)
    assert decision.allowed
    assert decision.reason == budgets.ALLOWED


def test_a_run_that_would_cross_the_ceiling_is_refused_before_it_starts(database):
    _organisation(database, "t1")
    _budget(database, "t1", ceiling=1000, spent=900)
    decision = budgets.check(_Scope(database, "t1"), 200)
    assert not decision.allowed
    assert decision.reason == budgets.OVER_CEILING


def test_a_run_landing_exactly_on_the_ceiling_is_within_it(database):
    """The boundary, stated. An off-by-one here refuses a run and nothing says why."""
    _organisation(database, "t1")
    _budget(database, "t1", ceiling=1000, spent=800)
    assert budgets.check(_Scope(database, "t1"), 200).allowed


def test_an_unlimited_tenant_is_allowed_past_any_number(database):
    _organisation(database, "t1")
    _budget(database, "t1", ceiling=0, spent=10**9, unlimited=1)
    decision = budgets.check(_Scope(database, "t1"), 10**9)
    assert decision.allowed
    assert decision.ceiling_cents is None, (
        "an unlimited decision should report no ceiling rather than the unused column"
    )


def test_the_check_defaults_to_zero_so_standing_can_be_asked_without_an_estimate(
    database,
):
    _organisation(database, "t1")
    _budget(database, "t1", ceiling=1000, spent=1001)
    assert not budgets.check(_Scope(database, "t1")).allowed


def test_a_negative_estimate_raises_rather_than_being_clamped(database):
    """A negative cost silently becoming 0 would admit a run whose caller miscomputed."""
    _organisation(database, "t1")
    _budget(database, "t1", ceiling=1000)
    with pytest.raises(ValueError, match="negative"):
        budgets.check(_Scope(database, "t1"), -1)


def test_the_budget_check_reads_only_the_asking_tenants_row(database):
    """Two tenants, two ceilings. A check must not answer from the other's row.

    Not part of the leak suite, but the same invariant one layer up: a budget read that
    ignored scope would let a tenant spend against somebody else's ceiling.
    """
    for tenant in ("t1", "t2"):
        _organisation(database, tenant)
    _budget(database, "t1", ceiling=100, spent=0)
    _budget(database, "t2", ceiling=10**6, spent=0)

    assert not budgets.check(_Scope(database, "t1"), 500).allowed
    assert budgets.check(_Scope(database, "t2"), 500).allowed


def test_no_float_reaches_a_budget_decision(database):
    """Money is integer cents. A float ceiling against a float spend is a rounding bug."""
    _organisation(database, "t1")
    _budget(database, "t1", ceiling=1000, spent=1)
    decision = budgets.check(_Scope(database, "t1"), 2)
    for name in ("ceiling_cents", "spent_cents", "would_spend_cents"):
        value = getattr(decision, name)
        assert not isinstance(value, float), f"{name} is a float: {value!r}"


def test_the_explanation_never_names_the_tenant(database):
    """It is rendered onto pull requests, where a tenant id is a small disclosure."""
    _organisation(database, "acme-corporation")
    _budget(database, "acme-corporation", ceiling=10, spent=99)
    text = budgets.check(_Scope(database, "acme-corporation"), 5).explain()
    assert "acme-corporation" not in text, text


# ──────────────────────────────────────────────────────────────────────────────
# B6 -- tenant zero
# ──────────────────────────────────────────────────────────────────────────────

def test_the_blank_every_existing_run_carries_translates_to_tenant_zero():
    """`RunState.tenant_id` defaults to "" and ~38,000 documents on disk carry it."""
    assert tenant_zero.for_run_state("") == tenant_zero.TENANT_ZERO_ID


def test_whitespace_translates_too_rather_than_failing_two_frames_later():
    for blank in (" ", "\t", "\n"):
        assert tenant_zero.for_run_state(blank) == tenant_zero.TENANT_ZERO_ID


def test_a_real_tenant_id_is_never_reassigned_to_tenant_zero():
    """The inverse defect, and it would look identical in the data.

    A translation that swallowed real ids would file one customer's runs under tenant
    zero -- a cross-tenant write performed by the compatibility shim.
    """
    assert tenant_zero.for_run_state("acme") == "acme"
    assert not tenant_zero.is_tenant_zero("acme")


def test_tenant_zeros_id_is_not_blank_so_it_has_no_private_code_path():
    """A blank scope is refused by acting_as, so a blank tenant zero would need an
    exception -- and the one tenant behaving differently is the one whose bugs are found
    last."""
    assert tenant_zero.TENANT_ZERO_ID.strip()
    with engine.acting_as(tenant_zero.TENANT_ZERO_ID):
        assert engine.current_tenant() == tenant_zero.TENANT_ZERO_ID


def test_adopt_creates_tenant_zero_and_says_it_did(database):
    assert tenant_zero.adopt(database) is True
    with engine.acting_as(tenant_zero.TENANT_ZERO_ID):
        row = database.execute(
            'SELECT "id" FROM "organisation" WHERE "id" = ?',
            (tenant_zero.TENANT_ZERO_ID,),
        ).fetchone()
    assert row is not None


def test_adopt_is_idempotent_and_reports_that_it_did_nothing(database):
    """A startup step that fails on the second boot is a step somebody comments out."""
    assert tenant_zero.adopt(database) is True
    assert tenant_zero.adopt(database) is False


def test_adopt_does_not_overwrite_a_budget_an_operator_has_since_configured(database):
    """The property that makes it safe to call on every startup."""
    tenant_zero.adopt(database)
    scope = _Scope(database, tenant_zero.TENANT_ZERO_ID)
    with engine.acting_as(tenant_zero.TENANT_ZERO_ID):
        database.execute(
            'UPDATE "budget" SET "ceiling_cents" = ?, "unlimited" = ? '
            'WHERE "tenant_id" = ?',
            (5000, 0, tenant_zero.TENANT_ZERO_ID),
        )
    database.commit()

    tenant_zero.adopt(database)

    decision = budgets.check(scope, 1)
    assert decision.ceiling_cents == 5000, (
        "adopt reset a ceiling an operator had configured"
    )


def test_tenant_zero_is_adopted_unlimited_so_yesterdays_runs_still_run(database):
    """The existing deployment has no ceiling. Inventing one at adoption refuses the
    runs that were working the day before."""
    tenant_zero.adopt(database)
    decision = budgets.check(_Scope(database, tenant_zero.TENANT_ZERO_ID), 10**9)
    assert decision.allowed
    assert decision.ceiling_cents is None


def test_tenant_zero_gets_a_budget_row_rather_than_relying_on_an_absent_one(database):
    """Absent means refused, so "unlimited by omission" would refuse every run.

    This is the seam between B5's direction and B6's adoption, and getting it backwards
    breaks the existing deployment on the first run after the migration.
    """
    tenant_zero.adopt(database)
    with engine.acting_as(tenant_zero.TENANT_ZERO_ID):
        row = database.execute(
            'SELECT "unlimited" FROM "budget" WHERE "tenant_id" = ?',
            (tenant_zero.TENANT_ZERO_ID,),
        ).fetchone()
    assert row is not None, "no budget row; every tenant-zero run would be refused"
    assert row["unlimited"]


def test_tenant_zero_is_subject_to_the_same_guards_as_any_tenant(database):
    """Nothing about it is exempt. Measured through the database, not by inspection."""
    import sqlite3

    tenant_zero.adopt(database)
    _organisation(database, "other")
    with engine.acting_as("other"), pytest.raises(sqlite3.IntegrityError):
        database.execute(
            'UPDATE "organisation" SET "name" = ? WHERE "id" = ?',
            ("renamed by another tenant", tenant_zero.TENANT_ZERO_ID),
        )


def test_the_tenant_zero_id_has_one_spelling_in_the_codebase():
    """A second literal would be a second declaration of the translation's target."""
    assert tenant_zero.TENANT_ZERO_ID is db.TENANT_ZERO_ID
