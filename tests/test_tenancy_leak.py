"""THE LEAK SUITE. Lane B, B7.

OWNER: Lane B. Cross-tenant leakage is the one defect that would end this as a product,
so this file is not "assert isolation" -- it ATTEMPTS the breach on every accessor and
asserts each attempt is refused.

WHY THAT DISTINCTION IS THE WHOLE VALUE. An assertion that another tenant's data is absent
passes when isolation works, and equally when the row was never written, the fixture is
wrong, the table is empty, or the query is broken. It cannot fail for the right reason
because it cannot tell those apart -- which is this repository's signature defect, the
check that cannot distinguish "did not run" from "passed". An attempt that must be refused
has no such reading: something has to raise, and only the guard can raise it.

SO EVERY BREACH ATTEMPT IS PAIRED WITH A POSITIVE CONTROL. Before asserting that tenant B
cannot read a row, the suite asserts tenant A CAN read that same row. Without that half, a
refusal proves only that nothing was there -- and a suite that deleted its own fixtures
would pass every isolation test it has.

THE ACCESSORS ARE ENUMERATED FROM `accessors.ACCESSORS`, not listed here. A new accessor
registers itself through the decorator it needs anyway to be scoped, so it is breach-tested
automatically rather than silently unguarded. `test_every_registered_accessor_is_attempted`
is the guard on that guard: it fails if any registered accessor has no attempt built for
it, so the enumeration cannot quietly cover less than it claims.

TWO REFUSAL TYPES, AND WHY THE SUITE DOES NOT KEY ON THE TYPE. `CrossTenantAccess` says
"that is not yours"; `NotFound` says "no such thing here". Accessors keyed by an
UNGUESSABLE id (a run id, a repository id) raise the first, which is more informative and
costs nothing. Accessors keyed by something guessable -- a secret NAME like
`GITHUB_TOKEN`, or a membership naming a PERSON -- raise `NotFound` for both cases on
purpose, because distinguishing them would itself be the disclosure. So the suite asserts
that a REFUSAL happened and that no data came back, and only checks the specific type
where the accessor promises one.
"""

import sqlite3

import pytest

from agentorg import db
from agentorg.db import engine, migrations, schema
from agentorg.tenancy import accessors, budgets, crypto
from agentorg.tenancy.accessors import CrossTenantAccess, NotFound

# Any refusal from an accessor. Both derive from different builtins deliberately
# (PermissionError and LookupError), so this tuple is the one place they are treated alike.
REFUSALS = (CrossTenantAccess, NotFound)

VICTIM = "tenant-victim"
ATTACKER = "tenant-attacker"

# The victim's resources. Named so a failure message says whose data escaped.
VICTIM_RUN = "victim-run-0001"
VICTIM_REPO = "victim-repo-0001"
VICTIM_MEMBERSHIP = "victim-membership-0001"
VICTIM_USER = "victim-user-0001"
VICTIM_SECRET_NAME = "GITHUB_TOKEN"
VICTIM_SECRET_VALUE = "ghp_VictimsFakeTokenNeverRealOnlyATest01"

ATTACKER_USER = "attacker-user-0001"

# GUARD AGAINST A VACUOUS FILE, in the form CLAUDE.md prescribes: a matcher that can match
# nothing must say so. If the registry were empty every parametrised test below would
# collect zero cases and the file would report success having attempted no breach at all.
assert accessors.ACCESSORS, (
    "accessors.ACCESSORS is empty; every breach attempt in this file would pin nothing"
)
assert accessors.reads(), "no read accessors registered; the read attempts would be empty"
assert accessors.writes(), "no write accessors; the write attempts would be empty"


@pytest.fixture()
def database(monkeypatch):
    """Two tenants, each with a full set of rows. Hermetic: memory only, no network."""
    monkeypatch.setenv(crypto.MASTER_KEY_ENV, "a-test-master-key-for-the-leak-suite")
    connection = db.connect()
    migrations.migrate(connection)

    for tenant in (VICTIM, ATTACKER):
        with engine.acting_as(tenant):
            connection.execute(
                'INSERT INTO "organisation" VALUES (?,?,?)', (tenant, tenant, "now")
            )
            connection.execute(
                'INSERT INTO "budget" VALUES (?,?,?,?,?)', (tenant, 100_000, 0, 0, "now")
            )
    # app_user is global -- no tenant owns it, so these are inserted outside any scope.
    for user in (VICTIM_USER, ATTACKER_USER):
        connection.execute(
            'INSERT INTO "app_user" VALUES (?,?,?)', (user, f"{user}@example.com", "now")
        )

    victim = accessors.scope_for(connection, VICTIM)
    accessors.record_run(victim, VICTIM_RUN, "TICKET-1", "running")
    accessors.add_repository(victim, VICTIM_REPO, "victim/private-repo")
    accessors.add_member(victim, VICTIM_MEMBERSHIP, VICTIM_USER, "admin")
    encrypted = crypto.encrypt(VICTIM_SECRET_VALUE)
    accessors.put_secret(
        victim,
        "victim-secret-0001",
        VICTIM_SECRET_NAME,
        encrypted.nonce,
        encrypted.ciphertext,
        encrypted.mac,
        encrypted.cipher,
    )
    connection.commit()
    return connection


@pytest.fixture()
def victim_scope(database):
    return accessors.scope_for(database, VICTIM)


@pytest.fixture()
def attacker_scope(database):
    return accessors.scope_for(database, ATTACKER)


# ──────────────────────────────────────────────────────────────────────────────
# THE ATTEMPT TABLE. One entry per accessor: how to aim it at the victim's row.
#
# Kept as data rather than as a test each, so `test_every_registered_accessor_is_attempted`
# can compare it against the registry and fail when an accessor is added without an
# attempt. A test-per-accessor would make that comparison impossible, and a new accessor
# would then be unguarded with nothing red.
# ──────────────────────────────────────────────────────────────────────────────

def _attempts() -> dict[str, tuple]:
    """accessor name -> the positional arguments that aim it at the victim's resource."""
    return {
        # organisation
        "get_organisation": (VICTIM,),
        "update_organisation_name": (VICTIM, "renamed by the attacker"),
        # membership
        "list_members": (),
        "get_member": (VICTIM_MEMBERSHIP,),
        "add_member": ("forged-membership", VICTIM_USER, "admin"),
        "remove_member": (VICTIM_MEMBERSHIP,),
        # repository
        "list_repositories": (),
        "get_repository": (VICTIM_REPO,),
        "add_repository": ("forged-repo", "victim/private-repo"),
        # run
        "list_runs": (),
        "get_run": (VICTIM_RUN,),
        "record_run": (VICTIM_RUN, "TICKET-1", "promoted"),
        "update_run_status": (VICTIM_RUN, "promoted"),
        # secret
        "list_secret_names": (),
        "get_secret_row": (VICTIM_SECRET_NAME,),
        "put_secret": (
            "forged-secret", VICTIM_SECRET_NAME, "bm9uY2U=", "Y2lwaGVy", "bWFj",
            crypto.CIPHER_LOCAL_V1,
        ),
        "delete_secret": (VICTIM_SECRET_NAME,),
        # budget
        "get_budget": (VICTIM,),
        "set_budget": (VICTIM, 1),
        "add_spend": (VICTIM, 99_999),
    }


# Accessors that CREATE a row rather than reaching an existing one. They stamp `tenant_id`
# from the scope, so aiming them at a victim's identifier is not a cross-tenant attempt at
# all -- the row lands in the CALLER's scope. Measured: the attacker calling
# `add_repository(attacker, "attacker-repo", "victim/private")` produces
# `{'id': 'attacker-repo', 'tenant_id': 'attacker'}`, and the victim's listing is
# unchanged.
#
# THIS DISTINCTION WAS FOUND BY THIS SUITE, against an earlier attempt table that treated
# them like the rest and failed four cases. The failures were the TEST's, not the code's --
# and the fix is not to make these refuse, because refusing would break the legitimate case
# the schema is built around: two customers may both connect `acme/auth-service`, and a
# global refusal there would fail one customer's onboarding with a message about a
# repository they cannot see.
#
# So what IS asserted for them is the property that actually matters -- the write lands in
# the caller's scope and NOT the victim's -- in
# `test_a_creating_accessor_writes_into_the_callers_own_scope_and_not_the_victims`.
CREATORS = frozenset({"add_member", "add_repository", "record_run", "put_secret"})

# Accessors whose arguments name nothing tenant-specific, so "aiming at the victim" means
# calling them AS the attacker and asserting the victim's rows are not among the results.
# Listed explicitly rather than inferred from an empty argument tuple, because an accessor
# that took no arguments for a different reason would silently join this set.
LISTERS = frozenset({
    "list_members", "list_repositories", "list_runs", "list_secret_names",
})

# The accessors that DO reach an existing row, and so can commit a cross-tenant breach.
def _reaching() -> list[str]:
    return sorted(set(_attempts()) - LISTERS - CREATORS)

# Accessors that promise `CrossTenantAccess` specifically, because their key is
# unguessable. The rest may answer with either refusal -- see the module docstring.
PROMISES_CROSS_TENANT = frozenset({
    "get_organisation", "update_organisation_name",
    "get_repository",
    "get_run", "update_run_status",
    "get_budget", "set_budget", "add_spend",
    "remove_member",
})


def test_every_registered_accessor_is_attempted():
    """THE GUARD ON THE GUARD.

    The suite enumerates the registry, so an accessor added later is breach-tested
    automatically -- but only if an attempt exists for it. Without this test, a new
    accessor would appear in `ACCESSORS`, be skipped by every parametrisation for want of
    arguments, and be reported as covered.
    """
    missing = set(accessors.ACCESSORS) - set(_attempts())
    assert not missing, (
        f"these accessors are registered but have no breach attempt, so nothing in this "
        f"file tries to break them: {sorted(missing)}. Add an entry to _attempts()."
    )
    stale = set(_attempts()) - set(accessors.ACCESSORS)
    assert not stale, (
        f"these attempts name accessors that no longer exist, so they are attacking "
        f"nothing: {sorted(stale)}"
    )


def test_every_scoped_table_has_at_least_one_accessor_attempted():
    """A table with rows and no accessor is a table no attempt can reach.

    So the coverage claim is per-TABLE as well as per-accessor: an accessor added for a new
    table is caught by the test above, but a table added with no accessor at all would
    otherwise pass both silently.
    """
    covered = {accessors.ACCESSORS[name].table for name in _attempts()}
    for table in schema.SCOPED_TABLES:
        assert table.name in covered, (
            f"no accessor for scoped table {table.name!r} is breach-tested"
        )


# ──────────────────────────────────────────────────────────────────────────────
# THE BREACH ATTEMPTS -- one parametrised case per registered accessor
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", _reaching())
def test_the_attacker_cannot_reach_the_victims_row_through_any_accessor(
    name, attacker_scope, victim_scope
):
    """THE CENTRAL TEST. Every accessor that reaches an existing row must refuse.

    Both refusal families are accepted here because two accessors deliberately answer
    `NotFound` to avoid an oracle over guessable names -- see the module docstring. What is
    NOT accepted is returning data, or returning None, or succeeding quietly: a call that
    RETURNS fails the test.

    The creating accessors are excluded and tested separately -- they stamp `tenant_id`
    from the scope, so they cannot reach across one. See CREATORS.
    """
    entry = accessors.ACCESSORS[name]
    arguments = _attempts()[name]

    with pytest.raises(REFUSALS) as caught:
        entry.function(attacker_scope, *arguments)

    if name in PROMISES_CROSS_TENANT:
        assert isinstance(caught.value, CrossTenantAccess), (
            f"{name} promises CrossTenantAccess for an unguessable key but raised "
            f"{type(caught.value).__name__}"
        )


@pytest.mark.parametrize("name", sorted(CREATORS))
def test_a_creating_accessor_writes_into_the_callers_own_scope_and_not_the_victims(
    name, attacker_scope, victim_scope
):
    """The property that matters for an INSERT, since it cannot reach an existing row.

    MEASURED, and it is why these four are not expected to refuse: the attacker calling
    `add_repository(attacker, "attacker-repo", "victim/private")` produces a row owned by
    the attacker, and the victim's listing does not change. Refusing on the shared
    `full_name` would break the case the schema is designed for -- two customers connecting
    a repository of the same name.

    What must hold is that nothing the attacker writes becomes visible to the victim, and
    that the victim's own rows are untouched afterwards.
    """
    entry = accessors.ACCESSORS[name]
    table = entry.table

    before = _all_rows(victim_scope, table)
    # Aim it at the victim's identifiers, exactly as an attacker would.
    try:
        entry.function(attacker_scope, *_attempts()[name])
    except sqlite3.IntegrityError:
        # A UNIQUE collision on a primary key the victim already holds. Also acceptable:
        # nothing was written, which is the outcome being asserted.
        pass
    after = _all_rows(victim_scope, table)

    assert before == after, (
        f"{name}, called by the attacker, changed what the victim can see in {table}:\n"
        f"  before: {before}\n  after:  {after}"
    )

    for row in _all_rows(attacker_scope, table):
        assert row.get("tenant_id", ATTACKER) == ATTACKER, (
            f"{name} produced a row in the attacker's listing owned by "
            f"{row.get('tenant_id')!r}"
        )


def _all_rows(scope, table: str) -> list[dict]:
    """Every row of `table` visible in `scope`, read with the tenant predicate.

    Used by the creator tests as a before/after snapshot. Reads through the scope rather
    than the raw table on purpose: the question is what that TENANT can see.
    """
    tenant_column = schema.TABLES_BY_NAME[table].tenant_column
    with engine.acting_as(scope.tenant_id):
        rows = scope.connection.execute(
            f'SELECT * FROM "{table}" WHERE "{tenant_column}" = ? ORDER BY 1',
            (scope.tenant_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@pytest.mark.parametrize("name", _reaching())
def test_the_victim_can_do_what_the_attacker_could_not(
    name, victim_scope, attacker_scope
):
    """THE POSITIVE CONTROL, and it is what makes the test above mean anything.

    Without this, every refusal would also be satisfied by an empty database -- a suite
    that failed to insert its fixtures would pass every isolation assertion it has. The two
    tests together say: this row exists, its owner reads it, and the attacker cannot.
    """
    entry = accessors.ACCESSORS[name]
    result = entry.function(victim_scope, *_attempts()[name])
    if entry.kind == accessors.READ:
        assert result is not None, (
            f"{name} returned None for the row's OWNER; the fixture is not in place and "
            f"the matching breach attempt would pass against an empty table"
        )


@pytest.mark.parametrize("name", sorted(LISTERS))
def test_a_listing_never_includes_another_tenants_rows(name, attacker_scope, victim_scope):
    """A list accessor cannot be aimed, so the breach it can commit is over-returning.

    Asserted from BOTH sides: the attacker's listing must not contain the victim's
    identifiers, AND the victim's listing must contain them. The second half is the control
    -- an empty result satisfies the first half trivially.
    """
    entry = accessors.ACCESSORS[name]

    attacker_rows = entry.function(attacker_scope)
    serialized = repr(attacker_rows)
    for marker in (
        VICTIM_RUN, VICTIM_REPO, VICTIM_MEMBERSHIP, VICTIM_USER, VICTIM_SECRET_NAME,
        VICTIM_SECRET_VALUE,
    ):
        assert marker not in serialized, (
            f"{name} returned {marker!r} to the wrong tenant: {serialized}"
        )

    victim_rows = entry.function(victim_scope)
    assert victim_rows, (
        f"{name} returned nothing for the OWNER, so the assertion above would hold "
        f"against an empty table and prove nothing"
    )


# ──────────────────────────────────────────────────────────────────────────────
# The write breaches, in the shapes that are easy to get wrong
# ──────────────────────────────────────────────────────────────────────────────

def test_the_attacker_cannot_plant_a_row_in_the_victims_scope(attacker_scope):
    """THE BREACH RUNNING OUTWARD, which a read-only view of isolation misses entirely.

    Planting a row in another tenant's scope is how an attacker gets their code into
    somebody else's pipeline. Attempted directly against the table, not through an
    accessor, because the accessor sets `tenant_id` itself -- so this is the DATABASE
    layer's refusal being tested, which is what B8 asks for.
    """
    with engine.acting_as(ATTACKER), pytest.raises(sqlite3.IntegrityError):
        attacker_scope.connection.execute(
            'INSERT INTO "run" VALUES (?,?,?,?,?,?)',
            ("planted-run", VICTIM, "TICKET-X", "running", "now", None),
        )


def test_the_attacker_cannot_re_tenant_its_own_row_into_the_victims_scope(attacker_scope):
    """Giving data away is a breach of the same invariant as taking it.

    An UPDATE guard that checked only OLD.tenant_id would allow this, and the resulting row
    would look native to the victim.
    """
    accessors.record_run(attacker_scope, "attacker-run", "TICKET-A", "running")
    with engine.acting_as(ATTACKER), pytest.raises(sqlite3.IntegrityError):
        attacker_scope.connection.execute(
            'UPDATE "run" SET "tenant_id" = ? WHERE "run_id" = ?',
            (VICTIM, "attacker-run"),
        )


def test_the_attacker_cannot_delete_the_victims_row_at_the_database_layer(attacker_scope):
    """Destruction needs no read. A suite that only tested reads would miss it."""
    with engine.acting_as(ATTACKER), pytest.raises(sqlite3.IntegrityError):
        attacker_scope.connection.execute(
            'DELETE FROM "run" WHERE "run_id" = ?', (VICTIM_RUN,)
        )


@pytest.mark.parametrize(
    "statement",
    [
        'INSERT OR REPLACE INTO "run" VALUES (?,?,?,?,?,?)',
        'REPLACE INTO "run" VALUES (?,?,?,?,?,?)',
    ],
    ids=["insert-or-replace", "replace"],
)
def test_the_write_paths_that_do_not_look_like_inserts_are_guarded_too(
    statement, attacker_scope
):
    """`REPLACE` reaches the INSERT trigger -- measured, not assumed.

    Worth pinning because a guard written only against `INSERT INTO` would read as complete
    and leave two spellings open.
    """
    with engine.acting_as(ATTACKER), pytest.raises(sqlite3.IntegrityError):
        attacker_scope.connection.execute(
            statement, ("planted", VICTIM, "T", "running", "now", None)
        )


def test_an_upsert_onto_the_victims_existing_row_is_refused(attacker_scope):
    """The UPDATE path of an UPSERT fires the UPDATE trigger. Also measured."""
    with engine.acting_as(ATTACKER), pytest.raises(sqlite3.IntegrityError):
        attacker_scope.connection.execute(
            'INSERT INTO "run" VALUES (?,?,?,?,?,?) '
            'ON CONFLICT("run_id") DO UPDATE SET "status" = ?',
            ("victim-run-0001", ATTACKER, "T", "x", "now", None, "hijacked"),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Fail-closed: no tenant bound at all
# ──────────────────────────────────────────────────────────────────────────────

def test_with_no_tenant_bound_every_scoped_write_is_refused(database):
    """The state the process starts in must be the safe one.

    This is the case the `!=` operator would have admitted -- SQL's three-valued logic
    makes `'t' != NULL` evaluate to NULL, which does not fire a trigger. So without the
    `IS NOT` spelling, a write with NOTHING establishing who is asking would have gone
    through, which is the one moment a guard is most needed.
    """
    with engine.acting_as_nobody():
        for table in schema.SCOPED_TABLES:
            columns = ", ".join("?" for _ in table.columns)
            values = tuple(
                VICTIM if c.name == table.tenant_column else "x"
                for c in table.columns
            )
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(
                    f'INSERT INTO "{table.name}" VALUES ({columns})', values
                )


def test_a_scope_cannot_be_built_from_the_single_tenant_marker(database):
    """`""` must be translated by tenant_zero first, never used as a scope.

    A blank scope matches a blank tenant column, which is a row nobody owns -- and every
    pre-tenancy RunState carries that blank, so this is a real call somebody will make.
    """
    for blank in ("", "  ", "\t"):
        with pytest.raises(ValueError, match="may not be blank"):
            accessors.scope_for(database, blank)


def test_calling_an_accessor_without_a_scope_is_a_type_error(database):
    """THE STRUCTURAL HALF OF B3, and the strongest guarantee in the lane.

    `scope` has no default, so forgetting it is a TypeError raised by Python at the call
    site -- not a runtime check inside the function that a future edit could soften, and
    not a default that hands back somebody else's data. Asserted over EVERY registered
    accessor, so a new one with a defaulted scope fails here.
    """
    for name, entry in accessors.ACCESSORS.items():
        with pytest.raises(TypeError):
            entry.function()
        assert entry.function is not None, name


def test_no_accessor_declares_a_default_for_its_scope_parameter():
    """Read off the SIGNATURE, because the test above cannot see a default that exists.

    An accessor written `def get_run(scope=SOME_SCOPE, ...)` would not raise TypeError when
    called with no arguments -- it would run, against whatever that default names. So the
    signature is inspected directly: `scope` must be first and must have no default.
    """
    import inspect

    for name, entry in accessors.ACCESSORS.items():
        parameters = list(inspect.signature(entry.function).parameters.values())
        assert parameters, f"{name} takes no parameters at all"
        first = parameters[0]
        assert first.name == "scope", (
            f"{name}'s first parameter is {first.name!r}, not 'scope'; a scope passed "
            f"anywhere but first is a scope a caller can omit positionally"
        )
        assert first.default is inspect.Parameter.empty, (
            f"{name} defaults its scope to {first.default!r}. A default tenant is how "
            f"cross-tenant access happens by accident: the caller who forgets gets data "
            f"rather than an error."
        )


# ──────────────────────────────────────────────────────────────────────────────
# What a refusal is allowed to say
# ──────────────────────────────────────────────────────────────────────────────

def test_no_refusal_message_carries_the_victims_data(attacker_scope):
    """A refusal that quotes the row it refused is the leak, in the error text.

    The message may name the identifier the CALLER supplied -- they already know it, having
    typed it -- but never a FIELD read from the victim's row.

    THE TENANT-ID CLAUSE IS DELIBERATELY NARROW, and getting it right cost a correction.
    An earlier version forbade the victim's tenant id anywhere in any message, which sounds
    stronger and is unachievable: `organisation` and `budget` are keyed BY tenant, so
    naming the row a caller asked for necessarily names its owner. This suite caught the
    contradiction between that assertion and a message claiming the owner was not named.
    The honest rule is: the owning tenant may appear ONLY where it is the key the caller
    supplied, and never for a table keyed by anything else.
    """
    tenant_keyed = {
        name for name in _reaching()
        if schema.TABLES_BY_NAME[accessors.ACCESSORS[name].table].tenant_column
        in ("id", "tenant_id")
        and accessors.ACCESSORS[name].table in ("organisation", "budget")
    }
    assert tenant_keyed, "no tenant-keyed accessors found; the carve-out would pin nothing"

    for name in _reaching():
        entry = accessors.ACCESSORS[name]
        with pytest.raises(REFUSALS) as caught:
            entry.function(attacker_scope, *_attempts()[name])
        message = str(caught.value)

        # No FIELD of the victim's row, on any accessor. These are the values an attacker
        # would actually want, and none is an identifier the caller supplied.
        assert VICTIM_SECRET_VALUE not in message, f"{name} leaked the secret VALUE"
        assert "victim/private-repo" not in message, f"{name} leaked the repo name"
        assert VICTIM_USER not in message, f"{name} leaked a user id"
        assert "TICKET-1" not in message, f"{name} leaked the ticket id"

        if name not in tenant_keyed:
            assert VICTIM not in message, (
                f"{name} names the owning tenant {VICTIM!r} in its refusal, and its table "
                f"is not keyed by tenant, so the id did not come from the caller: {message}"
            )


def test_the_secret_refusal_does_not_reveal_that_another_tenant_holds_the_name(
    attacker_scope,
):
    """The one accessor whose key is guessable, so both cases must answer alike.

    `GITHUB_TOKEN` exists for the victim and not for the attacker. If the refusal for a
    name-that-exists-elsewhere differed from the refusal for a name-nobody-has, an attacker
    could enumerate credentials by name across every tenant.
    """
    with pytest.raises(REFUSALS) as held_by_victim:
        accessors.get_secret_row(attacker_scope, VICTIM_SECRET_NAME)
    with pytest.raises(REFUSALS) as held_by_nobody:
        accessors.get_secret_row(attacker_scope, "A_NAME_NOBODY_HAS_AT_ALL")

    assert type(held_by_victim.value) is type(held_by_nobody.value), (
        "the two cases raise different exception types, so the type is an oracle over "
        "every tenant's secret names"
    )


def test_a_cross_tenant_secret_read_never_returns_decryptable_material(attacker_scope):
    """The end-to-end property, stated as the thing that actually matters.

    Even granting every intermediate step, the question a customer asks is "can another
    customer decrypt my token". Nothing comes back to decrypt.
    """
    with pytest.raises(REFUSALS):
        row = accessors.get_secret_row(attacker_scope, VICTIM_SECRET_NAME)
        crypto.decrypt(crypto.EncryptedRecord(**row))


def test_the_attacker_cannot_spend_against_the_victims_budget(
    attacker_scope, victim_scope
):
    """Budget is the one scoped table where a breach costs money directly."""
    with pytest.raises(REFUSALS):
        accessors.add_spend(attacker_scope, VICTIM, 100_000)

    decision = budgets.check(victim_scope, 1)
    assert decision.allowed, (
        "the victim's budget was consumed by the attacker's attempt"
    )
    assert decision.spent_cents == 0, (
        f"the victim's spend moved to {decision.spent_cents}c"
    )


def test_the_attacker_cannot_raise_its_own_ceiling_by_naming_the_victim(attacker_scope):
    """A write aimed at another tenant's row must not fall back to the caller's own.

    Worth its own test: an accessor that ignored the id argument and used
    `scope.tenant_id` would pass every refusal test above by editing the attacker's row --
    a silent no-op on the victim that reads as correct isolation.
    """
    with pytest.raises(REFUSALS):
        accessors.set_budget(attacker_scope, VICTIM, 10**9)

    own = accessors.get_budget(attacker_scope, ATTACKER)
    assert own["ceiling_cents"] == 100_000, (
        f"the attacker's own ceiling changed to {own['ceiling_cents']} while it was "
        f"aiming at the victim's row"
    )


# ──────────────────────────────────────────────────────────────────────────────
# app_user: unreachable from a scope at all
# ──────────────────────────────────────────────────────────────────────────────

def test_there_is_no_accessor_that_reads_the_global_user_table_directly(attacker_scope):
    """The ADR's answer to a table that cannot be scoped: make it unreachable.

    `app_user` holds a global identity, so a tenant column there would be a lie. Instead no
    accessor touches it except through `membership`, which IS scoped -- so the reachable
    set is exactly this tenant's people and there is no accessor that has to remember a
    predicate the table cannot enforce.
    """
    for name, entry in accessors.ACCESSORS.items():
        assert entry.table != "app_user", (
            f"{name} is registered against app_user, which is unscoped; a scoped accessor "
            f"on a global table cannot enforce what its name implies"
        )


def test_a_listing_of_members_does_not_reveal_the_other_tenants_people(
    attacker_scope, victim_scope
):
    """The enumeration surface `app_user` would otherwise have.

    Both tenants' users exist in one table. The join must confine the answer to the asking
    tenant's memberships, and the control below proves the victim's own listing is not
    simply empty.
    """
    attacker_members = accessors.list_members(attacker_scope)
    assert all(m["user_id"] != VICTIM_USER for m in attacker_members), (
        f"the victim's user appeared in the attacker's member list: {attacker_members}"
    )

    victim_members = accessors.list_members(victim_scope)
    assert [m["user_id"] for m in victim_members] == [VICTIM_USER], (
        f"the victim's own member list is wrong, so the assertion above proves nothing: "
        f"{victim_members}"
    )


def test_the_member_listing_does_not_lose_columns_to_a_name_collision(victim_scope):
    """MEASURED: `dict(sqlite3.Row)` silently collapses duplicate column names.

    `membership JOIN app_user` has `id` and `created_at` on both sides, and an unaliased
    `SELECT *` returns 7 keys that become a 5-key dict -- the second `id` overwritten, with
    nothing raised. A listing that lost `user_id` that way would break the assertion above
    into a vacuous one, so the query names its columns and this test pins the result.
    """
    rows = accessors.list_members(victim_scope)
    assert rows, "no members; this test would pin nothing"
    for row in rows:
        assert set(row) == {"id", "user_id", "role", "email"}, (
            f"member row has unexpected keys {sorted(row)}; a JOIN column collision "
            f"drops fields silently"
        )
        assert row["user_id"] == VICTIM_USER
