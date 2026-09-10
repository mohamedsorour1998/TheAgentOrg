"""Item operations against the tenancy table. Step 3 of the DynamoDB plan.

**NO ACCESSOR HERE MAY NAME A PARTITION.** Every function below takes a
`tenant_id` and builds `TENANT#<tenant_id>` itself; not one of them accepts a
`pk`. That is the whole design, and it is a deliberate answer to the weakness
`docs/design/dynamodb-migration.md` §4 records:

    the WEB app   acts AS a tenant  -> `dynamodb:LeadingKeys` refuses the rest
    the PIPELINE  spans tenants     -> IAM cannot constrain it, by definition

For the pipeline there is no IAM condition to fall back on, so the only thing
between a stage and another tenant's rows is this module. Postgres solved that
with RLS -- a property of the connection, which no amount of careless SQL could
bypass. The nearest available equivalent is to make the wrong request
**impossible to phrase**: a caller cannot ask for a partition, so a caller cannot
ask for the wrong one.

This is weaker than RLS and the file says so rather than implying otherwise. A
bug HERE is a cross-tenant read, where a bug in an accessor's SQL was previously
caught by the database. That is why this module is small, has one way in, and is
driven by the leak suite.

**THE CLIENT IS INJECTED, ALWAYS.** Nothing constructs a boto3 client at import,
so the hermetic suite drives every path against an in-memory double with no
credentials and no network -- the split `agentorg/queue/_memory.py` already
established.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..db import _dynamo


class TableClient(Protocol):
    """The five operations this module issues, and nothing else.

    A Protocol rather than an ABC here, inverting `integrations/base.py`'s
    ruling, and the reason is whose type it is: that module defines an interface
    OUR adapters implement, where a misspelled method must fail at construction.
    This one describes a boto3 `Table` resource -- somebody else's object, which
    cannot inherit from us. Structural typing is the only option that can
    describe it at all.
    """

    def get_item(self, **kwargs: Any) -> dict: ...
    def query(self, **kwargs: Any) -> dict: ...
    def put_item(self, **kwargs: Any) -> dict: ...
    def update_item(self, **kwargs: Any) -> dict: ...
    def delete_item(self, **kwargs: Any) -> dict: ...


class CrossTenantKey(PermissionError):
    """A row was reached whose partition is not the caller's.

    Only `find_by_run_id` can raise this, because it is the only read that starts
    without a tenant. Every other function builds its own partition and cannot
    produce a mismatch.

    A `PermissionError` subclass, matching `accessors.CrossTenantAccess`, so a
    caller that already handles one handles this -- and so a refusal is never
    mistaken for a missing row.
    """


def get(client: TableClient, tenant_id: str, sort_key: str) -> dict | None:
    """One row, or None. The partition is built here, never passed in."""
    answer = client.get_item(
        Key={"pk": _dynamo.tenant_pk(tenant_id), "sk": sort_key},
        ConsistentRead=True,
    )
    return answer.get("Item")


def query_prefix(client: TableClient, tenant_id: str, token: str) -> list[dict]:
    """Every row of one type within one tenant, e.g. every `RUN#`.

    `begins_with` on the sort key, which is why the singleton tokens are bare and
    the rest carry a separator -- `_dynamo.sk` refuses a bare non-singleton for
    exactly this reason.

    **PAGINATED, AND THAT IS NOT TIDINESS.** DynamoDB caps a Query response at
    1 MB and returns `LastEvaluatedKey` rather than an error, so a single-page
    read of a tenant with many runs SILENTLY RETURNS A PREFIX OF THE ANSWER. A
    short list of runs reads as "this tenant has fewer runs", which is the same
    failure shape as an unscoped read returning nothing: a plausible answer to a
    question that was not asked.
    """
    rows: list[dict] = []
    start: dict | None = None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk)",
            "ExpressionAttributeValues": {
                ":pk": _dynamo.tenant_pk(tenant_id),
                ":sk": token if token in _SINGLETONS else f"{token}{_dynamo.SEP}",
            },
            "ConsistentRead": True,
        }
        if start:
            kwargs["ExclusiveStartKey"] = start
        answer = client.query(**kwargs)
        rows.extend(answer.get("Items", []))
        start = answer.get("LastEvaluatedKey")
        if not start:
            return rows


_SINGLETONS = frozenset({_dynamo.SK_ORG, _dynamo.SK_BUDGET, _dynamo.SK_PROFILE})


def put(client: TableClient, tenant_id: str, sort_key: str, attributes: dict) -> None:
    """Write a row into the caller's own partition.

    `pk` and `sk` are set from the arguments AFTER `attributes` is copied in, so
    a caller cannot smuggle a partition through the attribute map. That ordering
    is the entire protection and it is one line; a `dict(attributes, **key)`
    written the other way round would let `attributes={"pk": ...}` choose the
    tenant, which is a client-supplied tenant_id wearing a different hat -- the
    thing every layer in this repository refuses.
    """
    item = dict(attributes)
    item["pk"] = _dynamo.tenant_pk(tenant_id)
    item["sk"] = sort_key
    client.put_item(Item=item)


def delete(client: TableClient, tenant_id: str, sort_key: str) -> None:
    """Remove a row from the caller's own partition."""
    client.delete_item(Key={"pk": _dynamo.tenant_pk(tenant_id), "sk": sort_key})


def find_by_run_id(client: TableClient, tenant_id: str, run_id: str) -> dict | None:
    """A run looked up through GSI1, REFUSING one that belongs elsewhere.

    **THE ONLY READ IN THIS MODULE THAT STARTS WITHOUT A TENANT**, and therefore
    the only one that can produce a cross-tenant result. `gates.load(run_id)` and
    `jobs_for_run` both hold a run id and no tenant, which is precisely the
    lookup the base table cannot answer.

    Two things make it safe, and neither is sufficient alone:

    * **GSI1 is KEYS_ONLY.** The index physically cannot return another tenant's
      attributes -- only `pk` and `sk` come back, so a forgotten comparison leaks
      the EXISTENCE of a run id somebody already had, not its contents.
    * **The comparison is HERE, not at the call site.** A check written inline at
      each caller is a check that will eventually be written differently at one
      of them, and the leak suite has to attempt this specific breach
      (`docs/design/dynamodb-migration.md` §6).

    Raises rather than returning None on a mismatch. None means "no such run",
    and a caller that cannot tell "does not exist" from "belongs to someone else"
    will eventually report the second as the first -- which is the reassuring
    direction, and wrong.
    """
    answer = client.query(
        IndexName="gsi1",
        KeyConditionExpression="gsi1pk = :k",
        ExpressionAttributeValues={":k": f"{_dynamo.SK_RUN}{_dynamo.SEP}{run_id}"},
    )
    items = answer.get("Items", [])
    if not items:
        return None

    mine = _dynamo.tenant_pk(tenant_id)
    for row in items:
        if row.get("pk") == mine:
            # KEYS_ONLY, so the index gave us keys. The ROW comes from the base
            # table, where LeadingKeys applies for a tenant-scoped caller -- the
            # second of the two defences, and the reason the extra round trip is
            # worth paying for.
            return get(client, tenant_id, str(row["sk"]))

    raise CrossTenantKey(
        f"run {run_id!r} exists and is not in {tenant_id!r}. GSI1 is KEYS_ONLY, "
        "so nothing but its existence was read."
    )
