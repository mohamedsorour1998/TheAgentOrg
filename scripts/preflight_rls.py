"""Preflight check 6 — does the queue's DSN name a role RLS actually binds for?

LANE N, N4. Separate module from `preflight_platform.py` because it is the only check
in this repository that opens a DATABASE connection, and that is a different
dependency (psycopg) from every other check's `aws` and `gh`.

THE WRONG ANSWER, MEASURED 2026-08-28 ON PostgreSQL 16.15. One table, two rows in
different tenants, one RLS policy, two roles:

    as the TABLE OWNER, no tenant bound      2 of 2 rows visible
    as a plain application role, unbound     0 rows
    as a plain application role, tenant=t1   1 row

Postgres skips row-level security for three principals: a SUPERUSER, any role holding
BYPASSRLS, and the TABLE OWNER. `ALTER TABLE ... FORCE ROW LEVEL SECURITY` closes only
the third. So the single choice of which role a DSN names decides whether the tenancy
policies enforce anything at all -- and `pg_policies` LISTS EVERY POLICY EITHER WAY.

WHY THIS CANNOT BE A TEST, A TERRAFORM VARIABLE, OR A GREEN APPLY. It is a property
of the CREDENTIAL, not of the schema:

  * The suite cannot see it. `tests/test_tenancy.py` asserts the emitted DDL
    structurally and CLAUDE.md records that nothing in the suite connects to Postgres.
    Real DDL correctly asserted is exactly what is present in the failing case.
  * Terraform cannot see it. `modules/platform` takes a secret ARN; the role inside
    that secret's value is invisible to a plan, and reading the value at plan time is
    what put a live `github_pat_` into ten Actions artifacts.
  * A green apply cannot see it. Same distinction check 1 draws between an apply and
    `simulate-principal-policy`, one layer down: an apply proves the policy exists,
    this proves it BINDS.

So it is checked against a live connection or not at all. It is the sixth check
because it is the sixth question whose wrong answer has already happened here.

WHAT IT DOES NOT DO. It makes no writes, creates nothing, and reads no tenant data:
three catalogue queries and one `SELECT 1`. It never logs the DSN -- that string
carries a password, and `agentorg/tenancy`'s secret module already establishes that a
credential must not reach a log.
"""

from __future__ import annotations

import importlib
import os
import re

from scripts.preflight import CheckFailed

# The three ways a principal escapes RLS. Named as a table because the failure mode
# differs per row and an operator needs to know WHICH one they have:
#
#   superuser  -- nothing can be done to the schema to fix it. Use another role.
#   BYPASSRLS  -- an attribute on the role; `ALTER ROLE ... NOBYPASSRLS` removes it.
#   the owner  -- `FORCE ROW LEVEL SECURITY` per table fixes it, and is the only one
#                 of the three a schema change can address at all.
#
# Collapsing them into "RLS does not apply" would be the `fixture-fallback` versus
# `fixture-stub` mistake: one message for two conditions wanting different fixes.
_ESCAPE_SUPERUSER = "the role is a SUPERUSER"
_ESCAPE_BYPASSRLS = "the role holds BYPASSRLS"
_ESCAPE_OWNER = "the role OWNS tables that do not FORCE row level security"

# The tenancy tables RLS is meant to protect, from `agentorg/db/schema.py`. Read from
# that module rather than restated -- a hardcoded list here would be a second
# declaration, and a table added there would silently stop being checked while this
# check kept passing. That is the failure this repository has recorded thirteen times.
_SCHEMA_MODULE = "agentorg.db.schema"


def _tenancy_tables() -> list[str]:
    """The scoped table names, read from the schema module.

    Returns `[]` when the module cannot be read that way, and the CALLER treats an
    empty list as a REFUSAL rather than as "no tables to check" -- an empty set would
    make every assertion below vacuously true, which is how a check reads as coverage
    while checking nothing.
    """
    try:
        schema = importlib.import_module(_SCHEMA_MODULE)
    except ImportError:
        return []

    names: list[str] = []
    for attribute in dir(schema):
        value = getattr(schema, attribute, None)
        # The schema declares tables as objects carrying a `name`; accept either that
        # or a plain string constant, because this reads another lane's module and
        # must not depend on its internal shape.
        candidate = getattr(value, "name", None)
        if isinstance(candidate, str) and candidate:
            names.append(candidate)
    return sorted(set(names))


def _redact(dsn: str) -> str:
    """A DSN with its password replaced. NEVER print the raw value.

    `postgresql://user:secret@host:5432/db` -> `postgresql://user:***@host:5432/db`.
    Anchored on the LAST `@` before the host, because a password may itself contain
    one -- a greedy match would leak the tail of it.
    """
    return re.sub(r"(?<=://)([^:/@]+):([^@]*)@", r"\1:***@", dsn)


def check_the_dsn_role_is_bound_by_rls(dsn: str = "") -> str:
    """Check 6. Connect as the DSN's role and ask Postgres whether RLS applies to it.

    Three catalogue reads and one `SELECT 1`. No tenant data, no writes.
    """
    dsn = dsn or os.environ.get("QUEUE_DSN", "")

    if not dsn:
        # A LOUD SKIP. `QUEUE_BACKEND` defaults to `memory` and `QUEUE_DSN` to `""`,
        # so this is the ordinary state of a machine that is not running the queue.
        # Failing would make preflight refuse the documented default.
        return (
            "SKIPPED -- QUEUE_DSN is not set, so there is no database role to check.\n"
            "NOTHING HERE CHECKED TENANT ISOLATION. This is the DEFAULT state:\n"
            "QUEUE_BACKEND defaults to `memory` and modules/platform's\n"
            "runtime_enabled is false. Set QUEUE_DSN to the value the worker will\n"
            "use -- the same secret modules/platform injects -- and re-run."
        )

    if not dsn.startswith(("postgres://", "postgresql://")):
        # SQLITE IS NOT A FAILURE, AND SAYING SO IS THE POINT. CLAUDE.md records the
        # asymmetry: SQLite has no mechanism that constrains a SELECT against a base
        # table -- no RLS, and a trigger cannot fire on a read -- so on that dialect a
        # read is only as scoped as its accessor's `WHERE tenant_id = ?`. That is a
        # real and documented limit, not a misconfiguration, and it is what the whole
        # test suite runs against.
        return (
            f"dsn:      {_redact(dsn)}\n"
            f"SKIPPED -- this is not a PostgreSQL DSN, so there is no RLS to bind.\n"
            f"NOT A FAILURE, and the distinction is documented: SQLite has no\n"
            f"mechanism that constrains a SELECT against a base table, so a read is\n"
            f"only as scoped as its accessor's `WHERE tenant_id = ?`. Writes are\n"
            f"still refused by triggers. A multi-tenant DEPLOYMENT needs Postgres."
        )

    try:
        psycopg = importlib.import_module("psycopg")
    except ImportError as absent:
        raise CheckFailed(
            "psycopg is not installed, so this check cannot run -- and it is the ONLY\n"
            "check that can tell you whether tenant isolation binds at all.\n"
            "\n"
            "REFUSED RATHER THAN SKIPPED, because a Postgres DSN was supplied: the\n"
            "caller has asked for the multi-tenant path, and answering `skipped`\n"
            "there would report green for the one question that has a wrong answer\n"
            "with rows in it. Install psycopg[binary] -- it is pinned in\n"
            "infra/worker/requirements.txt, which is the worker's own environment."
        ) from absent

    lines = [f"dsn:      {_redact(dsn)}"]
    escapes: list[str] = []

    try:
        with (psycopg.connect(dsn, connect_timeout=10) as connection,
              connection.cursor() as cursor):
            cursor.execute(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            row = cursor.fetchone()
            if row is None:
                raise CheckFailed(
                    "\n".join(lines) + "\n"
                    "\npg_roles has no row for current_user, which should be\n"
                    "impossible. Not guessed at: an unrecognised condition is\n"
                    "refused rather than classified, because a classifier that\n"
                    "guesses is worse than one admitting it did not recognise\n"
                    "the answer."
                )
            user, superuser, bypassrls = row
            lines.append(f"role:     {user}")
            lines.append(f"superuser={superuser}  bypassrls={bypassrls}")
            if superuser:
                escapes.append(_ESCAPE_SUPERUSER)
            if bypassrls:
                escapes.append(_ESCAPE_BYPASSRLS)

            # THE OWNER CASE, WHICH IS THE ONE THAT SURPRISED ME. Checked per
            # table, and only for tables that both exist and have RLS enabled --
            # a table with no RLS at all is a different finding, reported below
            # rather than folded in.
            declared = _tenancy_tables()
            cursor.execute(
                "SELECT tablename, tableowner, rowsecurity, "
                "       relforcerowsecurity "
                "FROM pg_tables "
                "JOIN pg_class ON pg_class.relname = pg_tables.tablename "
                "WHERE schemaname = 'public'"
            )
            present = {
                name: (owner, rls, forced)
                for name, owner, rls, forced in cursor.fetchall()
            }
            lines.append(f"tables:   {len(present)} in public")

            owned_unforced = [
                name for name, (owner, rls, forced) in sorted(present.items())
                if owner == user and rls and not forced
            ]
            if owned_unforced:
                escapes.append(
                    f"{_ESCAPE_OWNER}: {', '.join(owned_unforced)}"
                )

            # RLS OFF ENTIRELY IS A SEPARATE FINDING from an escaping role, and
            # it is reported for the tables the schema module NAMES. A table
            # present with `rowsecurity = false` is not protected by anything,
            # regardless of which role connects.
            unprotected = [
                name for name in declared
                if name in present and not present[name][1]
            ]
            if declared:
                lines.append(
                    f"declared: {len(declared)} scoped tables in "
                    f"{_SCHEMA_MODULE}, {len(unprotected)} without RLS"
                )
            else:
                # ANTI-VACUITY. An empty declared list would make `unprotected`
                # empty too, so the RLS-off half of this check would pass while
                # examining nothing.
                lines.append(
                    f"declared: COULD NOT READ table names from "
                    f"{_SCHEMA_MODULE} -- the RLS-enabled half of this check "
                    f"examined nothing"
                )

            cursor.execute("SELECT 1")
    except CheckFailed:
        raise
    except Exception as failure:  # any driver or network failure is a real answer
        import logging

        logging.getLogger(__name__).exception(
            "check 6 could not reach the database named by QUEUE_DSN"
        )
        raise CheckFailed(
            "\n".join(lines) + "\n"
            f"\nconnecting as this DSN raised: {type(failure).__name__}: {failure}\n"
            f"\nREFUSED RATHER THAN SKIPPED. A Postgres DSN was supplied, so the\n"
            f"caller asked for the multi-tenant path; a database that cannot be\n"
            f"reached is a fault, and reporting it as `skipped` would make an\n"
            f"unreachable database read like an unconfigured one."
        ) from failure

    if escapes:
        raise CheckFailed(
            "\n".join(lines) + "\n"
            + "\n".join(f"  - {escape}" for escape in escapes) + "\n"
            "\n"
            "ROW LEVEL SECURITY DOES NOT APPLY TO THIS ROLE, so every tenancy policy\n"
            "is decoration and a cross-tenant read RETURNS ROWS. MEASURED on\n"
            "PostgreSQL 16.15, one table with one policy:\n"
            "\n"
            "    as the TABLE OWNER, no tenant bound      2 of 2 rows visible\n"
            "    as a plain application role, unbound     0 rows\n"
            "\n"
            "`pg_policies` LISTS EVERY POLICY IN BOTH CASES, so the schema reads as\n"
            "correct and nothing anywhere says isolation is off. That is this\n"
            "project's signature failure shape, on the one guarantee a multi-tenant\n"
            "product cannot lose.\n"
            "\n"
            "Remedy, by which escape you have:\n"
            "  SUPERUSER  -- no schema change helps. Connect as a different role.\n"
            "  BYPASSRLS  -- ALTER ROLE <role> NOBYPASSRLS.\n"
            "  OWNER      -- either ALTER TABLE <t> FORCE ROW LEVEL SECURITY for each\n"
            "                table, or (better) run migrations as the owner and give\n"
            "                the application a SEPARATE non-owning role with only\n"
            "                the GRANTs it needs. The second is what\n"
            "                modules/platform's queue_dsn_secret_arn expects."
        )

    return "\n".join(lines) + "\nRLS BINDS for this role."
