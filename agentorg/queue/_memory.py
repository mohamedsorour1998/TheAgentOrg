"""The in-process queue backend. THE DEFAULT, and what keeps the suite hermetic.

OWNER: Lane A, task A2.

WHY THE DEFAULT IS IN-PROCESS
=============================
`tests/conftest.py` carries six autouse guards that slam every external seam shut:
no test reaches Bedrock, the GitHub API, the terminal, the working tree's `runs/`,
the scanner cache or a real `git clone`. The whole suite runs with no AWS, no
GitHub and no network -- which is what makes it usable as a gate, and it is the
property four rules in CLAUDE.md exist to protect.

A queue whose default needed a Postgres server would end that in one commit. Every
test that drove a pipeline would need infrastructure, CI would need a service
container, and the failure when it was absent would be a connection error in a
test about something else. So the default backend is a dict, and the durable one
is opted into by setting a knob -- exactly the shape `STATE_BACKEND` already uses,
where `local` is the default and `dynamodb` is chosen.

Note which direction that runs. This is NOT a test double: it is a real backend
with real semantics that the deployed system could use for a single-worker
deployment. That distinction matters because a test double cannot be trusted to
express a failing case, and CLAUDE.md records NINE instances of exactly that
producing confidence that could not be falsified. `MemoryQueue` and the SQL
backend implement the same operations with the same refusals, and
`tests/test_queue.py` runs the same assertions against both.

WHAT IT DELIBERATELY CANNOT EXPRESS, stated rather than left implicit -- the same
disclosure `tests/test_state_backend.py` makes about its `_FakeTable`:

  * TRUE CONCURRENCY. There is a `threading.Lock`, and it makes `claim`
    thread-safe within one process, which is a real property and is tested. It is
    NOT the same as two OS processes contending, where the SQL backend's
    `SELECT ... FOR UPDATE SKIP LOCKED` is what does the work. A test asserting
    single-claim semantics here proves the invariant is expressible; it does not
    prove the SQL is right, which is why the same test runs against both.
  * SURVIVING A RESTART. It cannot, by construction -- the dict dies with the
    process. That is why `config.QUEUE_BACKEND`'s validation message calls out an
    in-memory queue losing a paused run, and why A4's restart test uses the SQL
    backend on a real file. A restart test written against this backend would pass
    trivially and prove the opposite of what it claimed.
  * A LEASE EXPIRING IN REAL TIME. `_now` is imported from the package so a test
    can move the clock rather than sleep. Wall-clock waiting in a test suite is
    how a 150-second suite becomes a 400-second one.
"""

from __future__ import annotations

import threading

from . import (
    APPROVING_DECISIONS,
    REJECTION_STAGES,
    TERMINAL_STATUSES,
    Job,
    JobStatus,
    _lease_until,
    _now,
)


class MemoryQueue:
    """Jobs in a dict, keyed by job_id. One process, one queue.

    A DICT KEYED ON job_id, and insertion order carries the FIFO ordering, which
    dicts have guaranteed since 3.7. A list would make `get` a scan and `claim` a
    scan, and the dict gives both for free -- but the ordering is load-bearing and
    so it is stated here rather than left as an implementation detail a tidy-up
    could break.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        # Guards every mutation. Not for the suite's benefit -- the suite is
        # single-threaded -- but because `scripts/worker.py` can be run more than
        # once in one process (the end-to-end test does exactly that), and a
        # check-then-set on `status` across two threads is the classic
        # double-claim. The invariant it protects is the one thing this whole
        # package is careful about: an agent invocation is not idempotent.
        self._lock = threading.RLock()

    # ── writes ────────────────────────────────────────────────────────────────

    def enqueue(self, job: Job) -> Job:
        """Store a job. Refuses a duplicate (run_id, stage, attempt).

        THE REFUSAL IS A6. Two rows for one (run_id, stage) means two workers each
        claim one, each runs the stage exactly once and correctly, and the agent
        behind it is invoked twice -- a PR comment posted twice, a model bill paid
        twice. `agent_client.py:107-110` disables botocore's retries for precisely
        this reason and says so. The queue is the layer above that seam and has
        the same obligation.

        It RAISES rather than returning the existing job. A caller enqueuing the
        same triple twice has a bug, and handing back the first job would let the
        bug ship: the caller would go on believing it had scheduled work it had
        not. `attempt` is the escape hatch -- a deliberate retry is a different
        job, with its own row.
        """
        with self._lock:
            key = (job.run_id, job.stage, job.attempt)
            for existing in self._jobs.values():
                if (existing.run_id, existing.stage, existing.attempt) == key:
                    raise ValueError(
                        f"job for run {job.run_id!r} stage {job.stage!r} attempt "
                        f"{job.attempt} is already queued as {existing.job_id} "
                        f"(status {existing.status!r}). Refused rather than "
                        f"stored: two rows for one stage means two workers each "
                        f"invoke the agent once, which posts the PR comment twice "
                        f"and pays the model bill twice. A deliberate retry uses "
                        f"a higher `attempt`."
                    )
            self._jobs[job.job_id] = job
            return job.model_copy()

    def claim(self, worker: str, *, lease_seconds: int) -> Job | None:
        """The oldest claimable job, leased to `worker`. None if there is none.

        CLAIMABLE MEANS, in order:

          * status `ready` -- nobody holds it; or
          * status `claimed` with an EXPIRED lease -- its worker is presumed dead,
            and this is A8. Reclaiming records `reclaimed_from`, which is the only
            trace anywhere that a stage may have run twice.

        `paused` IS NOT CLAIMABLE, and that single line is what keeps the three
        human gates standing after the seven jobs collapse. There is no lease
        timeout on a pause, no sweeper that ages one out, and no `force` argument.
        The only exit is `resume`, which cannot be called without a decision.

        Terminal statuses are not claimable either, which needs no defending, but
        note WHY it is checked rather than assumed: `complete` sets a terminal
        status and a later `claim` in the same process reads the same dict, so
        without the check a finished job would be handed out again the moment its
        lease field went stale.

        AT-LEAST-ONCE, NOT EXACTLY-ONCE, AND THIS IS THE HONEST STATEMENT OF IT.
        Reclaiming an expired lease can hand a worker a job whose previous owner
        is alive but wedged -- a hung network call, a stopped process. That worker
        would then run a stage a second time. The queue CANNOT rule this out; no
        queue can, without a fencing token the work itself honours. What it can do
        is refuse to be silent about it, which is what `reclaimed_from` is for:
        `scripts/worker.py` reads that field and REFUSES to run a reclaimed job's
        stage blind. See its `_already_ran` check. Calling this exactly-once here
        would be the false confidence CLAUDE.md's nine-instance pattern describes.
        """
        with self._lock:
            now = _now()
            for job in self._jobs.values():
                if job.status == "ready":
                    return self._lease(job, worker, lease_seconds, reclaimed="")
                if job.status == "claimed" and job.lease_expires_at <= now:
                    # The previous owner is presumed dead. Recorded, not erased.
                    return self._lease(job, worker, lease_seconds,
                                       reclaimed=job.claimed_by)
            return None

    def _lease(self, job: Job, worker: str, lease_seconds: int,
               *, reclaimed: str) -> Job:
        """Mark one job claimed. Called under the lock, never outside it."""
        job.status = "claimed"
        job.claimed_by = worker
        job.lease_expires_at = _lease_until(lease_seconds)
        job.updated_at = _now()
        if reclaimed:
            job.reclaimed_from = reclaimed
        return job.model_copy()

    def heartbeat(self, job_id: str, *, lease_seconds: int) -> Job:
        """Extend a claim. Refuses a job that is not currently claimed.

        A heartbeat on a `paused` or terminal job is a worker that has lost track
        of what it holds, and extending a lease on a job somebody else now owns
        would be worse than the confusion: it would silently take it back.
        """
        with self._lock:
            job = self._require(job_id)
            if job.status != "claimed":
                raise ValueError(
                    f"job {job_id} is {job.status!r}, not 'claimed', so there is "
                    f"no lease to renew. A heartbeat here would either revive a "
                    f"finished job or take a paused one out of a human's hands."
                )
            job.lease_expires_at = _lease_until(lease_seconds)
            job.updated_at = _now()
            return job.model_copy()

    def complete(self, job_id: str, *, status: JobStatus, exit_code: int) -> Job:
        """Record a job's ending. Refuses to overwrite one that already ended.

        THE REFUSAL IS THE SAME ONE `run_stage._stage_gate_rejected` MAKES, one
        layer down. Measured on run 32509257195, a rejection recorder overwrote a
        poisoned run's `status=blocked` with `status=rejected` and attributed it to
        a human who never saw the gate -- the block, the one thing that demo beat
        exists to show, erased by the job written to preserve refusals. A queue
        that let a second `complete` land on a finished job would reopen that
        exact hole at the job level, so it does not.
        """
        with self._lock:
            job = self._require(job_id)
            if job.status in TERMINAL_STATUSES:
                raise ValueError(
                    f"job {job_id} already ended as {job.status!r} with exit "
                    f"{job.exit_code}; refusing to record {status!r}. Overwriting "
                    f"an ending is how a block becomes a rejection attributed to "
                    f"a human who never saw the gate -- measured on run "
                    f"32509257195."
                )
            job.status = status
            job.exit_code = exit_code
            job.claimed_by = ""
            job.lease_expires_at = ""
            job.updated_at = _now()
            return job.model_copy()

    def fail(self, job_id: str, *, exit_code: int) -> Job:
        """Record a crash. Deliberately not a re-enqueue -- see `queue.fail`."""
        return self.complete(job_id, status="failed", exit_code=exit_code)

    def pause(self, job_id: str, *, gate: str) -> Job:
        """Hold a job at a gate. It stops being claimable until `resume`."""
        with self._lock:
            job = self._require(job_id)
            if job.status in TERMINAL_STATUSES:
                raise ValueError(
                    f"job {job_id} already ended as {job.status!r}; a pause here "
                    f"would put a finished run back on the approval screen asking "
                    f"a human to decide something already decided."
                )
            job.status = "paused"
            job.awaiting_gate = gate
            # The lease is dropped: nobody holds a paused job, and a lingering
            # lease would make it look reclaimable to a `claim` that skipped the
            # status check.
            job.claimed_by = ""
            job.lease_expires_at = ""
            job.updated_at = _now()
            return job.model_copy()

    def resume(self, run_id: str, *, gate: str, decision: str,
               approver: str = "", reason: str = "") -> Job:
        """Release the run's paused job at `gate`. The only exit from `paused`.

        An APPROVAL leaves the stage alone: the gate stage runs and records the
        approval, as `gate1` does on Actions today.

        A REFUSAL REPOINTS THE JOB AT THE GATE'S RECORDER STAGE. `gate1` becomes
        `gate1-rejected`, and the reason is `run_stage._stage_gate`'s: that
        function hardcodes `decision="approved"` and cannot record a refusal,
        because on Actions a rejected Environment skips the gate job entirely, so
        nothing inside it ever executes. Three separate recorder JOBS exist there
        for that reason, and each has to GUESS from `needs.<stage>.result` whether
        a human refused -- a guess that, measured on run 32575709109, posted
        `REJECTED by mohamedsorour1998` for a run that was merely cancelled.

        Here the decision is an ARGUMENT. There is nothing to infer, and the
        recorder runs because a human refused rather than because a job did not
        run. That is the single largest correctness gain in this lane.

        `approver` and `reason` are recorded on the JOB, not written into the run's
        state. The gate stage is the one writer -- see `queue.resume`.
        """
        with self._lock:
            for job in self._jobs.values():
                if (job.run_id == run_id and job.status == "paused"
                        and job.awaiting_gate == gate):
                    if decision not in APPROVING_DECISIONS:
                        job.stage = REJECTION_STAGES[gate]
                    job.status = "ready"
                    job.decided_by = approver
                    job.decision_reason = reason
                    job.updated_at = _now()
                    return job.model_copy()
            raise LookupError(
                f"run {run_id!r} has no job paused at {gate!r}. Raised rather "
                f"than ignored: a resume that silently did nothing would leave a "
                f"human believing they had released a run, with the run still "
                f"waiting and nothing anywhere saying so."
            )

    # ── reads ─────────────────────────────────────────────────────────────────

    def adopt_run_id(self, job_id: str, run_id: str) -> Job:
        """Rename one job's run id in place. See `queue.adopt_run_id`.

        The identity index is (run_id, stage, attempt), so this MOVES the job to a
        new identity -- and a collision there means something else already claims
        to be this run's `plan`. Checked rather than allowed, because the in-memory
        backend has no UNIQUE index to catch it the way the SQL one does, and the
        two must refuse the same things.
        """
        with self._lock:
            job = self._require(job_id)
            key = (run_id, job.stage, job.attempt)
            for other in self._jobs.values():
                if other.job_id != job_id and (
                        other.run_id, other.stage, other.attempt) == key:
                    raise ValueError(
                        f"cannot adopt run id onto job {job_id}: {other.job_id} "
                        f"already holds ({run_id}, {job.stage}, {job.attempt}). "
                        f"Two rows for one stage means the agent runs twice."
                    )
            job.run_id = run_id
            job.updated_at = _now()
            return job.model_copy()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy() if job else None

    def jobs_for_run(self, run_id: str) -> list[Job]:
        with self._lock:
            return [job.model_copy() for job in self._jobs.values()
                    if job.run_id == run_id]

    def awaiting(self) -> list[Job]:
        with self._lock:
            return [job.model_copy() for job in self._jobs.values()
                    if job.status == "paused"]

    def _require(self, job_id: str) -> Job:
        """The stored job, or a refusal naming the id.

        Returns the LIVE object, not a copy, because every caller is under the
        lock and about to mutate it. The public methods copy on the way out --
        handing out the live object is how a caller's `job.status = ...` silently
        becomes a write to the queue.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise LookupError(
                f"no queued job {job_id!r}. Raised rather than returning None: "
                f"every caller of this is about to record an outcome, and an "
                f"outcome recorded against no job is an outcome nobody can read."
            )
        return job
