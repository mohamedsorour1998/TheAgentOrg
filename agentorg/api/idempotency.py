"""K7's first half: a retried submission must not start a second run.

OWNER: Lane K.

WHY THIS EXISTS AS A LAYER RATHER THAN LEANING ON THE QUEUE'S UNIQUE INDEX
=========================================================================
`queue.enqueue` is already idempotent on `(run_id, stage, attempt)` and RAISES on
a duplicate, and its docstring gives the reason this lane cares about: "two rows
for one (run_id, stage) means two workers each claim one, each runs the stage
exactly once and correctly, and the agent behind it is invoked twice -- a PR
comment posted twice, a model bill paid twice."

That is exactly the protection an HTTP submission needs, and it is NOT reachable
from HTTP on its own. MEASURED, and this is the whole justification for this file:

    queue.enqueue("api-t1-deadbeef", "plan")     -> job A
    queue.adopt_run_id(job_A, "real-run-9999")   -> plan's real run id
    queue.jobs_for_run("api-t1-deadbeef")        -> 0     <- the key is GONE
    queue.enqueue("api-t1-deadbeef", "plan")     -> ACCEPTED. A SECOND RUN.

`plan` is enqueued under a placeholder run id because the real one does not exist
until the stage runs (`RunState.run_id` is a `default_factory` uuid), and
`adopt_run_id` then RENAMES that row in place -- correctly, since two rows for one
`plan` would be the duplicate the queue spends most of its care refusing. But the
rename frees the placeholder, so a deterministic placeholder cannot carry
idempotency by itself: the UNIQUE index stops a retry that arrives before the
stage runs and admits the identical retry that arrives after. **A window that
narrow is worse than no protection, because it makes the defect intermittent.**

So the record is here, keyed on what the CLIENT said rather than on what the queue
later renamed.

WHAT THIS GUARANTEES, AND WHAT IT HONESTLY DOES NOT
===================================================
Guaranteed: two submissions carrying the same `Idempotency-Key` for the same
tenant produce ONE queued run, and the second gets the first one's ids back with
`idempotent_replay: true` on the response. A client that retries on a timeout --
which is the normal reason a CI caller retries -- does not start a second
pipeline, does not open a second PR and does not pay a second model bill.

NOT guaranteed, stated because the queue's own contract says so: this does not
make the PIPELINE exactly-once. CLAUDE.md records that the claim is
"at-least-once, not exactly-once", because a lease can expire while its worker is
alive but wedged, and `reclaimed_from` is the only trace that a stage may have run
twice. This module makes SUBMISSION idempotent. Re-execution of an already-claimed
stage is `worker._already_ran`'s problem and it reads the run's own record to
decide -- a different mechanism at a different layer, and neither substitutes for
the other.

Also not guaranteed: durability. The store is in-process, like the key store and
like `queue._memory`. A restart forgets the keys, so a retry that spans a restart
can start a second run -- which is a smaller window than the measured one above
but is a real one, and the durable version is a row beside the queue's. Named
rather than implied, because a limit recorded only in somebody's head is a limit
nobody costs.

WHY THE KEY IS SCOPED BY TENANT AND THE SCOPING IS NOT OPTIONAL
==============================================================
The record's key is `(tenant_id, idempotency_key)`. `Idempotency-Key` is a value
the CLIENT chooses, so two tenants will eventually choose the same one -- `1`,
`retry`, a fixed build number. Keyed on the string alone, tenant B's submission
would be answered with tenant A's run id, which is a cross-tenant disclosure
delivered by the deduplication layer rather than by a query. `tenancy`'s whole
premise is that every read takes a tenant with no default; this store keeps that
property at the one place a client-supplied string becomes a lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A key longer than this is refused rather than stored. It is client-supplied and
# becomes a dict key held for the process's life, so an unbounded one is memory a
# caller controls. 200 is generous for a uuid or a build id.
MAX_KEY_LENGTH = 200


@dataclass(frozen=True)
class Record:
    """What a completed submission recorded, for replaying to a retry.

    `job_id` and `run_id` are BOTH stored, and the second is the placeholder at
    the time of submission -- so a replay answers with exactly what the first
    response said, rather than with what the row says now. A replay whose body
    differed from the original response would make a retry look like a different
    run, which is the failure this module exists to prevent, in a subtler form.
    """

    job_id: str
    run_id: str
    ticket_id: str


@dataclass
class IdempotencyStore:
    """Submission records for one process. Keyed `(tenant_id, key)`.

    Mirrors `auth.InMemoryKeyStore` and `queue._memory.MemoryQueue` in shape --
    module state, an explicit setter, a `clear` for tests -- because a store a
    caller could construct per-request would deduplicate against nothing.
    """

    records: dict[tuple[str, str], Record] = field(default_factory=dict)

    def get(self, tenant_id: str, key: str) -> Record | None:
        """The record for this tenant's key, or None.

        A BLANK TENANT IS REFUSED rather than looked up. `""` is the
        single-tenant marker every pre-tenancy `RunState` carries, and a blank
        scope here would pool every un-translated caller's keys together -- so
        two tenants that both skipped the translation would deduplicate against
        each other. `tenancy.scope_for` refuses a blank for the same reason and
        this is the same refusal at the one other place a tenant id is a key.
        """
        return self.records.get(self._key(tenant_id, key))

    def put(self, tenant_id: str, key: str, record: Record) -> Record:
        """Store a record. Refuses to overwrite an existing one.

        REFUSES RATHER THAN REPLACES, and that direction is the point. An
        overwrite would mean the second submission's run id becomes the answer to
        every later retry, so the first run -- which is genuinely queued and will
        genuinely produce a PR -- becomes unreachable through the API that
        started it. `queue.complete` refuses an overwrite of a terminal status
        for a related reason, measured on run 32509257195 where a recorder erased
        a poisoned run's `status=blocked`.

        The caller checks `get` first, so reaching this with a key already
        present is a bug in the caller and is loud accordingly.
        """
        composite = self._key(tenant_id, key)
        if composite in self.records:
            existing = self.records[composite]
            raise ValueError(
                f"an idempotency record already exists for this tenant and key, "
                f"naming job {existing.job_id}. Refused rather than overwritten: "
                f"the first submission is genuinely queued, and replacing its "
                f"record would make it unreachable through the API that started "
                f"it while every retry answered with a different run."
            )
        self.records[composite] = record
        return record

    def clear(self) -> None:
        """Drop every record. For tests, and for tests only -- see queue.reset."""
        self.records.clear()

    @staticmethod
    def _key(tenant_id: str, key: str) -> tuple[str, str]:
        """The composite key, with both halves validated.

        Validated HERE rather than at each call site, because there are two call
        sites (`get` and `put`) and a check on one of them is a check that can be
        bypassed by the other.
        """
        if not tenant_id or not tenant_id.strip():
            raise ValueError(
                "tenant_id may not be blank when keying an idempotency record. "
                "\"\" is the single-tenant marker; translate it with "
                "tenancy.tenant_zero.for_run_state() first. A blank scope pools "
                "every un-translated caller's keys, so two tenants that both "
                "skipped the translation would deduplicate against each other."
            )
        if not key or not key.strip():
            raise ValueError(
                "an idempotency key may not be blank. A blank key is not 'no "
                "key' -- the caller reaches here only when a header was sent, "
                "and treating a whitespace header as absent would silently drop "
                "the protection the caller asked for."
            )
        if len(key) > MAX_KEY_LENGTH:
            raise ValueError(
                f"idempotency key is longer than {MAX_KEY_LENGTH} characters. "
                f"It is client-supplied and becomes a dict key held for the "
                f"process's life, so it is bounded. The value is not echoed: it "
                f"is untrusted input and this message can reach a rendered page."
            )
        return (tenant_id, key)


_STORE = IdempotencyStore()


def idempotency_store() -> IdempotencyStore:
    """The process's store, read through a function.

    Not the bare name, for CLAUDE.md's reason about reading knobs through the
    module: `from .idempotency import _STORE` binds the object at import and
    would not see a test's replacement.
    """
    return _STORE


def set_idempotency_store(store: IdempotencyStore) -> None:
    """Replace the process's store. The single seam a test substitutes."""
    global _STORE
    _STORE = store
