"""The tenancy store's item operations. Step 3 of the DynamoDB plan.

WHAT THIS FILE DEFENDS, AND WHY IT MATTERS MORE THAN THE SQL EQUIVALENT. Under
Postgres a careless accessor was caught by the database: RLS is a property of the
connection and no SQL could bypass it. On DynamoDB the pipeline gets no such
backstop -- `dynamodb:LeadingKeys` cannot constrain a principal that legitimately
spans tenants -- so `_dynamo_store` IS the isolation for that path.

The design answer is to make the wrong request unphraseable rather than checked:
no function accepts a `pk`. These tests pin that property from the outside, plus
the two places it could still leak -- an attribute map smuggling a partition, and
the one lookup that starts without a tenant.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentorg.db import _dynamo
from agentorg.tenancy import _dynamo_store as store


class FakeTable:
    """An in-memory stand-in for a boto3 `Table`, WITH PAGINATION.

    Deliberately not a Mock, and deliberately not a single-page dict. This
    repository has found fifteen doubles that could not express the failing case,
    and two failing cases live here: a Query that returns only part of the answer
    (DynamoDB caps a response at 1 MB and signals more with `LastEvaluatedKey`),
    and a GSI that returns another tenant's keys. A fake that always answers in
    one page makes the first untestable and would let a single-page read ship.
    """

    def __init__(self, page_size: int = 100):
        self.items: dict[tuple[str, str], dict] = {}
        self.page_size = page_size
        self.queries: list[dict] = []

    # -- writes ------------------------------------------------------------
    def put_item(self, Item):
        self.items[(Item["pk"], Item["sk"])] = dict(Item)
        return {}

    def delete_item(self, Key):
        self.items.pop((Key["pk"], Key["sk"]), None)
        return {}

    def update_item(self, **kwargs):
        return {}

    # -- reads -------------------------------------------------------------
    def get_item(self, Key, ConsistentRead=False):
        found = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(found)} if found else {}

    def query(self, **kwargs):
        self.queries.append(kwargs)
        values = kwargs.get("ExpressionAttributeValues", {})

        if kwargs.get("IndexName") == "gsi1":
            # KEYS_ONLY: the real index returns ONLY the key attributes, and the
            # fake must too -- otherwise a test could not tell a leak of
            # existence from a leak of contents.
            wanted = values[":k"]
            rows = [
                {"pk": v["pk"], "sk": v["sk"]}
                for v in [self.items[k] for k in sorted(self.items)]
                if v.get("gsi1pk") == wanted
            ]
            # SPARSE: a row without `gsi1pk` is absent from the index entirely,
            # which is what keeps the three singleton rows out of it.
            limit = kwargs.get("Limit")
            return {"Items": rows[:limit] if limit else rows}

        pk, prefix = values[":pk"], values[":sk"]
        rows = [
            dict(v) for (k_pk, k_sk), v in sorted(self.items.items())
            if k_pk == pk and k_sk.startswith(prefix)
        ]
        start = kwargs.get("ExclusiveStartKey")
        if start:
            after = (start["pk"], start["sk"])
            rows = [r for r in rows if (r["pk"], r["sk"]) > after]
        page = rows[: self.page_size]
        answer: dict = {"Items": page}
        if len(rows) > self.page_size:
            answer["LastEvaluatedKey"] = {"pk": page[-1]["pk"], "sk": page[-1]["sk"]}
        return answer


# ── the property the whole design rests on ───────────────────────────────────

def test_no_store_function_accepts_a_partition_key():
    """A caller cannot name a partition, so a caller cannot name the wrong one.

    Asserted over the SIGNATURES rather than by reading the module, because this
    is a claim about the interface a future author will program against. The
    moment one function grows a `pk=` parameter, the pipeline's only protection
    becomes a code review.
    """
    import inspect

    offenders = []
    for name in ("get", "query_prefix", "put", "delete", "find_by_run_id"):
        params = inspect.signature(getattr(store, name)).parameters
        if any(p in params for p in ("pk", "partition", "partition_key")):
            offenders.append(name)

    assert not offenders, (
        f"{offenders} accept a partition key. Every function must BUILD its own "
        f"from tenant_id -- that is what makes a cross-tenant request impossible "
        f"to phrase rather than merely forbidden."
    )


def test_an_attribute_map_cannot_smuggle_a_partition():
    """`put` copies attributes FIRST and sets the key AFTER.

    Written the other way round, `attributes={"pk": "TENANT#victim"}` would
    choose the tenant -- a client-supplied tenant_id wearing a different hat,
    which is what every layer in this repository refuses.
    """
    table = FakeTable()

    store.put(table, "attacker", _dynamo.sk(_dynamo.SK_RUN, "r-1"), {
        "pk": _dynamo.tenant_pk("victim"),
        "sk": "RUN#somebody-elses",
        "status": "running",
    })

    assert list(table.items) == [(_dynamo.tenant_pk("attacker"), "RUN#r-1")], (
        f"the attribute map chose the key: {list(table.items)}"
    )


# ── the breach the plan requires (§6) ────────────────────────────────────────

def test_a_run_belonging_to_another_tenant_is_REFUSED_not_returned():
    """GSI1 is the one index reachable without a tenant in the key.

    This is the attempt `docs/design/dynamodb-migration.md` §6 requires. It has
    to be an attempt rather than an assertion about the code, following
    `tests/test_tenancy_leak.py`: an isolation claim nobody tried to break is not
    evidence.
    """
    table = FakeTable()
    store.put(table, "victim", _dynamo.sk(_dynamo.SK_RUN, "r-secret"), {"status": "promoted"})

    with pytest.raises(store.CrossTenantKey):
        store.find_by_run_id(table, "attacker", "r-secret")


def test_the_refusal_leaks_EXISTENCE_and_never_CONTENTS():
    """KEYS_ONLY is doing work, and this is what it buys.

    Even on the refusal path the index returned only `pk`/`sk`, so a caller who
    forgot the comparison would learn that a run id exists -- one they already
    had -- and nothing about it. The message must not carry the row.
    """
    table = FakeTable()
    store.put(table, "victim", _dynamo.sk(_dynamo.SK_RUN, "r-secret"), {
        "status": "promoted",
        "ticket_id": "SECRET-TICKET-42",
    })

    with pytest.raises(store.CrossTenantKey) as caught:
        store.find_by_run_id(table, "attacker", "r-secret")

    assert "SECRET-TICKET-42" not in str(caught.value), (
        "the refusal message carries the victim's row contents"
    )
    index_reads = [q for q in table.queries if q.get("IndexName") == "gsi1"]
    assert index_reads, "GSI1 was never queried; this test pinned nothing"


def test_the_victim_can_read_their_own_run():
    """The positive control. Without it, a `find_by_run_id` that refused
    everything would satisfy both tests above."""
    table = FakeTable()
    store.put(table, "victim", _dynamo.sk(_dynamo.SK_RUN, "r-secret"), {"status": "promoted"})

    found = store.find_by_run_id(table, "victim", "r-secret")

    assert found is not None and found["status"] == "promoted", found


def test_a_run_that_does_not_exist_is_NONE_and_not_a_refusal():
    """"No such run" and "somebody else's run" are different facts.

    Collapsing them would be the reassuring direction and wrong: a caller that
    cannot tell them apart eventually reports a refusal as an absence.
    """
    assert store.find_by_run_id(FakeTable(), "anyone", "never-existed") is None


# ── the silent-truncation hazard ─────────────────────────────────────────────

def test_a_listing_reads_EVERY_page():
    """DynamoDB caps a Query at 1 MB and returns `LastEvaluatedKey`, not an error.

    So a single-page read returns a PREFIX of the answer, and a short list of
    runs reads as "this tenant has fewer runs" -- the same failure shape as an
    unscoped read returning nothing: a plausible answer to a question nobody
    asked.
    """
    table = FakeTable(page_size=3)
    for i in range(10):
        store.put(table, "t1", _dynamo.sk(_dynamo.SK_RUN, f"r-{i:02d}"), {"status": "done"})

    rows = store.query_prefix(table, "t1", _dynamo.SK_RUN)

    assert len(rows) == 10, f"read {len(rows)} of 10 rows; pagination stopped early"


def test_a_listing_never_crosses_into_another_tenant():
    """The partition is built from the scope, so this cannot fail without the
    key builder being wrong -- which is exactly why it is asserted."""
    table = FakeTable()
    store.put(table, "t1", _dynamo.sk(_dynamo.SK_RUN, "mine"), {})
    store.put(table, "t2", _dynamo.sk(_dynamo.SK_RUN, "theirs"), {})

    rows = store.query_prefix(table, "t1", _dynamo.SK_RUN)

    assert [r["sk"] for r in rows] == ["RUN#mine"], rows


def test_a_prefix_query_does_not_match_a_LONGER_token():
    """`begins_with` on a bare token would over-match.

    `RUN` without its separator also matches a future `RUNBOOK#`, which returns a
    superset while looking correct. The separator is appended for every
    non-singleton token for this reason.
    """
    table = FakeTable()
    store.put(table, "t1", _dynamo.sk(_dynamo.SK_RUN, "real"), {})
    table.put_item(Item={"pk": _dynamo.tenant_pk("t1"), "sk": "RUNBOOK#other"})

    rows = store.query_prefix(table, "t1", _dynamo.SK_RUN)

    assert [r["sk"] for r in rows] == ["RUN#real"], rows
