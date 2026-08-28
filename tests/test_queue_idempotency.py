"""A6 and A8: claiming twice must not run an agent twice; a killed worker recovers.

OWNER: Lane A, tasks A6 and A8.

WHY THIS IS NOT A THEORETICAL PROPERTY
======================================
`agent_client.py:107-110` disables botocore's retries and states the cost:

    An agent invocation is not idempotent: it writes a PR comment and burns model
    tokens, so a silent botocore retry of a call that actually succeeded would
    double both.

The queue is the layer above that seam and has the same obligation. A re-run
`develop` posts every develop and review comment a second time and pays for every
model call again; a re-run `promote` calls `merge_pr` on a PR that is already merged.

THE HONEST STATEMENT IS AT-LEAST-ONCE, NOT EXACTLY-ONCE
======================================================
Two workers cannot hold one job at the same time -- that is `claim`'s transaction
and the UNIQUE index. What no queue can rule out, without a fencing token the work
itself honours, is a lease that expired while its worker was ALIVE BUT WEDGED: a
hung network call, a stopped process. The reclaiming worker is then the second to
run that stage.

So the queue refuses to be SILENT about it rather than claiming it cannot happen.
`reclaimed_from` is the only trace anywhere that a stage may have run twice, and
`worker._already_ran` reads the RUN's own record before running such a job. Calling
this exactly-once would be the false confidence CLAUDE.md's nine-instance pattern
describes.
"""

import pathlib
import sys

import pytest

from agentorg import queue

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import worker  # the module under test for `_already_ran` and `_poisoned_for`

assert queue.TERMINAL_STATUSES, "no terminal statuses; these tests would pin nothing"
assert worker._STAGE_EVIDENCE, (
    "worker._STAGE_EVIDENCE is empty; every _already_ran assertion below would pin "
    "nothing -- the operational form of CLAUDE.md's 'a matcher that can match "
    "nothing must assert that it matched'"
)


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, tmp_path, monkeypatch):
    queue.reset()
    if request.param == "memory":
        monkeypatch.setattr(queue.config, "QUEUE_BACKEND", "memory")
    else:
        monkeypatch.setattr(queue.config, "QUEUE_BACKEND", "postgres")
        monkeypatch.setenv("QUEUE_DSN", str(tmp_path / "queue.sqlite3"))
    yield queue._backend()
    queue.reset()


# ── A6: one job, one claim ───────────────────────────────────────────────────

def test_a_second_worker_cannot_claim_a_job_the_first_holds(backend):
    """The double-CLAIM, closed by the transaction rather than by a check."""
    queue.enqueue("run-i1", "develop")
    first = queue.claim("worker-a")
    assert first is not None
    assert queue.claim("worker-b") is None, (
        "TWO WORKERS HOLD ONE JOB. Each would run the stage once, correctly, and "
        "the agent behind it would be invoked twice -- a duplicate PR comment and "
        "a second model bill."
    )


def test_enqueueing_the_same_stage_twice_raises_rather_than_making_two_jobs(backend):
    """Two rows for one (run_id, stage) is two workers each running it once.

    It RAISES rather than returning the existing job: a caller enqueuing the same
    triple twice has a bug, and handing back the first job would let the bug ship --
    the caller would go on believing it had scheduled work it had not.
    """
    queue.enqueue("run-i2", "develop")
    with pytest.raises(ValueError, match="already queued"):
        queue.enqueue("run-i2", "develop")
    assert len(queue.jobs_for_run("run-i2")) == 1


def test_a_deliberate_retry_uses_a_higher_attempt(backend):
    """`attempt` is the escape hatch, and it is part of the identity rather than a
    counter for reporting. A human who wants a stage retried performs a deliberate
    act with its own row, rather than something the queue did quietly."""
    queue.enqueue("run-i3", "develop", attempt=1)
    second = queue.enqueue("run-i3", "develop", attempt=2)
    assert second.attempt == 2
    assert len(queue.jobs_for_run("run-i3")) == 2


def test_a_terminal_job_is_never_offered_again(backend):
    """Checked rather than assumed: `complete` sets a terminal status and a later
    `claim` reads the same rows, so without the check a finished job would be handed
    out again the moment its lease field went stale."""
    for status, code in (("done", 0), ("blocked", 3), ("rejected", 4),
                         ("already_final", 5), ("failed", 1)):
        run = f"run-terminal-{status}"
        job = queue.enqueue(run, "develop")
        queue.claim("worker-a")
        queue.complete(job.job_id, status=status, exit_code=code)
        assert queue.claim("worker-b") is None, f"a {status} job was re-offered"


def test_adopt_run_id_updates_the_row_and_does_not_create_a_second(backend):
    """`plan` is enqueued with a placeholder because the real id does not exist
    until the stage runs. Two rows for one `plan` would be two claimable jobs."""
    job = queue.enqueue("pending-T1-123", "plan", ticket_id="T-1", ticket_text="x")
    queue.claim("worker-a")
    adopted = queue.adopt_run_id(job.job_id, "real-run-id")
    assert adopted.run_id == "real-run-id"
    assert adopted.job_id == job.job_id, "a new row was created"
    assert len(queue.jobs_for_run("real-run-id")) == 1
    assert queue.jobs_for_run("pending-T1-123") == []


def test_adopt_run_id_refuses_a_value_parsed_out_of_stdout_that_is_unsafe(backend):
    """This id arrives by being PARSED OUT OF A SUBPROCESS'S STDOUT, so it is the
    least trusted string in the package -- and the next thing anything does with it
    is build a path."""
    job = queue.enqueue("pending-T2-123", "plan")
    with pytest.raises(ValueError, match="unsafe run id"):
        queue.adopt_run_id(job.job_id, "../../etc/passwd")


def test_adopt_run_id_refuses_to_collide_with_an_existing_identity(backend):
    """Both backends refuse, though only one has a UNIQUE index to do it -- which is
    why this runs over both. A Python-only check on one side and a database
    constraint on the other must produce the same refusal."""
    queue.enqueue("taken-run", "plan")
    job = queue.enqueue("pending-T3-123", "plan")
    with pytest.raises(ValueError, match="already"):
        queue.adopt_run_id(job.job_id, "taken-run")


# ── A8: a killed worker's run stays claimable ────────────────────────────────

def test_an_expired_lease_is_reclaimed_and_the_reclaim_is_recorded(backend):
    """A worker killed mid-stage leaves the run CLAIMABLE, not lost.

    Actions gave this for free by killing the runner and marking the job failed.
    Here the lease is a column, so a dead worker's job ages back into the queue.

    `lease_seconds=-1` rather than a `time.sleep`: wall-clock waiting is how a
    165-second suite becomes a 400-second one, and the comparison under test is a
    string comparison of ISO-8601 timestamps either way.
    """
    queue.enqueue("run-a8", "develop")
    first = queue.claim("worker-killed", lease_seconds=-1)
    assert first is not None

    second = queue.claim("worker-fresh")
    assert second is not None, (
        "A KILLED WORKER'S JOB WAS NOT RECLAIMABLE. The run is stranded: nothing "
        "will ever claim it again and no surface says so."
    )
    assert second.job_id == first.job_id
    assert second.claimed_by == "worker-fresh"
    assert second.reclaimed_from == "worker-killed", (
        "the reclaim was not recorded; `reclaimed_from` is the ONLY trace anywhere "
        "that a stage may have run twice, and `worker._already_ran` reads it"
    )


def test_a_live_lease_is_not_reclaimed(backend):
    """The other half, and it is the half that matters more: a lease shorter than
    the work it covers hands the same job to a second worker while the first is
    still running it, which is the double-invocation this package exists to refuse."""
    queue.enqueue("run-a8b", "develop")
    queue.claim("worker-busy", lease_seconds=600)
    assert queue.claim("worker-greedy") is None


def test_heartbeat_keeps_a_slow_stage_from_being_reclaimed(backend):
    """A long stage is not a dead worker. `develop` runs the developer/reviewer loop
    plus the security scan, with `agent_client`'s read timeout at 300s PER
    INVOCATION -- renewal exists so a legitimately slower stage need not raise the
    lease for everybody."""
    queue.enqueue("run-a8c", "develop")
    job = queue.claim("worker-slow", lease_seconds=-1)
    queue.heartbeat(job.job_id, lease_seconds=600)
    assert queue.claim("worker-greedy") is None, (
        "a heartbeat did not protect the job; a slow stage would be run twice"
    )


# ── A6's second half: the worker refuses to re-run work already on the record ──

def test_already_ran_is_false_for_a_job_that_was_never_reclaimed(backend):
    """The check runs ONLY for a reclaimed job. A normal claim is the first claim,
    and reading the run's state for every job would make a fresh `develop` skip
    itself whenever a previous run of the same id had one."""
    queue.enqueue("run-i9", "develop")
    claimed = queue.claim("worker-a")
    assert claimed.reclaimed_from == ""
    assert worker._already_ran(claimed) is False


def test_already_ran_reads_the_RUN_not_the_queue(backend, tmp_path, monkeypatch):
    """THE LOAD-BEARING CHOICE. The queue knows a lease expired; only the RUN knows
    whether the stage finished its work before it did.

    A stage that got far enough to write its result is not run again -- because
    running it again posts duplicate comments and pays twice, while skipping one
    that had already written its result fails LOUDLY on the next stage (which finds
    a state it cannot use). This repository's discipline is to prefer the loud one.
    """
    from agentorg.state import DevResult, PlanResult, RunState, SecurityResult

    state = RunState(run_id="run-evidence", ticket_id="T-1", ticket_text="x")
    monkeypatch.setattr(worker.gates, "load", lambda run_id: state)

    job = queue.Job(run_id="run-evidence", stage="develop",
                    reclaimed_from="worker-dead")

    # No security verdict yet: the stage did NOT finish, so it must run.
    assert worker._already_ran(job) is False

    # `state.dev` alone is NOT enough, and that is deliberate -- see below.
    state.dev = DevResult(branch="b", diff="d", summary="s", files_changed=[])
    assert worker._already_ran(job) is False, (
        "`develop` was treated as complete on the strength of `state.dev`, which "
        "the DEVELOPER fills FIRST. That skips a reclaimed `develop` that produced "
        "a diff and then died before the scanners ran -- a run whose security "
        "verdict never happened, treated as complete."
    )

    # The LAST thing that stage writes is the security verdict.
    state.security = SecurityResult(verdict="pass", findings=[], blocking=[],
                                    explanation="e")
    assert worker._already_ran(job) is True

    # `plan` keys on its own result.
    plan_job = queue.Job(run_id="run-evidence", stage="plan",
                         reclaimed_from="worker-dead")
    assert worker._already_ran(plan_job) is False
    state.plan = PlanResult(tasks=["t"], acceptance_criteria=["a"],
                            target_files=["f"], notes="n")
    assert worker._already_ran(plan_job) is True


def test_develop_keys_on_security_and_not_on_dev():
    """Pinned as DATA as well as behaviour, because the mapping is one dict entry
    and a plausible-looking edit to `"dev"` would pass every other test here."""
    assert worker._STAGE_EVIDENCE["develop"] == "security", (
        "`develop` does four things in order and `state.dev` is filled by the "
        "FIRST; only `security` proves the whole stage finished"
    )


def test_already_ran_is_false_when_there_is_no_state_at_all(backend, monkeypatch):
    """No state means the stage did not get as far as saving one, so it did not run.
    Not an error: `plan` legitimately has no prior state."""
    def absent(run_id):
        raise FileNotFoundError(run_id)

    monkeypatch.setattr(worker.gates, "load", absent)
    job = queue.Job(run_id="run-gone", stage="plan", reclaimed_from="worker-dead")
    assert worker._already_ran(job) is False


def test_a_gate_stage_has_no_result_field_and_is_not_skipped(backend):
    """A gate records a DECISION rather than a result, and `gates.resume` appends to
    a list -- so a re-run appends a second identical decision rather than
    overwriting one. Visible and harmless (`not_promotable` READS the decisions
    rather than counting them), and there is no result field to read."""
    for stage in queue.GATES:
        job = queue.Job(run_id="run-gate", stage=stage, reclaimed_from="worker-dead")
        assert worker._already_ran(job) is False
        assert stage not in worker._STAGE_EVIDENCE
