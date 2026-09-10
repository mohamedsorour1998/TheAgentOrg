"""THE LEAK SUITE, against DynamoDB. Step 3's acceptance criterion.

`tests/test_tenancy_leak.py` attempts a real cross-tenant breach through every
registered accessor on the SQL backend. This file does the same against
`_dynamo_accessors`, and the two must stay in step: an accessor added to one
backend and not the other is a hole, and a leak suite that only audits the
backend being retired audits nothing.

**WHY IT MATTERS MORE HERE.** On Postgres the last line of defence was RLS -- a
property of the connection that no careless accessor could bypass. On DynamoDB
the pipeline has no such backstop, because `dynamodb:LeadingKeys` cannot
constrain a principal that legitimately spans tenants. So for that path these
attempts ARE the evidence, not a supplement to it.

Every attempt is driven through the PUBLIC accessor, never through the store, for
the reason the SQL suite gives: the store is where the protection is implemented,
and a test that calls it directly checks the implementation against itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentorg.tenancy import _dynamo_accessors as d
from agentorg.tenancy import accessors as sql_accessors
from agentorg.tenancy.accessors import CrossTenantAccess, NotFound
from tests.test_dynamo_store import FakeTable

VICTIM = "tenant-victim"
ATTACKER = "tenant-attacker"

# One breach attempt per accessor: (args the ATTACKER passes to reach the
# VICTIM's row). Restated as a LITERAL rather than derived from the registry --
# a parametrisation built from the thing under test empties silently when the
# thing changes, which this repository has measured twice (SEVERITY_ORDER, and
# guard.VERDICT_ARGUMENTS going 32 passed -> 31 passed with nothing failing).
ATTEMPTS: dict[str, tuple] = {
    "get_organisation": (VICTIM,),
    "update_organisation_name": (VICTIM, "renamed-by-attacker"),
    "get_member": ("m-victim",),
    "remove_member": ("m-victim",),
    "get_repository": ("repo-victim",),
    "get_run": ("run-victim",),
    "update_run_status": ("run-victim", "promoted"),
    "get_secret_row": ("GITHUB_TOKEN",),
    "delete_secret": ("GITHUB_TOKEN",),
    "get_budget": (VICTIM,),
    "set_budget": (VICTIM, 999_999, True),
    "add_spend": (VICTIM, 500),
}

# Accessors that take no victim-owned identifier: a listing or a create. They
# cannot be "aimed" at another tenant, so they are audited by the listing test
# below instead of by a breach attempt.
NOT_AIMABLE = {
    "list_members", "list_repositories", "list_runs", "list_secret_names",
    "add_member", "add_repository", "record_run", "put_secret",
}


def _seed(table: FakeTable) -> None:
    """The victim's rows, one per table, written through the real accessors."""
    d.store.put(table, VICTIM, "ORG", {"id": VICTIM, "name": "Victim Ltd"})
    d.add_member(table, VICTIM, "m-victim", "u-victim", "reviewer")
    d.add_repository(table, VICTIM, "repo-victim", "victim/private")
    d.record_run(table, VICTIM, "run-victim", "TICKET-1", "promoted", "local://x")
    d.put_secret(table, VICTIM, "s-1", "GITHUB_TOKEN", "n", "CIPHERTEXT-XYZ", "m", "v1")
    d.set_budget(table, VICTIM, VICTIM, 10_000, False)
    # The attacker's own organisation, so their scope is real rather than empty.
    d.store.put(table, ATTACKER, "ORG", {"id": ATTACKER, "name": "Attacker Ltd"})


def test_every_ported_accessor_is_either_attempted_or_declared_unaimable():
    """The anti-vacuity check, in BOTH directions.

    Missing from `ATTEMPTS` means an accessor is breach-tested by nothing while
    the file still reports a row of passes. Present but no longer real means the
    literal has gone stale and is attempting something that does not exist.
    """
    ported = {
        name for name in dir(d)
        if not name.startswith("_") and callable(getattr(d, name))
        and name in sql_accessors.ACCESSORS
    }

    untested = ported - set(ATTEMPTS) - NOT_AIMABLE
    assert not untested, f"accessors with no breach attempt and not declared unaimable: {sorted(untested)}"

    stale = (set(ATTEMPTS) | NOT_AIMABLE) - ported
    assert not stale, f"attempts naming accessors that do not exist: {sorted(stale)}"

    assert len(ported) == 20, f"expected 20 ported accessors, found {len(ported)}"


@pytest.mark.parametrize("name", sorted(ATTEMPTS))
def test_the_attacker_cannot_reach_the_victims_row(name):
    """The breach itself, through the public accessor."""
    table = FakeTable()
    _seed(table)

    with pytest.raises((CrossTenantAccess, NotFound)):
        getattr(d, name)(table, ATTACKER, *ATTEMPTS[name])


@pytest.mark.parametrize("name", sorted(ATTEMPTS))
def test_a_refused_WRITE_left_the_victims_row_untouched(name):
    """A refusal that still wrote is worse than no refusal, because the exception
    reassures the caller while the damage is done.

    `remove_member` and `delete_secret` are the sharp cases: DynamoDB's delete on
    an absent key SUCCEEDS, so an accessor that deleted before checking would
    report a refusal on the read it never did and silently destroy nothing -- or,
    with the key built from the wrong tenant, destroy something.
    """
    table = FakeTable()
    _seed(table)
    before = {k: dict(v) for k, v in table.items.items()}

    with pytest.raises((CrossTenantAccess, NotFound)):
        getattr(d, name)(table, ATTACKER, *ATTEMPTS[name])

    assert table.items == before, (
        f"{name} refused and still modified the store: "
        f"{set(before) ^ set(table.items)}"
    )


@pytest.mark.parametrize("name", sorted(ATTEMPTS))
def test_the_victim_can_do_what_the_attacker_could_not(name):
    """THE POSITIVE CONTROL, and without it every test above is satisfied by an
    accessor that refuses everybody."""
    table = FakeTable()
    _seed(table)

    getattr(d, name)(table, VICTIM, *ATTEMPTS[name])


@pytest.mark.parametrize(
    "name", ["list_members", "list_repositories", "list_runs", "list_secret_names"]
)
def test_a_listing_never_includes_another_tenants_rows(name):
    """A listing cannot be aimed, so it is audited by what it returns."""
    table = FakeTable()
    _seed(table)

    assert getattr(d, name)(table, ATTACKER) == [], (
        f"{name} returned the victim's rows to the attacker"
    )
    assert getattr(d, name)(table, VICTIM), (
        f"{name} returned nothing for the VICTIM either; this test would pin nothing"
    )


def test_a_refusal_never_carries_the_secret_it_refused():
    """`get_secret_row` is `oracle_safe=False`, so the refusal must not even
    confirm the name exists elsewhere -- and must certainly not carry ciphertext."""
    table = FakeTable()
    _seed(table)

    with pytest.raises(NotFound) as caught:
        d.get_secret_row(table, ATTACKER, "GITHUB_TOKEN")

    message = str(caught.value)
    assert "CIPHERTEXT-XYZ" not in message, "the refusal carried the ciphertext"
    assert VICTIM not in message, "the refusal named the owning tenant"


def test_a_guessable_identifier_gets_NOTFOUND_and_never_CROSSTENANT():
    """The `oracle_safe` split, asserted as behaviour.

    A secret name and a membership id are guessable, so the two refusals must be
    INDISTINGUISHABLE -- otherwise the exception type answers "does that tenant
    hold this credential", or "does that person belong to another organisation".
    """
    table = FakeTable()
    _seed(table)

    for call in (
        lambda: d.get_secret_row(table, ATTACKER, "GITHUB_TOKEN"),
        lambda: d.get_member(table, ATTACKER, "m-victim"),
    ):
        with pytest.raises(NotFound) as caught:
            call()
        assert not isinstance(caught.value, CrossTenantAccess), (
            "a guessable identifier produced CrossTenantAccess, which confirms "
            "the row exists in another tenant"
        )


def test_an_UNGUESSABLE_identifier_DOES_distinguish_the_two_refusals():
    """The other half, without which `oracle_safe` could be hardcoded False.

    A run id is UUID-shaped, so telling a legitimate caller "no such run" rather
    than "not yours" is the useful answer and discloses nothing enumerable.
    """
    table = FakeTable()
    _seed(table)

    with pytest.raises(CrossTenantAccess):
        d.get_run(table, ATTACKER, "run-victim")

    with pytest.raises(NotFound) as caught:
        d.get_run(table, ATTACKER, "run-that-never-existed")
    assert not isinstance(caught.value, CrossTenantAccess)
