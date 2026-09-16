"""The twenty accessors, against DynamoDB. Step 3 of the migration plan.

**THE SIGNATURES ARE IDENTICAL TO `accessors.py`, DELIBERATELY.** That is what
lets `tests/test_tenancy_leak.py` -- which enumerates the registry and attempts a
breach through every accessor -- run against this backend without being rewritten
to suit it. A leak suite adjusted to fit the thing it audits is not a leak suite.

**WHAT CHANGES, AND WHAT MUST NOT.** The storage changes completely. The two
refusals do not: `NotFound` and `CrossTenantAccess` keep their meanings, and the
`oracle_safe` split -- which decides whether a caller may learn that an id exists
in ANOTHER tenant -- is carried over verbatim, because it is a disclosure
decision rather than a SQL detail. `accessors._require`'s docstring is the
authority on both and is not restated here.

**THE ORDER INSIDE `_require` IS THE LOAD-BEARING PART.** The scoped read runs
FIRST; only when it finds nothing does the existence probe run, and its answer is
used solely to pick between two refusals -- never to return data. Written the
other way round the unscoped read becomes the primary path and the scope a filter
applied afterwards, which is one careless `return` from handing over the row.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..db import _dynamo
from . import _dynamo_store as store
from .accessors import CrossTenantAccess, NotFound


def _now() -> str:
    """An ISO-8601 UTC timestamp, matching what the SQL path stored.

    A STRING, not a DynamoDB number, and not a `datetime`. Sort keys and
    comparisons in this table are lexicographic, and ISO-8601 in UTC sorts
    correctly as text -- which a local-time or epoch-second representation does
    not. It also keeps `created_at` byte-identical to the rows already written by
    the Postgres path, so a backfill does not have to convert.
    """
    return datetime.now(UTC).isoformat()


def _require(
    client,
    tenant_id: str,
    sort_key: str,
    label: str,
    identifier: str,
    *,
    oracle_safe: bool = True,
) -> dict:
    """One row in scope, or the refusal that applies. See the module docstring.

    `oracle_safe=False` collapses both cases into `NotFound`, so the caller
    learns only that they cannot see it. That is the honest answer for a
    guessable identifier: a secret NAME like `GITHUB_TOKEN` exists for every
    tenant, so distinguishing "no such secret" from "not yours" would answer
    "does that tenant hold this credential" for any name somebody types. A
    `user_id` is worse -- it answers a question about a PERSON.
    """
    row = store.get(client, tenant_id, sort_key)
    if row is not None:
        return dict(row)

    if not oracle_safe:
        raise NotFound(
            f"no {label} {identifier!r} in this tenant's scope. Whether one "
            f"exists under another tenant is deliberately NOT distinguished "
            f"here: the identifier is guessable, so the distinction would itself "
            f"be the disclosure."
        )

    if not store.exists_anywhere(client, sort_key):
        raise NotFound(
            f"no {label} {identifier!r} exists. (Distinct from a cross-tenant "
            f"refusal on purpose -- see accessors.NotFound.)"
        )
    raise CrossTenantAccess(
        f"{label} {identifier!r} is outside this tenant's scope. No FIELD of "
        f"that row is included here -- only the identifier the caller supplied, "
        f"which for a tenant-keyed table is necessarily a tenant id."
    )


def _rows(client, tenant_id: str, token: str) -> list[dict]:
    """Every row of one type in the caller's own partition, paginated."""
    return [dict(r) for r in store.query_prefix(client, tenant_id, token)]


# ── organisation: self-scoped, a row IS a tenant ─────────────────────────────

def get_organisation(client, tenant_id: str, organisation_id: str) -> dict:
    """Takes the id EXPLICITLY rather than reading the scope, and that is not
    redundancy -- it is what makes the accessor breachable by a test. An accessor
    that can only ever read its own scope cannot be asked for somebody else's."""
    if organisation_id != tenant_id:
        raise CrossTenantAccess(
            f"organisation {organisation_id!r} is outside this tenant's scope."
        )
    return _require(client, tenant_id, _dynamo.SK_ORG, "organisation", organisation_id)


def update_organisation_name(client, tenant_id: str, organisation_id: str, name: str) -> None:
    row = get_organisation(client, tenant_id, organisation_id)
    row["name"] = name
    store.put(client, tenant_id, _dynamo.SK_ORG, row)


# ── membership ───────────────────────────────────────────────────────────────

def list_members(client, tenant_id: str) -> list[dict]:
    return _rows(client, tenant_id, _dynamo.SK_MEMBER)


def get_member(client, tenant_id: str, membership_id: str) -> dict:
    # `oracle_safe=False`: a membership id maps to a PERSON, and answering
    # "exists elsewhere" would disclose that they belong to another organisation.
    return _require(
        client, tenant_id, _dynamo.sk(_dynamo.SK_MEMBER, membership_id),
        "membership", membership_id, oracle_safe=False,
    )


def add_member(client, tenant_id: str, membership_id: str, user_id: str, role: str) -> None:
    store.put(client, tenant_id, _dynamo.sk(_dynamo.SK_MEMBER, membership_id), {
        "id": membership_id, "tenant_id": tenant_id, "user_id": user_id,
        "role": role, "created_at": _now(),
    })


def remove_member(client, tenant_id: str, membership_id: str) -> None:
    # READ FIRST, because **A DYNAMODB DELETE ON AN ABSENT KEY SUCCEEDS.**
    # Without this, removing a membership that does not exist reports success,
    # and so does removing one that belongs to somebody else -- not because the
    # victim's row was touched, but because the caller is told a row was removed
    # when none was.
    #
    # NOTE WHAT THIS IS *NOT*. It is not what stops a cross-tenant delete:
    # `store.delete` builds the key from the caller's own tenant, so the victim's
    # row is unreachable either way. An earlier draft of this comment claimed the
    # read was the protection, and a RED step disproved it -- reversing the two
    # lines failed only the POSITIVE CONTROL, never the breach. Recorded because
    # a comment that overstates a guard is how the next person deletes the guard
    # that actually matters.
    get_member(client, tenant_id, membership_id)
    store.delete(client, tenant_id, _dynamo.sk(_dynamo.SK_MEMBER, membership_id))


# ── repository ───────────────────────────────────────────────────────────────

def list_repositories(client, tenant_id: str) -> list[dict]:
    return _rows(client, tenant_id, _dynamo.SK_REPO)


def get_repository(client, tenant_id: str, repository_id: str) -> dict:
    return _require(
        client, tenant_id, _dynamo.sk(_dynamo.SK_REPO, repository_id),
        "repository", repository_id,
    )


def add_repository(client, tenant_id: str, repository_id: str, full_name: str) -> None:
    store.put(client, tenant_id, _dynamo.sk(_dynamo.SK_REPO, repository_id), {
        "id": repository_id, "tenant_id": tenant_id,
        "full_name": full_name, "created_at": _now(),
    })


# ── run ──────────────────────────────────────────────────────────────────────

def list_runs(client, tenant_id: str) -> list[dict]:
    return _rows(client, tenant_id, _dynamo.SK_RUN)


def get_run(client, tenant_id: str, run_id: str) -> dict:
    return _require(
        client, tenant_id, _dynamo.sk(_dynamo.SK_RUN, run_id), "run", run_id,
    )


# **WHY THE WHOLE STATE DOCUMENT RIDES ON THE INDEX ROW.** The run's own record is
# `gates.save`, and on the deployed pipeline that is a JSONL file on whichever Actions
# runner ran the stage, handed forward as an artifact. The web application cannot read
# an Actions artifact -- so `/runs/<id>` could show the index row and NOTHING else, and
# rendered every stage as `NOT STARTED` for a run whose `plan` had genuinely succeeded.
# That is the did-not-run-versus-passed conflation this repository exists to refuse,
# on a screen.
#
# A COPY, NOT A MOVE. `gates.save` remains the single writer of the run's record; this
# is a denormalised read model for one screen, written by the same call that already
# writes the index. Moving the document here instead would mean removing the artifact
# handoff across seven jobs, which was attempted and reverted -- see
# `run-pipeline.yml`'s header.
#
# BOUNDED, BECAUSE DYNAMODB REFUSES AN ITEM OVER 400 KB AND `record_run` SWALLOWS
# EVERY FAILURE. A state that grew past the limit would make indexing stop entirely
# and silently -- the run list would simply stop gaining rows. Measured on real runs:
# a complete one is ~5.9 KB, so the bound is ~60x headroom and exists to fail
# VISIBLY rather than to be reached.
STATE_BYTES_LIMIT = 350_000


def record_run(client, tenant_id: str, run_id: str, ticket_id: str,
               status: str, state_ref: str, state_json: str = "") -> None:
    item = {
        "run_id": run_id, "tenant_id": tenant_id, "ticket_id": ticket_id,
        "status": status, "state_ref": state_ref, "created_at": _now(),
    }
    item.update(_state_attributes(state_json))
    store.put(client, tenant_id, _dynamo.sk(_dynamo.SK_RUN, run_id), item)


def _state_attributes(state_json: str) -> dict:
    """`state` when it fits, and a NAMED refusal when it does not.

    `state_too_large` is written rather than the field being quietly omitted,
    because an absent `state` and an oversized one want different fixes and look
    identical on the screen -- the same reason `scan_provenance` distinguishes
    `fixture-fallback` from `fixture-stub`.
    """
    if not state_json:
        return {}
    if len(state_json.encode("utf-8")) > STATE_BYTES_LIMIT:
        return {"state_too_large": True}
    return {"state": state_json}


def update_run_status(client, tenant_id: str, run_id: str, status: str,
                      state_json: str = "") -> None:
    row = get_run(client, tenant_id, run_id)
    row["status"] = status
    # REFRESHED AT EVERY STAGE, because `_emit` calls this from every stage and the
    # screen is meant to follow a run as it happens. A state written only at `plan`
    # would show the ticket and then never change.
    row.pop("state_too_large", None)
    row.update(_state_attributes(state_json))
    store.put(client, tenant_id, _dynamo.sk(_dynamo.SK_RUN, run_id), row)


# ── secret ───────────────────────────────────────────────────────────────────

def list_secret_names(client, tenant_id: str) -> list[str]:
    """NAMES ONLY. The ciphertext, nonce and mac stay in the store — a listing is
    the one call whose result is most likely to reach a log line or a template."""
    return [str(r["name"]) for r in _rows(client, tenant_id, _dynamo.SK_SECRET)]


def get_secret_row(client, tenant_id: str, name: str) -> dict:
    # `oracle_safe=False`: `GITHUB_TOKEN` exists for every tenant.
    return _require(
        client, tenant_id, _dynamo.sk(_dynamo.SK_SECRET, name),
        "secret", name, oracle_safe=False,
    )


def put_secret(client, tenant_id: str, secret_id: str, name: str, nonce: str,
               ciphertext: str, mac: str, cipher: str) -> None:
    store.put(client, tenant_id, _dynamo.sk(_dynamo.SK_SECRET, name), {
        "id": secret_id, "tenant_id": tenant_id, "name": name, "nonce": nonce,
        "ciphertext": ciphertext, "mac": mac, "cipher": cipher,
        "created_at": _now(),
    })


def delete_secret(client, tenant_id: str, name: str) -> None:
    get_secret_row(client, tenant_id, name)  # refuse before deleting; see remove_member
    store.delete(client, tenant_id, _dynamo.sk(_dynamo.SK_SECRET, name))


# ── budget ───────────────────────────────────────────────────────────────────

def get_budget(client, tenant_id: str, budget_tenant_id: str) -> dict:
    """A budget is keyed BY tenant, so naming the row names its owner. That is
    not a disclosure: the caller supplied the value, so they already had it."""
    if budget_tenant_id != tenant_id:
        raise CrossTenantAccess(
            f"budget {budget_tenant_id!r} is outside this tenant's scope."
        )
    return _require(client, tenant_id, _dynamo.SK_BUDGET, "budget", budget_tenant_id)


def set_budget(client, tenant_id: str, budget_tenant_id: str,
               ceiling_cents: int, unlimited: bool) -> None:
    if budget_tenant_id != tenant_id:
        raise CrossTenantAccess(
            f"budget {budget_tenant_id!r} is outside this tenant's scope."
        )
    existing = store.get(client, tenant_id, _dynamo.SK_BUDGET) or {}
    store.put(client, tenant_id, _dynamo.SK_BUDGET, {
        "tenant_id": tenant_id, "ceiling_cents": int(ceiling_cents),
        # SPEND SURVIVES A CEILING CHANGE. Resetting it here would let a budget
        # be raised and the spend forgotten in one call, which is a way to spend
        # past a ceiling that no test of the ceiling itself would catch.
        "spent_cents": int(existing.get("spent_cents", 0)),
        "unlimited": bool(unlimited), "updated_at": _now(),
    })


def add_spend(client, tenant_id: str, budget_tenant_id: str, cents: int) -> None:
    """A tenant with NO budget row is REFUSED, not admitted.

    Absent must not read as unlimited -- the same direction as a blank
    `ci_status_measured` that must not read as `unknown`. `_require` raises here
    for free, because there is nothing to update.
    """
    if budget_tenant_id != tenant_id:
        raise CrossTenantAccess(
            f"budget {budget_tenant_id!r} is outside this tenant's scope."
        )
    row = _require(client, tenant_id, _dynamo.SK_BUDGET, "budget", budget_tenant_id)
    row["spent_cents"] = int(row.get("spent_cents", 0)) + int(cents)
    row["updated_at"] = _now()
    store.put(client, tenant_id, _dynamo.SK_BUDGET, row)
