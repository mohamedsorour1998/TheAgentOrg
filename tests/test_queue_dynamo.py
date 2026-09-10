"""The DynamoDB queue backend. Step 5 of the migration plan.

The claim is the only thing here worth real scrutiny. Everything else is storage;
`claim` is a CONCURRENCY guarantee, and the failing case -- two workers taking one
job -- is invisible to any test whose double cannot express a lost race.

So `ConditionalFake` below actually evaluates the two condition expressions this
module uses. A fake that accepted every write would let a read-then-write claim
pass every test in this file and execute every run twice in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentorg.queue import _dynamo as q


class ConditionalCheckFailedException(Exception):
    """Named to match botocore's runtime-generated class.

    `_dynamo.claim` matches on the class NAME rather than importing it, because
    that exception is generated per-client at runtime and has no stable symbol.
    This double therefore has to carry the same name -- and if the module ever
    switches to a real import, this test stops passing, which is the correct
    signal rather than a silent divergence.
    """


class ConditionalFake:
    """An in-memory table that ENFORCES the two conditions this module sends."""

    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["pk"], Item["sk"])
        if ConditionExpression == "attribute_not_exists(pk)" and key in self.items:
            raise ConditionalCheckFailedException("pk exists")
        self.items[key] = dict(Item)
        return {}

    def get_item(self, Key, ConsistentRead=False):
        found = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(found)} if found else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues,
                    ExpressionAttributeNames=None, ConditionExpression=None,
                    ReturnValues=None):
        key = (Key["pk"], Key["sk"])
        item = self.items.get(key)
        if item is None:
            raise ConditionalCheckFailedException("no such item")

        claim_condition = "attribute_not_exists(claimed_by) AND #s = :ready"
        already_taken = (
            "claimed_by" in item
            or item.get("status") != ExpressionAttributeValues.get(":ready")
        )
        if ConditionExpression == claim_condition and already_taken:
            raise ConditionalCheckFailedException("already claimed or not ready")

        # Crude but faithful for the two expressions this module sends: apply the
        # SET assignments, then honour a REMOVE clause.
        body = UpdateExpression
        set_part = body.split("REMOVE")[0].replace("SET", "", 1)
        for assignment in set_part.split(","):
            if "=" not in assignment:
                continue
            name, value = (part.strip() for part in assignment.split("=", 1))
            if ExpressionAttributeNames and name in ExpressionAttributeNames:
                name = ExpressionAttributeNames[name]
            item[name] = ExpressionAttributeValues[value]
        if "REMOVE" in body:
            for attribute in body.split("REMOVE")[1].split(","):
                item.pop(attribute.strip(), None)

        self.items[key] = item
        return {"Attributes": dict(item)}

    def query(self, IndexName, KeyConditionExpression, ExpressionAttributeValues,
              ScanIndexForward=True, ExclusiveStartKey=None):
        wanted = ExpressionAttributeValues[":s"]
        rows = [dict(v) for v in self.items.values() if v.get("gsi2pk") == wanted]
        rows.sort(key=lambda r: r.get("gsi2sk", ""), reverse=not ScanIndexForward)
        return {"Items": rows}


# ── the guarantee ────────────────────────────────────────────────────────────

def test_two_workers_racing_produce_exactly_one_winner():
    """THE LOAD-BEARING TEST. A read-then-write claim passes everything else.

    Both workers see a READY job. The condition is evaluated by the store as part
    of the write, so the second update fails rather than overwriting the first
    worker's claim. Written as read-then-write, both would succeed and the stage
    would run twice with nothing recording that it had.
    """
    table = ConditionalFake()
    queue = q.DynamoQueue(table)
    queue.enqueue("t1", "j-1", run_id="r-1")

    first = queue.claim("t1", "j-1", "worker-a")
    second = queue.claim("t1", "j-1", "worker-b")

    assert first is not None, "the first claim failed; nothing was tested"
    assert first["claimed_by"] == "worker-a"
    assert second is None, f"worker-b also claimed the job: {second}"
    assert table.items[("TENANT#t1", "JOB#j-1")]["claimed_by"] == "worker-a"


def test_a_losing_claim_returns_NONE_and_does_not_raise():
    """A lost race is ordinary, not exceptional -- every worker loses most races.

    Raising would make the normal case indistinguishable from a real fault in the
    worker's logs.
    """
    table = ConditionalFake()
    queue = q.DynamoQueue(table)
    queue.enqueue("t1", "j-1")
    queue.claim("t1", "j-1", "worker-a")

    assert queue.claim("t1", "j-1", "worker-b") is None


def test_enqueue_REFUSES_to_overwrite_a_live_job():
    """PutItem replaces at a (pk, sk) pair rather than failing, so without the
    condition a re-enqueue would reset a claimed job to ready and lose the claim."""
    table = ConditionalFake()
    queue = q.DynamoQueue(table)
    queue.enqueue("t1", "j-1")
    queue.claim("t1", "j-1", "worker-a")

    with pytest.raises(ConditionalCheckFailedException):
        queue.enqueue("t1", "j-1")

    assert table.items[("TENANT#t1", "JOB#j-1")]["claimed_by"] == "worker-a"


# ── the pause, which is why SQL was chosen over SQS ──────────────────────────

def test_a_paused_job_has_NO_expiry_and_is_not_claimable():
    """The property SQS could not provide: a gate awaiting a human waits
    indefinitely, and nothing makes the job claimable again on a timer."""
    table = ConditionalFake()
    queue = q.DynamoQueue(table)
    queue.enqueue("t1", "j-1")
    queue.claim("t1", "j-1", "worker-a")

    paused = queue.pause("t1", "j-1", "gate2")

    assert paused["status"] == q.STATUS_PAUSED
    assert paused["awaiting_gate"] == "gate2"
    assert "lease_until" not in paused, "a paused job kept a lease that can expire"
    assert "claimed_by" not in paused, "a paused job stayed claimed"
    # And it is NOT claimable, because the condition requires status == ready.
    assert queue.claim("t1", "j-1", "worker-b") is None


def test_awaiting_finds_paused_jobs_across_tenants():
    """A service-role read: 'which runs wait for a human' is not a question one
    tenant can answer."""
    table = ConditionalFake()
    queue = q.DynamoQueue(table)
    for tenant in ("t1", "t2"):
        queue.enqueue(tenant, f"j-{tenant}")
        queue.claim(tenant, f"j-{tenant}", "w")
        queue.pause(tenant, f"j-{tenant}", "gate1")

    waiting = queue.awaiting()

    assert {row["tenant_id"] for row in waiting} == {"t1", "t2"}, waiting


def test_ready_is_FIFO_by_CREATION_time():
    """`gsi2sk` is the creation time and never the update time.

    Sorted by update time a job that heartbeats keeps moving to the back, and one
    that has been ready for hours starves behind a busy run -- fair-looking, and
    not FIFO.
    """
    table = ConditionalFake()
    queue = q.DynamoQueue(table)
    queue.enqueue("t1", "old", created_at="2026-01-01T00:00:00+00:00")
    queue.enqueue("t1", "new", created_at="2026-06-01T00:00:00+00:00")

    assert [r["job_id"] for r in queue.ready()] == ["old", "new"]


def test_a_claimed_job_leaves_the_ready_index():
    """`gsi2pk` moves with the status, so a claimed job is not offered again."""
    table = ConditionalFake()
    queue = q.DynamoQueue(table)
    queue.enqueue("t1", "j-1")
    assert len(queue.ready()) == 1

    queue.claim("t1", "j-1", "worker-a")

    assert queue.ready() == [], "a claimed job is still advertised as ready"
