"""The tenancy schema as DATA, rendered to DDL per dialect.

OWNER: Lane B. See ADR-001-database.md for the decision this file implements.

WHY THE SCHEMA IS DATA AND NOT TWO .sql FILES. Two hand-written dialect files are two
declarations of one fact, free to drift, and the drift is silent: SQLite is the tested
path and Postgres is the deployed one, so a table missing from the Postgres file would be
found in production. Here a table cannot exist in one dialect and not the other, because
one `Table` renders to both. `tests/test_tenancy.py` asserts every table renders in every
dialect, which is a real assertion rather than a restatement precisely because the
rendering is derived.

WHY TENANT SCOPING IS A FIELD ON THE TABLE, NOT A CONVENTION. `Table.tenant_column` is
required to be either a real column name or an explicit `None` with a written reason. A
new table therefore cannot become unscoped by nobody thinking about it -- the constructor
raises. `unscoped_reason` is validated non-empty for the same reason: "why is this table
outside tenant scope" is exactly the question whose unwritten answer becomes a breach.

THE DDL THIS EMITS IS WHERE WRITE ISOLATION LIVES, on both dialects:

  * SQLite -- three `BEFORE INSERT/UPDATE/DELETE` triggers per scoped table, each
    comparing against a `current_tenant()` application-defined function.
  * Postgres -- `ENABLE` plus `FORCE ROW LEVEL SECURITY` and one policy per table,
    comparing against `current_setting('agentorg.tenant_id', true)`.

Reads differ, and the difference is stated in the ADR rather than glossed: Postgres's
policy `USING` clause constrains a SELECT, and nothing in SQLite can. So on the tested
path a read is only as scoped as its accessor, which is why the accessors carry an
explicit predicate the leak suite's RED step can remove.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The dialects. Named constants rather than bare strings for config.py's reason: a
# typo'd dialect must raise rather than silently render nothing.
SQLITE = "sqlite"
POSTGRES = "postgres"
DIALECTS = (SQLITE, POSTGRES)

# The application-defined SQLite function and the Postgres setting that carry "who is
# asking". One name each, referenced everywhere, because a second spelling of either is a
# guard that compares against nothing -- and a guard comparing against NULL is the
# fail-open case measured in the ADR.
SQLITE_TENANT_FUNCTION = "current_tenant"
POSTGRES_TENANT_SETTING = "agentorg.tenant_id"

# Tenant zero. `RunState.tenant_id` defaults to "" and every run on disk carries that, so
# the blank is not migrated away -- it is TRANSLATED, in exactly one place
# (`tenancy.tenant_zero`), to this id. Non-blank deliberately: a blank tenant id is
# refused by every accessor, and tenant zero must be an ordinary tenant in every respect
# so that no code path exists which only tenant zero exercises.
TENANT_ZERO_ID = "tenant-zero"
TENANT_ZERO_NAME = "Tenant Zero (the original single-tenant deployment)"


@dataclass(frozen=True)
class Column:
    """One column, in the two dialects' spellings.

    `sqlite_type` and `postgres_type` are both required. A single `type` field mapped by
    a lookup table would be the smaller code and the worse design: TEXT/JSONB and
    INTEGER/BIGINT do not correspond one-to-one, and a lookup that guessed would put the
    guess where nobody reads it. Written out, a reviewer sees both.
    """

    name: str
    sqlite_type: str
    postgres_type: str
    null: bool = False
    primary_key: bool = False
    unique: bool = False
    default: str | None = None
    references: str | None = None  # "table(column)"

    def render(self, dialect: str) -> str:
        kind = self.sqlite_type if dialect == SQLITE else self.postgres_type
        parts = [f'"{self.name}"', kind]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.null and not self.primary_key:
            parts.append("NOT NULL")
        if self.unique and not self.primary_key:
            parts.append("UNIQUE")
        if self.default is not None:
            parts.append(f"DEFAULT {self.default}")
        if self.references is not None:
            parts.append(f"REFERENCES {self.references}")
        return " ".join(parts)


@dataclass(frozen=True)
class Table:
    """One table, its tenant column, and why if it has none."""

    name: str
    columns: tuple[Column, ...]
    tenant_column: str | None
    unscoped_reason: str = ""
    unique_together: tuple[tuple[str, ...], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.name}: duplicate column names in {names}")

        # THE TWO REFUSALS THAT MAKE `tenant_column` MEAN SOMETHING. Without the first, a
        # table could name a scope column that does not exist and every guard built from
        # it would compare against nothing. Without the second, a table becomes unscoped
        # by silence -- which is how the one defect this lane exists to prevent arrives:
        # not by a decision, by an omission nobody had to defend.
        if self.tenant_column is not None:
            if self.tenant_column not in names:
                raise ValueError(
                    f"{self.name}: tenant_column {self.tenant_column!r} is not one of "
                    f"its columns {names}. A scope column that does not exist would "
                    f"render a guard comparing against nothing, which admits every row."
                )
            if self.unscoped_reason:
                raise ValueError(
                    f"{self.name}: has a tenant_column AND an unscoped_reason. One or "
                    f"the other -- a table that is scoped and also explains why it is "
                    f"not is a table nobody can reason about."
                )
        elif not self.unscoped_reason.strip():
            raise ValueError(
                f"{self.name}: tenant_column is None and no unscoped_reason was given. "
                f"A table outside tenant scope is a deliberate decision and must be "
                f"written down; the unwritten version of this decision is a leak."
            )

        for combination in self.unique_together:
            for column in combination:
                if column not in names:
                    raise ValueError(
                        f"{self.name}: unique_together names {column!r}, "
                        f"not among {names}"
                    )

    @property
    def scoped(self) -> bool:
        return self.tenant_column is not None

    def render(self, dialect: str) -> str:
        if dialect not in DIALECTS:
            raise ValueError(
                f"unknown dialect {dialect!r}; expected one of {', '.join(DIALECTS)}"
            )
        body = [c.render(dialect) for c in self.columns]
        for combination in self.unique_together:
            cols = ", ".join(f'"{c}"' for c in combination)
            body.append(f"UNIQUE ({cols})")
        inner = ",\n  ".join(body)
        return f'CREATE TABLE IF NOT EXISTS "{self.name}" (\n  {inner}\n);'

    def render_isolation(self, dialect: str) -> str:
        """The DDL that refuses a cross-tenant WRITE at the database layer.

        SQLite: three triggers. `IS NOT`, never `!=` -- measured in the ADR, `'t2' !=
        NULL` is NULL, which SQLite does not treat as truthy, so a `!=` guard does not
        fire when NO tenant is bound. That is the fail-open case: the guard is absent
        exactly when nothing has established who is asking. Both spellings refuse an
        ordinary mismatch, so the defect survives any hand test.

        The UPDATE trigger tests OLD *and* NEW. OLD alone lets a tenant re-tenant its own
        row into somebody else's scope -- a write that gives data away rather than taking
        it, and it is still a breach of the same invariant.

        Postgres: `ENABLE` plus `FORCE`, and FORCE is not decoration -- without it the
        table owner is exempt from the table's own policies, and the owner is who the
        application connects as by default. A policy the connection ignores reads as
        protection and is none.
        """
        if not self.scoped:
            return ""
        col = self.tenant_column
        if dialect == SQLITE:
            fn = f"{SQLITE_TENANT_FUNCTION}()"
            # One RAISE per verb, built from a template so the three cannot drift into
            # differently-worded refusals -- the message reaches a log an operator reads.
            def abort(verb: str) -> str:
                return (
                    f"BEGIN SELECT RAISE(ABORT, "
                    f"'cross-tenant {verb} refused: {self.name}'); END;"
                )

            return "\n".join([
                f'CREATE TRIGGER IF NOT EXISTS "{self.name}_no_cross_tenant_insert"',
                f'BEFORE INSERT ON "{self.name}"',
                f'WHEN NEW."{col}" IS NOT {fn}',
                abort("insert"),
                "",
                f'CREATE TRIGGER IF NOT EXISTS "{self.name}_no_cross_tenant_update"',
                f'BEFORE UPDATE ON "{self.name}"',
                f'WHEN OLD."{col}" IS NOT {fn} OR NEW."{col}" IS NOT {fn}',
                abort("update"),
                "",
                f'CREATE TRIGGER IF NOT EXISTS "{self.name}_no_cross_tenant_delete"',
                f'BEFORE DELETE ON "{self.name}"',
                f'WHEN OLD."{col}" IS NOT {fn}',
                abort("delete"),
            ])

        setting = f"current_setting('{POSTGRES_TENANT_SETTING}', true)"
        return "\n".join([
            f'ALTER TABLE "{self.name}" ENABLE ROW LEVEL SECURITY;',
            f'ALTER TABLE "{self.name}" FORCE ROW LEVEL SECURITY;',
            f'CREATE POLICY "{self.name}_tenant_isolation" ON "{self.name}"',
            f'  USING ("{col}" = {setting})',
            f'  WITH CHECK ("{col}" = {setting});',
        ])


# ──────────────────────────────────────────────────────────────────────────────
# THE TABLES. B1: organisation, user, membership, repository, run, secret, budget.
# ──────────────────────────────────────────────────────────────────────────────

ORGANISATION = Table(
    name="organisation",
    columns=(
        Column("id", "TEXT", "TEXT", primary_key=True),
        Column("name", "TEXT", "TEXT"),
        Column("created_at", "TEXT", "TIMESTAMPTZ"),
    ),
    # SELF-SCOPED: a row IS a tenant, so the scope column is the primary key and a
    # tenant reads exactly one row -- its own. Not "unscoped": the guards apply, and
    # they are what stops one organisation renaming another.
    tenant_column="id",
)

APP_USER = Table(
    name="app_user",
    columns=(
        Column("id", "TEXT", "TEXT", primary_key=True),
        Column("email", "TEXT", "TEXT", unique=True),
        Column("created_at", "TEXT", "TIMESTAMPTZ"),
    ),
    tenant_column=None,
    # A GLOBAL IDENTITY, DELIBERATELY. One person may belong to several organisations, so
    # a tenant_id here would be a lie -- and the plausible-looking alternative, one user
    # row per organisation, makes "the same person" unrepresentable and turns an email
    # change into a fan-out.
    #
    # The consequence is handled rather than accepted: there is NO tenant-scoped accessor
    # that reads this table, so it is not reachable from a scope at all. Users are read
    # through `membership`, joined and filtered, which means no caller can enumerate the
    # user base and no accessor has to remember a predicate this table cannot enforce.
    unscoped_reason=(
        "app_user is a global identity: one person may hold memberships in several "
        "organisations, so no single tenant owns the row. It is unreachable from tenant "
        "scope -- membership is the only route in, and that table IS scoped."
    ),
)

MEMBERSHIP = Table(
    name="membership",
    columns=(
        Column("id", "TEXT", "TEXT", primary_key=True),
        Column("tenant_id", "TEXT", "TEXT", references='"organisation"("id")'),
        Column("user_id", "TEXT", "TEXT", references='"app_user"("id")'),
        Column("role", "TEXT", "TEXT"),
        Column("created_at", "TEXT", "TIMESTAMPTZ"),
    ),
    tenant_column="tenant_id",
    # One person holds at most one role per organisation. Without this, two rows can
    # disagree about somebody's role and which one wins is whichever the query happened
    # to return first.
    unique_together=(("tenant_id", "user_id"),),
)

REPOSITORY = Table(
    name="repository",
    columns=(
        Column("id", "TEXT", "TEXT", primary_key=True),
        Column("tenant_id", "TEXT", "TEXT", references='"organisation"("id")'),
        Column("full_name", "TEXT", "TEXT"),
        Column("created_at", "TEXT", "TIMESTAMPTZ"),
    ),
    tenant_column="tenant_id",
    # `full_name` is UNIQUE PER TENANT, never globally: two customers may both connect a
    # repository called `acme/auth-service`, and a global unique constraint would make
    # one customer's onboarding fail with a message about a repository they cannot see.
    unique_together=(("tenant_id", "full_name"),),
)

RUN = Table(
    name="run",
    columns=(
        Column("run_id", "TEXT", "TEXT", primary_key=True),
        Column("tenant_id", "TEXT", "TEXT", references='"organisation"("id")'),
        Column("ticket_id", "TEXT", "TEXT"),
        Column("status", "TEXT", "TEXT"),
        Column("created_at", "TEXT", "TIMESTAMPTZ"),
        # The run's own state document stays where it is -- runs/<id>.state.json or the
        # DynamoDB item. This table INDEXES runs by tenant; it does not become a third
        # writer of RunState. `state_ref` records where the document lives, the way
        # gates.StateRef formats itself, so nothing has to guess a backend.
        Column("state_ref", "TEXT", "TEXT", null=True),
    ),
    tenant_column="tenant_id",
)

SECRET = Table(
    name="secret",
    columns=(
        Column("id", "TEXT", "TEXT", primary_key=True),
        Column("tenant_id", "TEXT", "TEXT", references='"organisation"("id")'),
        Column("name", "TEXT", "TEXT"),
        # THE CIPHERTEXT AND NOTHING ELSE. No plaintext column exists, so there is no
        # column a careless write could put a token in. The three parts are stored
        # separately because a single blob invites reading it as one opaque string and
        # skipping the MAC check.
        Column("nonce", "TEXT", "TEXT"),
        Column("ciphertext", "TEXT", "TEXT"),
        Column("mac", "TEXT", "TEXT"),
        # WHICH CIPHER WROTE THIS ROW, recorded the way SecurityResult.scan_provenance
        # records which scanner mode produced a verdict. A KMS migration is then visible
        # in the data, and a silent downgrade to the local cipher is detectable rather
        # than inferred from a deployment date.
        Column("cipher", "TEXT", "TEXT"),
        Column("created_at", "TEXT", "TIMESTAMPTZ"),
    ),
    tenant_column="tenant_id",
    unique_together=(("tenant_id", "name"),),
)

BUDGET = Table(
    name="budget",
    columns=(
        Column("tenant_id", "TEXT", "TEXT", primary_key=True,
               references='"organisation"("id")'),
        # INTEGER CENTS, never a float. A float ceiling compared against a float spend is
        # a rounding bug with a currency symbol in front of it, and the direction it
        # rounds decides whether a run is admitted.
        Column("ceiling_cents", "INTEGER", "BIGINT"),
        Column("spent_cents", "INTEGER", "BIGINT", default="0"),
        # UNLIMITED IS A COLUMN, SET EXPLICITLY. The alternative -- a NULL ceiling
        # meaning unlimited -- makes "nobody has configured this yet" and "this tenant
        # may spend without bound" the same value. `budgets.check` refuses a tenant with
        # no row at all, so a missing budget is a refusal rather than a blank cheque.
        Column("unlimited", "INTEGER", "BOOLEAN", default="0"),
        Column("updated_at", "TEXT", "TIMESTAMPTZ"),
    ),
    tenant_column="tenant_id",
)

# Order matters: a FOREIGN KEY may only name a table that already exists.
TABLES: tuple[Table, ...] = (
    ORGANISATION,
    APP_USER,
    MEMBERSHIP,
    REPOSITORY,
    RUN,
    SECRET,
    BUDGET,
)

TABLES_BY_NAME = {t.name: t for t in TABLES}
SCOPED_TABLES = tuple(t for t in TABLES if t.scoped)
UNSCOPED_TABLES = tuple(t for t in TABLES if not t.scoped)


def render_schema(dialect: str) -> str:
    """Every table, then every isolation rule, as one DDL script."""
    if dialect not in DIALECTS:
        raise ValueError(
            f"unknown dialect {dialect!r}; expected one of {', '.join(DIALECTS)}"
        )
    chunks = [t.render(dialect) for t in TABLES]
    chunks += [t.render_isolation(dialect) for t in SCOPED_TABLES]
    return "\n\n".join(c for c in chunks if c) + "\n"
