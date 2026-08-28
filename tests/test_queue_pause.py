"""A4: a paused run survives a worker restart. THE PROPERTY ACTIONS CANNOT GIVE US.

OWNER: Lane A, task A4.

WHY THIS FILE IS THE IMPORTANT ONE
==================================
The seven-job pipeline exists entirely because of one sentence, which
`scripts/run_stage.py:9-22` and CLAUDE.md both state and which is true: "a GitHub
Environment pauses a JOB, and a job cannot pause in its middle." Every other
structural decision in the cloud path follows from it -- `develop` carries four
unrelated things because none is a gate boundary, the revision loop cannot be split
because Actions has no "repeat until", and three separate recorder JOBS exist
because a rejected Environment skips its job rather than running it with a verdict.

An Environment pauses by holding a runner slot. A queue pauses because a ROW says
so, which costs nothing and can happen anywhere. That is the whole claim of this
lane, and this file is where it is either true or not.

THE RESTART TESTS RUN ON THE SQL BACKEND ONLY, AND THAT IS NOT AN OMISSION
=========================================================================
`_memory.py` says it plainly: it cannot survive a restart, by construction -- the
dict dies with the process. A restart test written against it would pass trivially
while proving the OPPOSITE of its name, which is the single most repeatable form of
the nine-instance pattern CLAUDE.md records. So the durability tests below use a
real sqlite file and a genuinely separate `SqlQueue` instance, and the tests that
are about the pause's SEMANTICS rather than its durability run over both.
"""

import subprocess
import sys

import pytest

from agentorg import queue
from agentorg.queue import _sql

assert queue.GATES, "GATES is empty; every test in this file would pin nothing"
assert queue.REJECTION_STAGES, "no recorders; the refusal tests would pin nothing"

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


@pytest.fixture()
def queue_file(tmp_path):
    """A path for a sqlite queue. The FILE is the point: it outlives a process."""
    return tmp_path / "queue.sqlite3"


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, queue_file, monkeypatch):
    """Both backends, for the tests about what a pause MEANS."""
    queue.reset()
    if request.param == "memory":
        monkeypatch.setattr(queue.config, "QUEUE_BACKEND", "memory")
    else:
        monkeypatch.setattr(queue.config, "QUEUE_BACKEND", "postgres")
        monkeypatch.setenv("QUEUE_DSN", str(queue_file))
    yield queue._backend()
    queue.reset()


# ── what a pause MEANS. Both backends. ───────────────────────────────────────

def test_a_paused_job_is_not_claimable(backend):
    """THE ONE LINE THAT KEEPS THE THREE GATES STANDING AFTER THE SEVEN COLLAPSE.

    There is no lease timeout on a pause, no sweeper that ages one out, and no
    `force` argument. A worker that wanted to skip a gate would have to write to
    the queue's storage directly.
    """
    job = queue.enqueue("run-p1", "gate1", status="paused", awaiting_gate="gate1")
    assert queue.claim("worker-a") is None, (
        "a PAUSED job was handed to a worker; the gate did not hold"
    )
    assert queue.get(job.job_id).status == "paused"


def test_a_pause_clears_the_lease_so_no_sweeper_can_reclaim_it(backend):
    """A lingering lease would make a paused job look reclaimable to any `claim`
    that checked expiry before status."""
    job = queue.enqueue("run-p2", "develop")
    queue.claim("worker-a")
    paused = queue.pause(job.job_id, gate="gate2")
    assert paused.claimed_by == ""
    assert paused.lease_expires_at == "", (
        "a paused job still carries a lease; an expiry-first claim would take it"
    )


def test_pause_refuses_a_gate_that_is_not_one_of_the_three(backend):
    """`resume` addresses a paused job by (run_id, gate), so a pause at an
    unnameable gate is a run nothing can ever release -- and it LOOKS deliberate."""
    job = queue.enqueue("run-p3", "develop")
    with pytest.raises(ValueError, match="not a human gate"):
        queue.pause(job.job_id, gate="gate4")


def test_pause_refuses_a_run_that_has_already_ended(backend):
    job = queue.enqueue("run-p4", "develop")
    queue.claim("worker-a")
    queue.complete(job.job_id, status="blocked", exit_code=3)
    with pytest.raises(ValueError, match="already ended"):
        queue.pause(job.job_id, gate="gate2")


def test_resume_is_the_only_exit_and_it_cannot_be_called_without_a_decision(backend):
    """There is no argument to any function here that advances a paused run without
    saying what the human said."""
    queue.enqueue("run-p5", "gate1", status="paused", awaiting_gate="gate1")
    with pytest.raises(ValueError, match="not a gate decision"):
        queue.resume("run-p5", gate="gate1", decision="")
    with pytest.raises(ValueError, match="not a gate decision"):
        queue.resume("run-p5", gate="gate1", decision="probably")
    assert queue.claim("worker-a") is None, "a refused resume released the job anyway"


def test_an_approval_makes_the_gate_stage_claimable_unchanged(backend):
    queue.enqueue("run-p6", "gate1", status="paused", awaiting_gate="gate1")
    resumed = queue.resume("run-p6", gate="gate1", decision="approved",
                           approver="reem@example")
    assert resumed.stage == "gate1", "an approval must not repoint the stage"
    assert resumed.status == "ready"
    assert resumed.decided_by == "reem@example", (
        "the human's name is not on the row, so the gate stage would record "
        "`github-environment-reviewer` -- an Environment that never held this job"
    )
    assert queue.claim("worker-a") is not None


def test_a_refusal_repoints_the_job_at_the_gates_recorder(backend):
    """THE SINGLE LARGEST CORRECTNESS GAIN IN THIS LANE.

    `run_stage._stage_gate` hardcodes `decision="approved"` and CANNOT record a
    refusal, because on Actions a rejected Environment skips the gate job entirely.
    Three recorder jobs exist there for that reason, and each has to GUESS from
    `needs.<stage>.result` whether a human refused -- a guess that, measured on run
    32575709109, posted `REJECTED by mohamedsorour1998` for a run that was merely
    cancelled, naming a person who never saw the gate.

    Here the decision is an ARGUMENT. There is nothing to infer.
    """
    queue.enqueue("run-p7", "gate2", status="paused", awaiting_gate="gate2")
    resumed = queue.resume("run-p7", gate="gate2", decision="rejected",
                           approver="reem@example")
    assert resumed.stage == "gate2-rejected"
    assert resumed.stage == queue.REJECTION_STAGES["gate2"]


def test_overridden_is_an_approving_decision(backend):
    """`overridden` is the one capability a human is meant to keep -- the documented
    `gates_cli resume --decision overridden` route. A queue that refused it would
    delete that escape hatch while looking like it had tightened something."""
    queue.enqueue("run-p8", "gate2", status="paused", awaiting_gate="gate2")
    resumed = queue.resume("run-p8", gate="gate2", decision="overridden")
    assert resumed.stage == "gate2", "an override was treated as a refusal"
    assert resumed.status == "ready"


def test_resume_raises_when_no_job_is_paused_at_that_gate(backend):
    """A resume that silently did nothing would leave a human believing they had
    released a run, with the run still waiting and nothing anywhere saying so."""
    with pytest.raises(LookupError, match="no job paused"):
        queue.resume("run-nonexistent", gate="gate1", decision="approved")


def test_awaiting_lists_exactly_the_paused_jobs(backend):
    queue.enqueue("run-p9", "gate1", status="paused", awaiting_gate="gate1")
    queue.enqueue("run-p10", "develop")
    waiting = queue.awaiting()
    assert [job.run_id for job in waiting] == ["run-p9"]


# ── A4 PROPER: durability across a restart. sqlite only, for the reason above. ──

def test_a_paused_run_survives_a_new_backend_instance(queue_file, monkeypatch):
    """The row outlives the object that wrote it."""
    queue.reset()
    monkeypatch.setattr(queue.config, "QUEUE_BACKEND", "postgres")
    monkeypatch.setenv("QUEUE_DSN", str(queue_file))
    queue.enqueue("run-durable", "gate1", status="paused", awaiting_gate="gate1",
                  ticket_id="T-9", ticket_text="carried", poisoned=True)

    # A genuinely separate instance against the same file -- what a second worker
    # process gets. `queue.reset()` drops the built backend so nothing is shared.
    queue.reset()
    reopened = _sql.SqlQueue(dsn=str(queue_file), dialect="sqlite")
    waiting = reopened.awaiting()
    assert len(waiting) == 1, "the pause did not survive a new instance"
    assert waiting[0].awaiting_gate == "gate1"
    assert waiting[0].ticket_text == "carried"
    assert waiting[0].poisoned is True
    assert reopened.claim("worker-b") is None, (
        "a reopened queue handed out a PAUSED job; the gate did not survive"
    )
    queue.reset()


def test_a_paused_run_survives_A_REAL_PROCESS_RESTART(queue_file):
    """THE TEST A4 ACTUALLY ASKS FOR: a separate OS process, not a new object.

    A new instance in the same interpreter shares module state, the import system
    and any cached connection. A subprocess shares nothing but the file, which is
    the only honest reading of "survives a worker restart".

    PYTHONPATH IS SET, and without it this test would be the cf5cb83 failure again:
    a subprocess resolves `agentorg` through the editable install's finder, which
    points at whatever checkout `pip install -e` ran in rather than this tree. In a
    worktree that tree has no `agentorg.queue` at all.
    """
    import os

    def in_a_fresh_process(code: str) -> str:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
            env={**os.environ,
                 "PYTHONPATH": str(REPO_ROOT),
                 "QUEUE_BACKEND": "postgres",
                 "QUEUE_DSN": str(queue_file)},
        )
        assert completed.returncode == 0, (
            f"the child process failed:\n{completed.stdout}\n{completed.stderr}"
        )
        return completed.stdout

    # PROCESS 1 pauses a run at gate2, then exits.
    first = in_a_fresh_process(
        "from agentorg import queue\n"
        "job = queue.enqueue('run-restart', 'gate2', status='paused',\n"
        "                    awaiting_gate='gate2', ticket_id='POISON-1',\n"
        "                    ticket_text='survives', poisoned=True)\n"
        "print('PAUSED', job.job_id, job.status)\n"
    )
    assert "PAUSED" in first

    # PROCESS 2 -- a different interpreter, nothing shared but the file -- sees the
    # pause, cannot claim it, and reads back the inputs it never received.
    second = in_a_fresh_process(
        "from agentorg import queue\n"
        "waiting = queue.awaiting()\n"
        "print('WAITING', len(waiting), waiting[0].awaiting_gate,\n"
        "      waiting[0].ticket_text, waiting[0].poisoned)\n"
        "print('CLAIMED', queue.claim('worker-in-process-2'))\n"
    )
    assert "WAITING 1 gate2 survives True" in second, (
        f"the pause did not cross a real process boundary:\n{second}"
    )
    assert "CLAIMED None" in second, (
        f"A SECOND PROCESS CLAIMED A PAUSED JOB -- the gate did not hold across a "
        f"restart, which is the one property this lane exists to provide:\n{second}"
    )

    # PROCESS 3 resumes it with a decision, and only then is it claimable.
    third = in_a_fresh_process(
        "from agentorg import queue\n"
        "queue.resume('run-restart', gate='gate2', decision='approved',\n"
        "             approver='reem@example')\n"
        "job = queue.claim('worker-in-process-3')\n"
        "print('AFTER RESUME', job.stage, job.status, job.decided_by,\n"
        "      job.ticket_text, job.poisoned)\n"
    )
    assert "AFTER RESUME gate2 claimed reem@example survives True" in third, (
        f"the resumed job lost its decision or its inputs:\n{third}"
    )
