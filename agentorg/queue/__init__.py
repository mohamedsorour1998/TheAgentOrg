"""The job queue — what GitHub Actions provides today, replaced deliberately.

OWNER: Lane A. Spec `docs/final/01-specification.md` §12.

Actions gives this pipeline four things, and this package replaces each one:

    sequencing            `needs:` between seven jobs  ->  the stage chain below
    artifact handoff      upload/download-artifact     ->  `gates.save`/`gates.load`
    pausing for approval  a GitHub Environment          ->  a durable `paused` row
    per-job isolation     a fresh runner per job        ->  a subprocess per stage
                                                            (see queue/runner.py)

THE PAUSE IS THE ONE THAT CHANGES THE SHAPE OF THE SYSTEM
========================================================
The seven-job pipeline exists entirely because "a GitHub Environment pauses a JOB,
and a job cannot pause in its middle" -- `scripts/run_stage.py:9-22` and CLAUDE.md
both say so, and it is true. Every other structural decision in the cloud path
follows from it: `develop` carries four unrelated things because none of them is a
gate boundary; the revision loop cannot be split because Actions has no "repeat
until"; and three separate rejection-recorder JOBS exist because a rejected
Environment skips its job rather than running it with a verdict.

A queue with durable state removes the constraint. A run pauses because a ROW says
it is paused, not because a process is being held open, so the pause costs nothing
and can happen anywhere. **The seven jobs may therefore collapse. THE THREE HUMAN
GATES MUST NOT**, and this package is built so that they cannot: a gate is a job
whose status is `paused`, it is the only status `claim` will not hand to a worker,
and the only way out of it is `resume` with a `HumanDecision`. There is no code
path from `paused` to `ready` that does not carry a decision.

WHAT A JOB IS
=============
One (run_id, stage, attempt). Nothing finer: the stage is already the unit the
pipeline is cut into, `scripts/run_stage.py` already runs exactly one, and its
exit codes are already the vocabulary for "what happened". Making a job anything
else would mean re-deriving those codes, and `A7`'s whole point is that they must
not be re-derived -- see `queue/runner.py`.

WHY THE STAGE CHAIN IS DECLARED HERE AND NOT IMPORTED
=====================================================
`scripts/run_stage.py` holds `STAGE_CHAIN`, and the honest options were to import
it or to restate it. Importing means `agentorg/` reaching into `scripts/`, which
is backwards -- and `scripts/run_stage.py` is scheduled for DELETION in Phase 3 of
the plan, so an import would make this package depend on a file that is going
away. So it is restated, and the drift that restating invites is closed by a test
rather than by a promise: `tests/test_queue.py` loads `run_stage.py` and asserts
the two agree, name by name, and asserts its own matcher found something first.
When that file is deleted the test fails and tells the integrator, which is the
correct time to hear about it.

THE BACKENDS
============
`config.QUEUE_BACKEND` chooses, and it is validated at import (config.py:242-253)
so a typo cannot select the in-memory queue in production.

    memory      queue/_memory.py   in-process. THE DEFAULT, and it is what keeps
                                   the suite hermetic -- see that module.
    postgres    queue/_sql.py      durable. The ADR is that module's docstring.
    sqs         --                 NOT IMPLEMENTED, and it RAISES rather than
                                   falling through to memory. See `_backend`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .. import log
from ..common import config

# ── the vocabulary ────────────────────────────────────────────────────────────
#
# A job's status. Seven values, and the four terminal ones are DELIBERATELY NOT
# collapsed into one `done`, for exactly the reason `run_stage.py:139-178` gives
# about its exit codes: "the run was blocked", "a human refused it", "a recorder
# was asked to overwrite a finished run" and "this crashed" are four different
# facts, and the demo's whole point is that the first is a WORKING pipeline
# reporting a real verdict. A queue that recorded all four as `failed` would put
# the poisoned demo run and a broken worker in the same bucket on the one surface
# an operator reads.
#
#   ready         claimable now
#   claimed       a worker holds a lease on it
#   paused        A HUMAN GATE. The only status `claim` refuses. Durable.
#   done          the stage completed and the run advances
#   blocked       the deterministic block rule stopped the run -- exit 3
#   rejected      a human refused, or a cap was hit -- exit 4
#   already_final a stage declined to overwrite a run that had ended -- exit 5
#   failed        the stage crashed -- exit 1, or any code with no meaning
JobStatus = Literal[
    "ready", "claimed", "paused", "done", "blocked", "rejected",
    "already_final", "failed",
]

# The statuses a job cannot leave. `claim` refuses these, and so does `complete`.
TERMINAL_STATUSES = frozenset(
    {"done", "blocked", "rejected", "already_final", "failed"}
)

# THE STAGE CHAIN. The stages that ADVANCE a run, in order -- one job each. Same
# list as `scripts/run_stage.py:STAGE_CHAIN`, restated for the reason this
# module's docstring gives and cross-checked by a test rather than trusted.
STAGE_CHAIN = ["plan", "gate1", "develop", "gate2", "sre", "gate3", "promote"]

# The three human gates. A job for one of these is the only kind that is ever
# `paused`, and `resume` is the only way out.
#
# DERIVED from STAGE_CHAIN's membership in the recorder map below rather than
# written out a fourth time in this repository, so a gate added to the chain
# cannot be left without a pause.
REJECTION_STAGES = {
    "gate1": "gate1-rejected",
    "gate2": "gate2-rejected",
    "gate3": "gate3-rejected",
}
GATES = tuple(stage for stage in STAGE_CHAIN if stage in REJECTION_STAGES)

# The decisions `resume` accepts, and which of them let a run through. Same split
# as `graph._APPROVING_DECISIONS`, and `overridden` is APPROVING here for the same
# reason it is there: it is the one capability a human is meant to keep, and a
# queue that refused it would delete the documented override route while looking
# like it had tightened something.
APPROVING_DECISIONS = frozenset({"approved", "overridden"})
REFUSING_DECISIONS = frozenset({"rejected"})

# How long a claim is good for, in seconds. A worker that has not renewed by then
# is presumed dead and its job becomes claimable again -- which is A8, crash
# recovery, and the property Actions gave us for free by killing the runner.
#
# 600s, chosen against what one stage actually costs: `develop` runs the
# developer/reviewer loop plus the security scan, and `agent_client`'s data-plane
# read timeout is 300s PER INVOCATION (agent_client.py:114) with up to four
# revision passes behind it. A lease shorter than the work it covers is worse than
# no lease at all: it hands the same job to a second worker while the first is
# still running it, which is the double-invocation this whole file is careful
# about. Renewal exists (`heartbeat`) so a legitimately slower stage need not
# raise this number.
DEFAULT_LEASE_SECONDS = 600


def _now() -> str:
    """UTC, ISO-8601. A module function so tests can move the clock.

    Every lease comparison in this package is a STRING comparison of these
    values, which is sound because ISO-8601 UTC sorts lexicographically -- and
    it is what keeps the two backends' comparisons identical, since a TEXT column
    compares the same way in SQL as in Python.
    """
    return datetime.now(UTC).isoformat()


def _lease_until(seconds: int) -> str:
    from datetime import timedelta

    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


class Job(BaseModel):
    """One unit of queued work: run this stage of this run, once.

    A pydantic model rather than a dataclass, for the reason the rest of this
    repository uses pydantic: the durable backend stores it and reads it back, so
    a field that changed shape has to fail at the boundary rather than several
    reads later. It is NOT part of the frozen contract in `state.py` -- that file
    describes a RUN, this one describes a unit of WORK, and the two have different
    lifetimes.
    """

    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    stage: str
    # WHICH ATTEMPT THIS IS, and it is part of the idempotency key rather than a
    # counter for reporting. See `enqueue` and `claim`.
    attempt: int = 1
    # Carried, stored and handed back; NOT used for scoping. Tenant scoping is
    # Lane B's, and a filter written here would be a second, weaker copy of it.
    # The field exists so Lane B does not have to edit this package to add one.
    tenant_id: str = ""

    # ── THE RUN'S INPUTS, ON THE ROW. MEASURED DEFECT; see `enqueue`. ─────────
    #
    # These three were originally arguments to `scripts/worker.py`'s process and
    # nothing else, which worked for exactly as long as one process ran the whole
    # pipeline. MEASURED with two processes -- `start_run` in one, a bare worker in
    # the next, which is what a restart looks like:
    #
    #     run_stage: error: plan needs --ticket-id and --ticket-text
    #     [worker] the plan stage printed no run_id
    #
    # The ticket text existed only in the argv of a process that had exited. This
    # is the same structural fix `RunState.poisoned`, `ci_status_measured` and
    # `model_provenance` all are: a value chosen in one process and needed in
    # another has to travel as a FIELD. On Actions it never came up, because
    # `run-pipeline.yml` interpolates one workflow-level input into all seven jobs.
    # A queue has the second invocation by design.
    ticket_id: str = ""
    ticket_text: str = ""
    trigger: str = "manual"
    # THE POISONED CHOICE, and it is authoritative for `plan` ONLY. Every later
    # stage reads `RunState.poisoned`, which `plan` wrote -- see
    # `scripts/worker.py:_poisoned_for` for the run where reading argv instead sent
    # a poisoned ticket to gate2 with a `pass` verdict.
    poisoned: bool = False

    status: JobStatus = "ready"
    claimed_by: str = ""
    lease_expires_at: str = ""
    # The gate this job is waiting at, set when status == "paused". Named rather
    # than inferred from `stage`, because a future non-gate pause (a budget hold,
    # a quota wait) must not read as a human gate.
    awaiting_gate: str = ""
    # WHO DECIDED AT THE GATE, and why. Written by `resume`, read by the worker,
    # handed to the gate stage as `--approver`.
    #
    # These are on the row for the same reason `ticket_text` is: the person clicks
    # in one process and the stage that records their name runs in another, minutes
    # later. Without them every queued approval would reach `_stage_gate`'s default
    # and be recorded as `github-environment-reviewer` -- naming a GitHub
    # Environment that never held this job, on the one field whose whole purpose is
    # attributing a decision to a human.
    decided_by: str = ""
    decision_reason: str = ""
    # The stage process's exit code, once it has run. `None` means it has not.
    exit_code: int | None = None
    # The worker whose lease expired, when this job was reclaimed. Empty
    # otherwise. THIS IS THE ONLY RECORD THAT A STAGE MAY HAVE RUN TWICE -- see
    # `claim`'s note on at-least-once delivery.
    reclaimed_from: str = ""

    enqueued_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


def next_stage(stage: str) -> str:
    """The stage that follows `stage`, or `""` if it is the last one.

    The whole of the queue's sequencing, in four lines. `needs:` in
    `run-pipeline.yml` expressed the same thing across seven job declarations.
    """
    if stage not in STAGE_CHAIN:
        raise ValueError(
            f"{stage!r} is not a stage that advances a run; the chain is "
            f"{' -> '.join(STAGE_CHAIN)}. A rejection recorder "
            f"({', '.join(sorted(REJECTION_STAGES.values()))}) is deliberately "
            f"not in it: it records an ending rather than advancing past one."
        )
    index = STAGE_CHAIN.index(stage)
    if index + 1 >= len(STAGE_CHAIN):
        return ""
    return STAGE_CHAIN[index + 1]


class QueueBackend(Protocol):
    """What a backend must do. Two implementations, one interface.

    Every method takes and returns `Job` values; nothing leaks a cursor, a
    message handle or a path. That is what lets `scripts/worker.py` be written
    once against both, and it is why `sqs` RAISES rather than being half-present:
    a backend that satisfied part of this and silently no-opped the rest would
    make "the run paused" and "the pause was dropped" the same observation.
    """

    def enqueue(self, job: Job) -> Job: ...
    def claim(self, worker: str, *, lease_seconds: int = ...) -> Job | None: ...
    def heartbeat(self, job_id: str, *, lease_seconds: int = ...) -> Job: ...
    def complete(self, job_id: str, *, status: JobStatus, exit_code: int) -> Job: ...
    def fail(self, job_id: str, *, exit_code: int) -> Job: ...
    def pause(self, job_id: str, *, gate: str) -> Job: ...
    def resume(self, run_id: str, *, gate: str, decision: str,
               approver: str = ..., reason: str = ...) -> Job: ...
    def adopt_run_id(self, job_id: str, run_id: str) -> Job: ...
    def get(self, job_id: str) -> Job | None: ...
    def jobs_for_run(self, run_id: str) -> list[Job]: ...
    def awaiting(self) -> list[Job]: ...


_BACKENDS: dict[str, QueueBackend] = {}


def _backend() -> QueueBackend:
    """The configured backend, built once.

    `config.QUEUE_BACKEND` is read THROUGH THE MODULE at call time, never bound
    at import -- config.py's own note about `SCANNERS_REQUIRED` explains why: a
    `from ..common.config import QUEUE_BACKEND` here would fix the value before
    any test fixture ran, so the knob would silently ignore both the suite and
    the deployed environment.

    `sqs` RAISES. It is a validated value of the knob (config.py:239) because the
    contract batch landed all three names at once, and this package implements
    two of them. A backend that fell back to `memory` would lose a paused run on
    the next worker restart while every surface reported success, which is the
    defect this whole repository is organised around. NotImplementedError names
    the missing thing instead.
    """
    name = config.QUEUE_BACKEND
    if name in _BACKENDS:
        return _BACKENDS[name]

    if name == config.QUEUE_BACKEND_MEMORY:
        from ._memory import MemoryQueue

        _BACKENDS[name] = MemoryQueue()
    elif name == config.QUEUE_BACKEND_POSTGRES:
        from ._sql import postgres_queue

        _BACKENDS[name] = postgres_queue()
    else:
        raise NotImplementedError(
            f"QUEUE_BACKEND={name!r} is a recognised value that this package does "
            f"not implement. It is NOT silently downgraded to "
            f"{config.QUEUE_BACKEND_MEMORY!r}: an in-process queue loses every "
            f"paused run when the worker restarts, and a run that vanished while "
            f"the worker reported healthy is the failure shape this project "
            f"exists to make impossible. Implemented backends: "
            f"{config.QUEUE_BACKEND_MEMORY!r}, {config.QUEUE_BACKEND_POSTGRES!r}."
        )
    return _BACKENDS[name]


def reset() -> None:
    """Drop every built backend. For tests, and for tests only.

    The in-process backend is module state, so without this a job enqueued by one
    test is claimable by the next -- which is `conftest.py`'s seam-5 story exactly
    (a stale cache hit looks precisely like a fresh answer). `tests/test_queue.py`
    calls this on BOTH sides of every test.

    NOTE FOR THE INTEGRATOR: that fixture is file-scoped because Lane A owns
    `tests/test_queue.py` and not `tests/conftest.py`. The moment a second lane
    enqueues anything, this belongs in `conftest.py` as an autouse fixture, for
    the reason the scanner-cache fixture's docstring gives at length: a
    file-scoped version of exactly this guard predicted its own gap, and a second
    lane then walked into it.
    """
    _BACKENDS.clear()


# ── the six operations, plus the three reads ──────────────────────────────────
#
# Module-level functions rather than a class the caller instantiates, matching
# `gates.py` and `log.py`: the backend is a deployment fact, not a per-caller
# choice, and a caller that could pick one could pick differently from the worker.


def enqueue(run_id: str, stage: str, *, attempt: int = 1, tenant_id: str = "",
            status: JobStatus = "ready", awaiting_gate: str = "",
            ticket_id: str = "", ticket_text: str = "", trigger: str = "manual",
            poisoned: bool = False) -> Job:
    """Put one stage of one run on the queue.

    THE RUN ID IS VALIDATED HERE, through `log.is_safe_run_id`, and this is a new
    place that mattered. A run id is already one path component and one partition
    key (log.py:91-100); with a queue it becomes a third thing -- a row a worker
    will later hand to a subprocess as `--run-id`. `../../etc/passwd` reaching
    that far is a state file written outside `runs/`, so it is refused at the
    boundary rather than deeper in.

    IDEMPOTENT ON (run_id, stage, attempt), and that is A6's first half. Enqueuing
    the same triple twice does not make two jobs -- it raises. Two jobs for one
    (run_id, stage) would be two workers each running the stage once, each
    correctly, and the pipeline would invoke an agent twice: a PR comment posted
    twice and a model bill paid twice, which is precisely what
    `agent_client.py:107-110` disables botocore's retries to avoid. The refusal is
    loud because a duplicate enqueue is a caller bug, and a queue that quietly
    deduplicated would hide it.

    THE RUN'S INPUTS TRAVEL ON THE ROW, not in the calling process's argv, and
    that was a measured defect rather than a design flourish -- see `Job`'s note.
    A worker that claims a `plan` job it did not enqueue has to be able to run it,
    and the only place the ticket text can come from is the row.
    """
    if not log.is_safe_run_id(run_id):
        raise ValueError(
            f"unsafe run id (length {len(run_id)}): a queued job's run id "
            f"becomes a path component, a partition key AND a `--run-id` "
            f"argument to a subprocess, so it is validated here as well as in "
            f"log.py. The value is not echoed: it is untrusted input and this "
            f"message can reach a rendered page."
        )
    if stage not in STAGE_CHAIN and stage not in REJECTION_STAGES.values():
        raise ValueError(
            f"{stage!r} is not a stage. Valid: {', '.join(STAGE_CHAIN)}, "
            f"{', '.join(sorted(REJECTION_STAGES.values()))}. Refused rather "
            f"than enqueued, because a worker would hand this straight to "
            f"`run_stage.py`, whose argparse `choices` would reject it -- one "
            f"crashed job per typo instead of one refused enqueue."
        )
    job = Job(run_id=run_id, stage=stage, attempt=attempt, tenant_id=tenant_id,
              status=status, awaiting_gate=awaiting_gate, ticket_id=ticket_id,
              ticket_text=ticket_text, trigger=trigger, poisoned=poisoned)
    return _backend().enqueue(job)


def claim(worker: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Job | None:
    """Take the next claimable job, or None. See `_memory.MemoryQueue.claim`.

    `worker` identifies the claimant and is recorded on the job. It is not
    optional and has no default: an unattributed claim cannot be told apart from a
    reclaim of a dead worker, which is the one distinction A8 rests on.
    """
    if not worker:
        raise ValueError(
            "claim needs a worker identity: it is what distinguishes 'this job "
            "is mine' from 'this job's previous owner is gone', and an empty "
            "one makes a reclaim unattributable."
        )
    return _backend().claim(worker, lease_seconds=lease_seconds)


def heartbeat(job_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Job:
    """Renew a claim. A long stage is not a dead worker."""
    return _backend().heartbeat(job_id, lease_seconds=lease_seconds)


def complete(job_id: str, *, status: JobStatus, exit_code: int) -> Job:
    """Record how a job ended. `status` is the queue's word, `exit_code` the process's.

    BOTH are stored, deliberately, and neither is derived from the other at read
    time. `queue/runner.py` maps one to the other in exactly one place; keeping
    the raw code means a code that mapping does not recognise is still on the
    record, rather than being flattened into `failed` and losing the evidence.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(
            f"complete() records an ENDING, and {status!r} is not one. Terminal: "
            f"{', '.join(sorted(TERMINAL_STATUSES))}. A job that advances is "
            f"completed as `done` and its successor is enqueued separately -- "
            f"see scripts/worker.py."
        )
    return _backend().complete(job_id, status=status, exit_code=exit_code)


def fail(job_id: str, *, exit_code: int) -> Job:
    """Record that a job crashed. It is NOT re-enqueued, and that is the decision.

    A crashed stage may have completed half its work -- opened a PR, posted three
    comments, burned four model calls -- and nothing in its exit code says how
    much. Re-running it would repeat whatever it finished, which is the
    non-idempotency `agent_client.py:107-110` already refuses to paper over with a
    botocore retry. So a failure stops the run and waits for a human, exactly as
    a red Actions job does today.

    A human who wants it retried enqueues the same stage at a HIGHER attempt,
    which is a deliberate act with its own row, rather than something the queue
    did quietly.
    """
    return _backend().fail(job_id, exit_code=exit_code)


def pause(job_id: str, *, gate: str) -> Job:
    """Hold a job at a human gate. THE OPERATION THIS WHOLE PACKAGE EXISTS FOR.

    The job stops being claimable and stays that way across any number of worker
    restarts, because the only thing holding it is a row. That is the constraint
    Actions could not lift: an Environment pauses a JOB by keeping a runner slot
    reserved, so the pipeline had to be cut at every gate boundary.

    `gate` must be one of the three. A pause at anything else would be a job no
    `resume` can address, which is a run that stops forever while every surface
    reports it merely waiting -- the same shape as a dropped pause, and worse,
    because it looks deliberate.
    """
    if gate not in GATES:
        raise ValueError(
            f"{gate!r} is not a human gate; the three are {', '.join(GATES)}. "
            f"Refused because `resume` addresses a paused job by (run_id, gate), "
            f"so a pause at an unnameable gate is a run nothing can release."
        )
    return _backend().pause(job_id, gate=gate)


def resume(run_id: str, *, gate: str, decision: str, approver: str = "",
           reason: str = "") -> Job:
    """Release a paused run past a gate, with the decision that released it.

    THE ONLY EXIT FROM `paused`, and it cannot be taken without a decision. That
    is what keeps the three gates from collapsing along with the seven jobs: there
    is no argument to any function here that advances a paused run without saying
    who let it through and what they said.

    An APPROVAL makes the gate's own job ready, so the worker runs the gate stage
    and it records the approval -- the same stage, doing the same thing, as
    today's `gate1` job. A REFUSAL makes the job ready as the gate's RECORDER
    stage instead (`gate1` -> `gate1-rejected`), which is how the refusal reaches
    the log at all: `run_stage._stage_gate` only ever records an approval, and it
    is correct to, because on Actions a rejected Environment SKIPS the gate job
    entirely. The recorder is a separate job there for that reason. Here it is the
    same job pointed at a different stage, which is strictly better -- the
    workflow's three recorder jobs had to guess from `needs.<stage>.result`
    whether a human refused, and measured on run 32575709109 that guess named a
    person who never saw the gate. A decision handed to `resume` is not a guess.

    THIS FUNCTION DOES NOT WRITE THE `HumanDecision`. It records who decided ON THE
    JOB and lets the gate stage -- the one writer, on Actions and here -- put it in
    the run's state. `scripts/worker.py:approve` documents the measured run where
    writing at both layers recorded one click as two decisions, the second
    attributed to a reviewer who does not exist on this path.
    """
    if decision not in APPROVING_DECISIONS | REFUSING_DECISIONS:
        raise ValueError(
            f"{decision!r} is not a gate decision; expected one of "
            f"{', '.join(sorted(APPROVING_DECISIONS | REFUSING_DECISIONS))}. "
            f"Refused rather than treated as a refusal: 'anything I do not "
            f"recognise means rejected' would make a typo look like a human "
            f"decision, and 'means approved' would be worse."
        )
    if gate not in GATES:
        raise ValueError(f"{gate!r} is not a human gate; the three are {', '.join(GATES)}")
    return _backend().resume(run_id, gate=gate, decision=decision,
                             approver=approver, reason=reason)


def adopt_run_id(job_id: str, run_id: str) -> Job:
    """Record the run id `plan` generated onto the job that generated it.

    `plan` is enqueued with a PLACEHOLDER run id, because the real one does not
    exist until the stage runs -- `RunState.run_id` is a `default_factory` uuid,
    and minting one here and handing it to the stage would mean two places
    generating run ids. So the row is corrected afterwards and `jobs_for_run` finds
    it from then on.

    A NEW ROW IS NOT CREATED. Two rows for one `plan` would be two claimable jobs
    for one stage, which is the duplicate this package spends most of its care
    refusing. This is an in-place rename of one field on one row.

    IT LIVES HERE RATHER THAN IN THE WORKER, and that is a fix rather than a
    preference. `scripts/worker.py` used to do this itself, by calling
    `queue._backend()` and then either `backend._update(...)` or
    `backend._jobs[job_id].run_id = ...` depending on which backend it found --
    reaching through two layers of privates and branching on the backend's TYPE,
    which is exactly what the `QueueBackend` protocol exists to make unnecessary.
    A third backend would have silently taken neither branch.

    THE NEW ID IS VALIDATED, because it arrives by being PARSED OUT OF A
    SUBPROCESS'S STDOUT (`runner._read_line`) -- so it is the least trusted string
    in this package, and the next thing anything does with it is build a path.
    """
    if not log.is_safe_run_id(run_id):
        raise ValueError(
            f"refusing to adopt an unsafe run id (length {len(run_id)}) onto job "
            f"{job_id}: this value was parsed out of a stage's stdout and becomes "
            f"a path component and a `--run-id` argument. The value is not echoed."
        )
    return _backend().adopt_run_id(job_id, run_id)


def get(job_id: str) -> Job | None:
    """One job by id, or None."""
    return _backend().get(job_id)


def jobs_for_run(run_id: str) -> list[Job]:
    """Every job for a run, oldest first. The run's history as the queue saw it."""
    return _backend().jobs_for_run(run_id)


def awaiting() -> list[Job]:
    """Every job paused at a gate. What an approval screen lists.

    The queue's answer to the question `approve_server._awaiting` derives from
    pause markers and decisions in the log. That derivation exists because
    nothing recorded the pause directly; here a paused job IS the record, so the
    listing is a read rather than an inference. Kept as a separate function
    rather than folded into a generic filter, because "what is waiting for a
    human" is the one queue query a person types.
    """
    return _backend().awaiting()
