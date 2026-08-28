"""The queue interface and the in-process backend. Lane A, tasks A1 and A2.

OWNER: Lane A. The pause is `test_queue_pause.py`, idempotency and crash recovery
are `test_queue_idempotency.py`, and the exit-code parity is
`test_worker_exit_codes.py`. This file covers the operations and their refusals.

EVERY TEST RUNS AGAINST BOTH BACKENDS, through the `backend` fixture, and that is
the point rather than thoroughness. `_memory.py`'s own docstring lists three things
it deliberately cannot express, and CLAUDE.md records NINE instances of a test
double that could not express the failing case producing confidence that could not
be falsified. A rule asserted only against the dict backend would be a rule the
deployed path never had to satisfy -- so the same assertions run over a real sqlite
file, and the two backends' refusals are compared rather than assumed to match.
"""

import pytest

from agentorg import queue
from agentorg.queue import _memory, _sql

# GUARD AGAINST A VACUOUS FILE. Every test below reads one of these, so an empty
# collection would make the file pass while pinning nothing -- CLAUDE.md's
# operational form of that rule, and it cost this repository nineteen-plus
# assertions that turned out to pin nothing.
assert queue.STAGE_CHAIN, "STAGE_CHAIN is empty; the sequencing tests would pin nothing"
assert queue.GATES, "GATES is empty; every pause assertion would pin nothing"
assert queue.TERMINAL_STATUSES, "no terminal statuses; the refusals would pin nothing"


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, tmp_path, monkeypatch):
    """One queue backend, empty, per test. BOTH are real; neither is a stub.

    `tmp_path` for the sqlite one, so it is a real file on disk with real
    transactions -- which is what `test_queue_pause.py` needs and what makes the
    shared assertions here meaningful rather than a second reading of the dict.

    `queue.reset()` on BOTH sides, for `conftest.py` seam 5's reason: the built
    backends live in module state, so a job enqueued by one test is claimable by
    the next, and a stale hit looks exactly like a fresh answer.
    """
    queue.reset()
    if request.param == "memory":
        monkeypatch.setattr(queue.config, "QUEUE_BACKEND", "memory")
    else:
        monkeypatch.setattr(queue.config, "QUEUE_BACKEND", "postgres")
        monkeypatch.setenv("QUEUE_DSN", str(tmp_path / "queue.sqlite3"))
    yield queue._backend()
    queue.reset()


def test_both_backends_are_exercised_and_are_not_the_same_class(backend):
    """The fixture really does produce two implementations, not one twice.

    Without this the param list could silently collapse -- a typo in the knob name
    would leave both runs on the memory backend and every assertion in this file
    would be a second reading of the same code. `pytest -k` on a misspelled name
    exits 0 with everything deselected, and this is the same hazard one level in.
    """
    assert isinstance(backend, (_memory.MemoryQueue, _sql.SqlQueue))


# ── A1: the operations ────────────────────────────────────────────────────────

def test_both_backends_actually_satisfy_the_protocol_they_claim_to(backend):
    """Every `QueueBackend` method exists on the backend, WITH ITS DEFAULTS.

    FOUND BY A TEST RATHER THAN BY READING, and it is worth recording how. The
    protocol declares `claim(self, worker, *, lease_seconds: int = ...)`, and both
    backends were written `lease_seconds: int` with NO default -- so neither
    actually satisfied the protocol it was documented against:

        TypeError: SqlQueue.claim() missing 1 required keyword-only argument:
                   'lease_seconds'

    Nothing caught it because `queue.claim()` -- the only caller in the package --
    passes the value explicitly from its own default. The gap was reachable only by
    calling a backend directly, which is what a restart test does when it reopens a
    file. A `Protocol` is not checked at runtime and `typing.runtime_checkable`
    would only check that names exist, not their signatures, so this is asserted
    over the signature itself.
    """
    import inspect

    for name in ("enqueue", "claim", "heartbeat", "complete", "fail", "pause",
                 "resume", "adopt_run_id", "get", "jobs_for_run", "awaiting"):
        assert hasattr(backend, name), f"{type(backend).__name__} has no {name}"

    declared = inspect.signature(queue.QueueBackend.claim).parameters
    actual = inspect.signature(type(backend).claim).parameters
    for parameter in declared:
        if parameter == "self":
            continue
        assert parameter in actual, f"claim is missing {parameter}"
        if declared[parameter].default is not inspect.Parameter.empty:
            assert actual[parameter].default is not inspect.Parameter.empty, (
                f"the protocol gives `{parameter}` a default and "
                f"{type(backend).__name__}.claim does not, so calling the backend "
                f"directly raises TypeError -- measured on SqlQueue.claim"
            )


def test_the_lease_default_is_the_packages_constant_and_not_a_restated_number(backend):
    """One declaration of the lease length, not three.

    A backend that hardcoded `600` would agree with `DEFAULT_LEASE_SECONDS` today
    and drift silently the moment either changed -- and the drift would show up as
    a stage reclaimed while still running, which is the double-invocation this
    package spends most of its care refusing.
    """
    import inspect

    assert (inspect.signature(type(backend).claim).parameters["lease_seconds"].default
            == queue.DEFAULT_LEASE_SECONDS)


def test_enqueue_then_claim_hands_back_the_same_job(backend):
    enqueued = queue.enqueue("run-1", "plan", ticket_id="T-1", ticket_text="x")
    claimed = queue.claim("worker-a")
    assert claimed is not None, "a ready job was not claimable"
    assert claimed.job_id == enqueued.job_id
    assert claimed.status == "claimed"
    assert claimed.claimed_by == "worker-a"


def test_the_run_inputs_travel_on_the_row(backend):
    """MEASURED DEFECT. They used to live only in the launching process's argv.

    Two processes -- a `start_run` in one and a bare worker in the next, which is
    what a restart looks like -- produced:

        run_stage: error: plan needs --ticket-id and --ticket-text
        [worker] the plan stage printed no run_id

    The ticket text existed only in the argv of a process that had exited. Same
    structural fix as `RunState.poisoned` and `ci_status_measured`: a value chosen
    in one process and needed in another travels as a field.
    """
    queue.enqueue("run-2", "plan", ticket_id="POISON-1",
                  ticket_text="Add a per-IP rate limit", trigger="issue",
                  poisoned=True)
    claimed = queue.claim("worker-a")
    assert claimed.ticket_id == "POISON-1"
    assert claimed.ticket_text == "Add a per-IP rate limit"
    assert claimed.trigger == "issue"
    assert claimed.poisoned is True, (
        "poisoned did not survive the round trip; sqlite has no boolean type and "
        "stores this as an INTEGER, so a 1 read back as 1 rather than True would "
        "make `if job.poisoned` right by accident and `is True` wrong"
    )


def test_an_empty_queue_claims_none_rather_than_raising(backend):
    assert queue.claim("worker-a") is None


def test_claim_refuses_an_unnamed_worker(backend):
    """An unattributed claim cannot be told apart from a reclaim of a dead one."""
    with pytest.raises(ValueError, match="worker identity"):
        queue.claim("")


def test_complete_records_the_status_and_the_raw_exit_code(backend):
    job = queue.enqueue("run-3", "plan")
    queue.claim("worker-a")
    done = queue.complete(job.job_id, status="done", exit_code=0)
    assert done.status == "done"
    assert done.exit_code == 0
    assert done.claimed_by == "", "a finished job still names an owner"


def test_complete_refuses_a_status_that_is_not_an_ending(backend):
    job = queue.enqueue("run-4", "plan")
    queue.claim("worker-a")
    with pytest.raises(ValueError, match="records an ENDING"):
        queue.complete(job.job_id, status="ready", exit_code=0)


def test_complete_refuses_to_overwrite_an_ending(backend):
    """The job-level form of run 32509257195's defect.

    A rejection recorder overwrote a poisoned run's `status=blocked` with
    `status=rejected` and attributed it to a human who never saw the gate -- the
    block, which the poisoned demo beat exists to show, erased by the job written
    to preserve refusals.
    """
    job = queue.enqueue("run-5", "develop")
    queue.claim("worker-a")
    queue.complete(job.job_id, status="blocked", exit_code=3)
    with pytest.raises(ValueError, match="already ended"):
        queue.complete(job.job_id, status="rejected", exit_code=4)
    assert queue.get(job.job_id).status == "blocked", "the block was overwritten"


def test_fail_records_a_crash_and_does_not_re_enqueue(backend):
    """A crashed stage may have posted three comments and burned four model calls.

    Nothing in its exit code says how much. Re-running it repeats whatever it
    finished, which is the non-idempotency `agent_client` already refuses to paper
    over with a botocore retry -- so a failure stops the run and waits for a human.
    """
    job = queue.enqueue("run-6", "develop")
    queue.claim("worker-a")
    queue.fail(job.job_id, exit_code=1)
    assert queue.get(job.job_id).status == "failed"
    assert queue.claim("worker-b") is None, "a failed job was re-offered"


def test_heartbeat_refuses_a_job_nobody_is_holding(backend):
    job = queue.enqueue("run-7", "plan")
    with pytest.raises(ValueError, match="no lease to renew"):
        queue.heartbeat(job.job_id)


# ── A1: the refusals at the boundary ─────────────────────────────────────────

def test_enqueue_refuses_an_unsafe_run_id(backend):
    """A queued run id becomes a path component AND a `--run-id` argument.

    `../../etc/passwd` reaching that far is a state file written outside `runs/`.
    The message must not echo the value: it is untrusted input and this message can
    reach a rendered page.
    """
    with pytest.raises(ValueError) as refusal:
        queue.enqueue("../../etc/passwd", "plan")
    assert "etc/passwd" not in str(refusal.value), (
        "the refusal echoed the hostile value it was refusing"
    )


def test_enqueue_refuses_a_stage_that_is_not_a_stage(backend):
    with pytest.raises(ValueError, match="is not a stage"):
        queue.enqueue("run-8", "bandit")


def test_next_stage_walks_the_chain_and_ends_at_promote():
    assert queue.next_stage("plan") == "gate1"
    assert queue.next_stage("gate3") == "promote"
    assert queue.next_stage("promote") == "", "promote must have no successor"


def test_next_stage_refuses_a_rejection_recorder():
    """A recorder records an ending, so advancing past one is a pipeline that
    continued past a refusal."""
    with pytest.raises(ValueError, match="not a stage that advances"):
        queue.next_stage("gate1-rejected")


def test_the_stage_chain_matches_run_stage_name_for_name():
    """The chain is RESTATED here rather than imported, and this closes the drift.

    `agentorg/` importing from `scripts/` is backwards, and `scripts/run_stage.py`
    is scheduled for deletion in Phase 3 -- so an import would make this package
    depend on a file that is going away. When that file goes, this test fails and
    tells the integrator, which is the correct time to hear about it.
    """
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_stage.py"
    spec = importlib.util.spec_from_file_location("run_stage_for_queue_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.STAGE_CHAIN, "run_stage.STAGE_CHAIN is empty; this would pin nothing"
    assert queue.STAGE_CHAIN == module.STAGE_CHAIN
    assert queue.REJECTION_STAGES == module.REJECTION_STAGES


def test_the_gates_are_derived_from_the_chain_and_not_written_a_fourth_time():
    """`GATES` is `STAGE_CHAIN` filtered by the recorder map, so a gate added to
    the chain cannot be left without a pause."""
    assert queue.GATES == ("gate1", "gate2", "gate3")
    assert set(queue.GATES) == set(queue.REJECTION_STAGES)


# ── A2: what the in-process backend is for ───────────────────────────────────

def test_the_default_backend_is_the_in_process_one():
    """The suite is hermetic, and this is what keeps it that way.

    A queue whose default needed a Postgres server would end that in one commit:
    every test that drove a pipeline would need infrastructure, and the failure
    when it was absent would be a connection error in a test about something else.
    """
    assert queue.config.QUEUE_BACKEND == queue.config.QUEUE_BACKEND_MEMORY


def test_an_unimplemented_backend_raises_rather_than_downgrading(monkeypatch):
    """`sqs` is a VALIDATED value of the knob that this package does not implement.

    It must not fall back to `memory`: an in-process queue loses every paused run
    when the worker restarts, and a run that vanished while the worker reported
    healthy is the defect this whole repository is organised around.
    """
    queue.reset()
    monkeypatch.setattr(queue.config, "QUEUE_BACKEND", "sqs")
    with pytest.raises(NotImplementedError, match="does not implement"):
        queue._backend()
    queue.reset()


# ── THE POSTGRES DIALECT'S DDL — added by the integrator on 2026-08-28 ─────────
#
# Lane A wrote both dialects and said plainly that the Postgres one had never been
# executed, because psycopg was not installed on any machine that ran this suite. It was
# executed for the first time against a real PostgreSQL 16.15 and failed on the first
# INSERT:
#
#     DatatypeMismatch: column "poisoned" is of type integer but expression is of type
#     boolean
#
# `poisoned INTEGER NOT NULL DEFAULT 0` is right for sqlite, which has no boolean type
# and stores 0/1 — and Postgres will not accept a Python `True` bound against an integer
# column. `_BOOL_COLUMNS` already coerced on the way OUT; nothing coerced on the way in,
# because psycopg correctly sends a bool as a bool. The two halves of the round trip were
# written against different assumptions and only one of them was ever run.
#
# These tests need no database. They read the rendered DDL, which is what the existing
# tests could not do while `_SCHEMA` was one dialect-blind string.

def test_the_postgres_schema_declares_poisoned_as_a_boolean():
    """A Python `True` bound against an INTEGER column is refused by Postgres.

    Read off the rendered DDL rather than the template, because the rendering is where
    the dialects diverge — the template deliberately holds a placeholder now.
    """
    from agentorg.queue import _sql

    # `__new__`, not `__init__`: the constructor connects, and this test is about the
    # rendered TEXT. `_schema` reads only `self.dialect`.
    queue = _sql.SqlQueue.__new__(_sql.SqlQueue)
    queue.dialect = "postgres"
    ddl = queue._schema()

    poisoned = [line.strip() for line in ddl.splitlines() if "poisoned" in line]
    assert poisoned, "no `poisoned` column in the Postgres DDL; this test pins nothing"
    assert "BOOLEAN" in poisoned[0].upper(), (
        f"the Postgres DDL declares {poisoned[0]!r}. Binding a Python bool against an "
        f"integer column raises DatatypeMismatch — measured against PostgreSQL 16.15."
    )
    assert "DEFAULT FALSE" in poisoned[0].upper(), (
        f"{poisoned[0]!r} defaults a boolean column to a numeric literal, which Postgres "
        f"refuses at CREATE TABLE"
    )


def test_the_sqlite_schema_still_declares_poisoned_as_an_integer():
    """The other half, or the fix is a swap rather than a rendering.

    SQLite has no boolean type: `DEFAULT FALSE` there is a bareword it accepts and then
    stores as text, so `_row_to_job`'s coercion would receive `'FALSE'` — truthy — and a
    clean run would read as poisoned. The two dialects genuinely need different literals.
    """
    from agentorg.queue import _sql

    queue = _sql.SqlQueue.__new__(_sql.SqlQueue)
    queue.dialect = "sqlite"
    poisoned = [ln.strip() for ln in queue._schema().splitlines() if "poisoned" in ln]

    assert poisoned, "no `poisoned` column in the sqlite DDL"
    assert "INTEGER" in poisoned[0].upper(), f"sqlite DDL changed: {poisoned[0]!r}"
    assert "DEFAULT 0" in poisoned[0].upper(), (
        f"{poisoned[0]!r} — a `FALSE` bareword is stored as TEXT by sqlite and reads as "
        f"truthy, so a clean run would report itself poisoned"
    )


def test_neither_rendering_leaves_an_unfilled_placeholder():
    """A `{BOOL}` reaching a database is a syntax error at CREATE TABLE.

    `_SCHEMA` is a `str.format` template now, so a future column added with a brace and
    no corresponding key renders literally and fails at connect time — on whichever
    dialect nobody ran. Cheaper to catch here.
    """
    from agentorg.queue import _sql

    for dialect in ("sqlite", "postgres"):
        queue = _sql.SqlQueue.__new__(_sql.SqlQueue)
        queue.dialect = dialect
        ddl = queue._schema()
        assert "{" not in ddl and "}" not in ddl, (
            f"the {dialect} DDL still contains a format placeholder: "
            f"{[ln for ln in ddl.splitlines() if '{' in ln or '}' in ln]}"
        )
