"""The queue on DynamoDB. Step 5 of the migration plan.

**THIS IS THE ONE PART OF THE SYSTEM DYNAMODB SUITS BETTER THAN SQL**, and the
reason is the same one that disqualified SQS. `_sql.py`'s ADR records it: SQS's
nearest thing to a pause is a visibility timeout **capped at 12 hours**, so a gate
awaiting a human silently becomes claimable after half a day and the run merges
with an approval nobody gave.

Here a pause is an ordinary item with no timeout at all, and the claim is a
CONDITIONAL WRITE -- `attribute_not_exists(claimed_by)` -- which is atomic in one
round trip. The SQL backend needs a transaction plus a UNIQUE index to get the
same guarantee.

**THE CLAIM REMAINS AT-LEAST-ONCE, NOT EXACTLY-ONCE.** Nothing here changes
`_sql.py`'s honest limit: two workers cannot hold one job, but a lease that
expired while its worker was ALIVE BUT WEDGED cannot be ruled out without a
fencing token the work itself honours. `reclaimed_from` is still the only trace
that a stage may have run twice, and `worker._already_ran` is still what reads
the run's own record before re-running such a job.

**JOBS ARE TENANT-PARTITIONED LIKE EVERYTHING ELSE**, at `TENANT#<t> /
JOB#<job_id>`. That is what lets the same `dynamodb:LeadingKeys` condition cover
them, and it is why `claim` -- which spans tenants by construction -- is a
service-role operation and reads GSI2 rather than a partition.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import _dynamo

# Status values that a claim may pick up. Restated as a literal rather than
# imported from the Job model, deliberately: this is the set the INDEX is keyed
# on, and a second declaration is the only way to notice the two drifting apart.
STATUS_READY = "ready"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _gsi2(status: str) -> str:
    """The index partition a job of this status sits in.

    Keyed on STATUS, sorted by creation time, so `claim` reads the oldest READY
    job with one Query rather than scanning and filtering -- a filter reads, and
    bills for, every item before discarding it.
    """
    return f"STATUS{_dynamo.SEP}{status}"


def job_item(tenant_id: str, job_id: str, **attributes: Any) -> dict:
    """The item a job is stored as, with both index keys attached.

    `gsi2sk` is the CREATION time and never the update time. Sorted by update
    time, a job that heartbeats would keep moving to the back of the queue and a
    busy run could starve one that has been ready for hours -- fair-looking, and
    not FIFO.
    """
    created = attributes.get("created_at") or _iso(_now())
    status = attributes.get("status", STATUS_READY)
    return {
        **attributes,
        "job_id": job_id,
        "tenant_id": tenant_id,
        "status": status,
        "created_at": created,
        "gsi2pk": _gsi2(status),
        "gsi2sk": created,
    }


class DynamoQueue:
    """`QueueBackend` over the tenancy table. The client is injected."""

    def __init__(self, client):
        self._client = client

    # -- writes ------------------------------------------------------------

    def enqueue(self, tenant_id: str, job_id: str, **attributes: Any) -> dict:
        """Add a job. Refuses to overwrite one that already exists.

        `attribute_not_exists(pk)` rather than a bare put: a re-enqueue of a live
        job would reset its status and lose a claim, and PutItem REPLACES at a
        (pk, sk) pair rather than failing. That is the same hazard `modules/state`
        documents for its own sort key, arriving through a different door.
        """
        item = job_item(tenant_id, job_id, **attributes)
        item["pk"] = _dynamo.tenant_pk(tenant_id)
        item["sk"] = _dynamo.sk(_dynamo.SK_JOB, job_id)
        item["gsi1pk"] = item["sk"]
        self._client.put_item(
            Item=item, ConditionExpression="attribute_not_exists(pk)"
        )
        return item

    def claim(self, tenant_id: str, job_id: str, worker: str,
              lease_seconds: int = 300) -> dict | None:
        """Take a READY job, atomically. None if somebody else got there first.

        **THE CONDITION IS THE WHOLE GUARANTEE.** `attribute_not_exists(claimed_by)`
        AND `status = ready` are checked by DynamoDB as part of the write, so two
        workers racing produce one winner and one `ConditionalCheckFailedException`
        -- no transaction, no index, no second round trip.

        Written as a read-then-write it would look correct and be wrong: both
        workers would read `ready`, both would write, and the second would
        silently overwrite the first's claim. The run would then execute twice
        with nothing recording that it had.
        """
        lease_until = _iso(_now() + timedelta(seconds=lease_seconds))
        try:
            answer = self._client.update_item(
                Key={
                    "pk": _dynamo.tenant_pk(tenant_id),
                    "sk": _dynamo.sk(_dynamo.SK_JOB, job_id),
                },
                UpdateExpression=(
                    "SET #s = :running, claimed_by = :w, lease_until = :l, "
                    "gsi2pk = :gs"
                ),
                ConditionExpression=(
                    "attribute_not_exists(claimed_by) AND #s = :ready"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":running": STATUS_RUNNING,
                    ":ready": STATUS_READY,
                    ":w": worker,
                    ":l": lease_until,
                    ":gs": _gsi2(STATUS_RUNNING),
                },
                ReturnValues="ALL_NEW",
            )
        # A broad handler that RE-RAISES everything it does not recognise, which
        # is why BLE001 does not fire and a suppression comment here was refused
        # as unused. Matched on the CLASS NAME rather than by importing
        # botocore.exceptions: that exception is generated per-client at runtime,
        # so there is no stable symbol to catch and an import would tie this
        # module to a vendor for one string.
        except Exception as exc:
            if type(exc).__name__ == "ConditionalCheckFailedException":
                return None
            raise
        return dict(answer.get("Attributes", {}))

    def pause(self, tenant_id: str, job_id: str, gate: str) -> dict:
        """Pause at a gate. **A DURABLE ROW, NOT A HELD SLOT.**

        No timeout, no visibility window, no expiry. A gate awaiting a human may
        wait indefinitely and the row simply says so -- which is the property SQS
        could not provide and the reason `_sql.py` chose SQL in the first place.
        The claim is released so a resumed job is claimable again.
        """
        answer = self._client.update_item(
            Key={
                "pk": _dynamo.tenant_pk(tenant_id),
                "sk": _dynamo.sk(_dynamo.SK_JOB, job_id),
            },
            UpdateExpression=(
                "SET #s = :paused, awaiting_gate = :g, gsi2pk = :gs "
                "REMOVE claimed_by, lease_until"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":paused": STATUS_PAUSED,
                ":g": gate,
                ":gs": _gsi2(STATUS_PAUSED),
            },
            ReturnValues="ALL_NEW",
        )
        return dict(answer.get("Attributes", {}))

    # -- reads -------------------------------------------------------------

    def get(self, tenant_id: str, job_id: str) -> dict | None:
        answer = self._client.get_item(
            Key={
                "pk": _dynamo.tenant_pk(tenant_id),
                "sk": _dynamo.sk(_dynamo.SK_JOB, job_id),
            },
            ConsistentRead=True,
        )
        return answer.get("Item")

    def awaiting(self) -> list[dict]:
        """Every paused job, across tenants. A SERVICE-ROLE READ.

        Spans partitions by construction -- "which runs are waiting for a human"
        is not a question one tenant can answer -- so it reads GSI2 and is
        refused to a tenant-scoped principal by the explicit index Deny in
        `modules/tenancy/iam.tf`.
        """
        return self._query_status(STATUS_PAUSED)

    def ready(self) -> list[dict]:
        """Every claimable job, oldest first. The other service-role read."""
        return self._query_status(STATUS_READY)

    def _query_status(self, status: str) -> list[dict]:
        rows: list[dict] = []
        start: dict | None = None
        while True:
            kwargs: dict[str, Any] = {
                "IndexName": "gsi2",
                "KeyConditionExpression": "gsi2pk = :s",
                "ExpressionAttributeValues": {":s": _gsi2(status)},
                # Oldest first: gsi2sk is the creation time, so this is FIFO.
                "ScanIndexForward": True,
            }
            if start:
                kwargs["ExclusiveStartKey"] = start
            answer = self._client.query(**kwargs)
            rows.extend(answer.get("Items", []))
            start = answer.get("LastEvaluatedKey")
            if not start:
                return rows


def dynamo_queue() -> DynamoQueue:
    """The backend `QUEUE_BACKEND=dynamodb` selects.

    **THIS FUNCTION IS WHY THE BACKEND EXISTS AT ALL, AND IT WAS MISSING.** The class
    above shipped in `2a62254` with seven passing tests and no caller: `queue/__init__.py`
    dispatched `memory` and `postgres` and raised `NotImplementedError` for everything
    else, while `config.QUEUE_BACKENDS` did not even admit the name -- so the compose
    file's `QUEUE_BACKEND: dynamodb` raised at import and neither container started.
    Correct code, tested, reached by nothing; this repository's second named pattern.

    THE CLIENT IS BUILT HERE AND NOWHERE ABOVE. `DynamoQueue.__init__` takes an injected
    client so the hermetic suite can drive every path against a double with no
    credentials and no network -- the split `_memory.py` established. A module-level
    `boto3.resource(...)` would undo that for the whole package, because importing the
    queue would then require credentials.

    NO CROSS-TENANT CREDENTIAL IS MINTED HERE, deliberately. The worker legitimately
    spans partitions -- it claims whichever job is next and only then learns whose tenant
    it is -- so it uses the ambient service credential that `modules/tenancy`'s
    `service_role_arns` grants. A caller that wants ONE tenant's rows under the
    `LeadingKeys` condition uses `db/tenant_credentials.table` instead, and the
    difference between those two is the whole of §4 of the migration plan.
    """
    import boto3

    from ..common import config

    return DynamoQueue(
        boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(
            config.TENANCY_TABLE
        )
    )
