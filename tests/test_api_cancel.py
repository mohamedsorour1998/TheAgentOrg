"""K7, second half: cancellation is honoured mid-run. And K2's reads, and K3.

OWNER: Lane K.

THE THREE POSITIONS A RUN CAN BE CANCELLED FROM, and they are genuinely different
mechanisms rather than three spellings of one:

    READY      the next stage has not been claimed  -> `queue.claim` skips it
    PAUSED     a human gate holds it, nothing runs  -> nothing to race
    CLAIMED    a stage is executing right now       -> the subprocess is NOT
                                                       killed; the worker's later
                                                       `complete` is REFUSED

The third is the one worth testing hardest, because it is where a cancellation API
usually lies. This one does not claim to stop the work -- it guarantees that the
work cannot ADVANCE the run, and the mechanism is `queue.complete`'s refusal to
overwrite a terminal status. `test_a_cancelled_run_cannot_be_advanced_by_the_stage_
that_was_already_executing` drives exactly that sequence.

WHY NOT A KILLED SUBPROCESS: `queue.fail`'s docstring gives the reason and it
applies unchanged -- "a crashed stage may have completed half its work -- opened a
PR, posted three comments, burned four model calls -- and nothing in its exit code
says how much". A cancel that killed the process would leave that state behind
while reporting the run cancelled, which is a stronger claim than the system can
support.
"""

import pytest

from agentorg import queue
from agentorg.api import auth, idempotency, service
from agentorg.api.errors import Conflict, Forbidden, NotFound, Unprocessable
from agentorg.security import scoring

# The gates are read from the queue rather than restated, so a change there does
# not leave this file testing a gate that no longer exists.
assert queue.GATES, "queue.GATES is empty; the gate-pause tests would pin nothing"
assert queue.TERMINAL_STATUSES, "no terminal statuses; the cancel tests would pin nothing"


@pytest.fixture(autouse=True)
def _clean_substrate():
    """Fresh stores on both sides of every test. See conftest.py guard 5."""
    queue.reset()
    auth.set_key_store(auth.InMemoryKeyStore())
    idempotency.set_idempotency_store(idempotency.IdempotencyStore())
    service.reset()
    yield
    queue.reset()
    auth.set_key_store(auth.InMemoryKeyStore())
    idempotency.set_idempotency_store(idempotency.IdempotencyStore())
    service.reset()


@pytest.fixture()
def credential():
    _, key = auth.issue_key("tenant-alpha")
    return auth.resolve(f"Bearer {key}")


def _submit(credential, ticket_id="7"):
    return service.submit_run(
        credential,
        service.RunSubmission(ticket_id=ticket_id, ticket_text="rate limit login"),
    )[0]


# ──────────────────────────────────────────────────────────────────────────────
# CANCELLATION, AT EACH OF THE THREE POSITIONS
# ──────────────────────────────────────────────────────────────────────────────

def test_cancelling_a_ready_run_stops_it_before_any_worker_claims_it(credential):
    """Position one. The strongest case, and the easiest.

    The assertion that matters is `queue.claim` returning None, not the status
    string: a cancel that recorded `rejected` and left the job claimable would
    satisfy a status check and still run the pipeline.
    """
    run = _submit(credential)
    assert queue.jobs_for_run(run.run_id)[0].status == "ready"

    cancelled = service.cancel_run(credential, run.run_id)
    assert cancelled.status == "rejected"
    assert queue.claim("a-worker") is None, (
        "the job is still claimable after being cancelled, so a worker would run "
        "the stage anyway"
    )


def test_cancelling_a_run_paused_at_a_gate_removes_it_from_the_awaiting_list(credential):
    """Position two. A pause is a durable ROW, so there is nothing to race.

    `queue.awaiting()` is what an approval screen lists, so a cancelled run still
    appearing there would offer a human a decision on a run that has ended -- the
    phantom decision `approve_server`'s single predicate exists to prevent.
    """
    run = _submit(credential)
    plan = queue.jobs_for_run(run.run_id)[0]
    queue.complete(plan.job_id, status="done", exit_code=0)
    queue.enqueue(run.run_id, "gate1", status="paused", awaiting_gate="gate1",
                  tenant_id="tenant-alpha")
    assert [job.awaiting_gate for job in queue.awaiting()] == ["gate1"]

    cancelled = service.cancel_run(credential, run.run_id)
    assert cancelled.status == "rejected"
    assert queue.awaiting() == [], (
        "a cancelled run is still awaiting a human decision, so the approval "
        "screen would offer a gate on a run that has ended"
    )
    assert queue.claim("a-worker") is None


def test_a_cancelled_run_cannot_be_advanced_by_the_stage_that_was_already_executing(
    credential,
):
    """POSITION THREE, AND THE ONE THAT DEFINES THE GUARANTEE.

    A claimed job means a subprocess is running. The cancel does not kill it -- and
    the property that makes the cancel meaningful anyway is that the worker's
    result cannot advance the run. `queue.complete` refuses to record a second
    ending, which is the same refusal that protected a poisoned run's
    `status=blocked` on run 32509257195.

    This sequence is the API's contract stated as a test: claim, cancel, then let
    the worker try to report success.
    """
    run = _submit(credential)
    claimed = queue.claim("worker-1")
    assert claimed is not None and claimed.status == "claimed", (
        "nothing was claimable, so this test would pin nothing"
    )

    cancelled = service.cancel_run(credential, run.run_id)
    assert cancelled.status == "rejected"

    with pytest.raises(ValueError, match="already ended"):
        queue.complete(claimed.job_id, status="done", exit_code=0)

    assert queue.jobs_for_run(run.run_id)[-1].status == "rejected", (
        "the executing stage's result overwrote the cancellation"
    )


def test_cancelling_a_run_that_already_ended_is_a_conflict_not_a_success(credential):
    """A cancel must never report success for a run it did not cancel.

    409 rather than 200, and rather than 400: the request was well formed and
    would have been valid a minute earlier. A caller retrying a cancel after a run
    BLOCKED needs to tell "already over" from "cancelled by me" -- and reporting
    200 would erase the distinction between the pipeline working and an operator
    stopping it.
    """
    run = _submit(credential)
    service.cancel_run(credential, run.run_id)
    with pytest.raises(Conflict):
        service.cancel_run(credential, run.run_id)


def test_cancelling_a_blocked_run_does_not_rewrite_its_ending(credential):
    """THE DEMO'S POISONED BEAT MUST SURVIVE A CANCEL.

    `blocked` is the deterministic block rule working, and it is the one status
    this project most needs to keep on the record -- a recorder overwriting it is
    a measured defect (run 32509257195), where "the block was erased by the job
    written to preserve refusals".
    """
    run = _submit(credential)
    plan = queue.jobs_for_run(run.run_id)[0]
    queue.complete(plan.job_id, status="blocked", exit_code=3)

    with pytest.raises(Conflict):
        service.cancel_run(credential, run.run_id)

    job = queue.jobs_for_run(run.run_id)[-1]
    assert job.status == "blocked", "the cancel overwrote a block"
    assert job.exit_code == 3, "the block's exit code 3 was rewritten"


def test_a_cancel_records_the_refusal_exit_code_from_run_stages_own_table(credential):
    """The exit code is `run_stage.py`'s, read through the queue's table.

    A hardcoded `4` would be a second declaration of `EXIT_REFUSED`, and CLAUDE.md
    records three mutations that survived 793 tests because `run_stage.py`'s
    constants were restated rather than imported.
    """
    from agentorg.queue import exit_codes

    run = _submit(credential)
    cancelled = service.cancel_run(credential, run.run_id)
    assert cancelled.exit_code == exit_codes.code_for("rejected")
    assert cancelled.exit_code != 3, (
        "a cancel must not report the block rule's exit code; the poisoned demo "
        "run and an operator's cancel would be indistinguishable"
    )


def test_another_tenant_cannot_cancel_this_tenants_run(credential):
    """The write path is scoped, not only the read path.

    A cross-tenant CANCEL is worse than a cross-tenant read: it destroys work. So
    it is asserted separately rather than assumed from the status test.
    """
    run = _submit(credential)
    _, other_key = auth.issue_key("tenant-beta")
    other = auth.resolve(f"Bearer {other_key}")

    with pytest.raises(Forbidden):
        service.cancel_run(other, run.run_id)

    assert queue.jobs_for_run(run.run_id)[0].status == "ready", (
        "the run was cancelled by another tenant despite the refusal"
    )


# ──────────────────────────────────────────────────────────────────────────────
# K2's READ
# ──────────────────────────────────────────────────────────────────────────────

def test_the_status_reports_every_stage_the_run_has_had(credential):
    """The run's history as the queue saw it, oldest first."""
    run = _submit(credential)
    plan = queue.jobs_for_run(run.run_id)[0]
    queue.complete(plan.job_id, status="done", exit_code=0)
    queue.enqueue(run.run_id, "gate1", status="paused", awaiting_gate="gate1",
                  tenant_id="tenant-alpha")

    status = service.run_status(credential, run.run_id)
    assert [row["stage"] for row in status.stages] == ["plan", "gate1"]
    assert status.stage == "gate1", "the headline must be the run's CURRENT position"
    assert status.awaiting_gate == "gate1"
    assert status.status == "paused"


def test_the_status_surfaces_a_reclaim_because_it_is_the_only_trace_of_a_double_run(
    credential,
):
    """`reclaimed_from` is the one signal that a stage may have run twice.

    CLAUDE.md: the claim is at-least-once, not exactly-once, and this field is the
    only trace. A status view that dropped it would remove the one place an
    operator could notice.
    """
    run = _submit(credential)
    job = queue.claim("worker-1", lease_seconds=0)
    reclaimed = queue.claim("worker-2")
    assert reclaimed is not None and reclaimed.job_id == job.job_id, (
        "the lease did not expire, so this test would pin nothing"
    )
    assert reclaimed.reclaimed_from == "worker-1"

    status = service.run_status(credential, run.run_id)
    assert status.reclaimed is True, (
        "a reclaimed stage is invisible in the status, so the only trace that a "
        "stage may have run twice never reaches an operator"
    )
    assert status.stages[-1]["reclaimed_from"] == "worker-1"


def test_a_run_with_no_jobs_is_not_found_and_an_unsafe_id_never_reaches_the_queue(
    credential,
):
    """404 for both, and the traversal case is refused BEFORE any lookup.

    `log.is_safe_run_id` is a positive test for one safe path component, and
    `gates._state_path` does no containment check of its own -- with
    `../../etc/passwd` it resolves outside `runs/` entirely.
    """
    with pytest.raises(NotFound):
        service.run_status(credential, "no-such-run")
    for hostile in ("../../etc/passwd", "..", ".", "a/b", ""):
        with pytest.raises(NotFound):
            service.run_status(credential, hostile)


def test_a_run_enqueued_without_a_tenant_belongs_to_tenant_zero(credential):
    """A pre-API run must be visible to the API, and to exactly one tenant.

    `scripts/worker.py --start` enqueues with no tenant, and every pre-tenancy
    `RunState` carries `""`. Read as "belongs to nobody" those runs are invisible;
    read as "belongs to whoever asks" it is a leak. `tenant_zero.for_run_state`
    translates it to exactly one tenant.
    """
    from agentorg.tenancy import tenant_zero

    queue.enqueue("legacy-run-0001", "plan", ticket_id="1", ticket_text="x")

    _, zero_key = auth.issue_key(tenant_zero.TENANT_ZERO_ID)
    zero = auth.resolve(f"Bearer {zero_key}")
    assert service.run_status(zero, "legacy-run-0001").stage == "plan"

    with pytest.raises(Forbidden):
        service.run_status(credential, "legacy-run-0001")


# ──────────────────────────────────────────────────────────────────────────────
# K3: the threshold goes through resolve_threshold, and security cannot be off
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("threshold", ["low", "medium", "high", "critical"])
def test_every_legal_threshold_is_accepted(threshold, credential):
    """All four pass today, because the floor is `critical`.

    Stated as a test rather than left implicit, because a reader who assumed the
    floor refuses something today would be wrong -- `scoring.resolve_threshold`'s
    docstring says so, and this is the assertion that keeps that honest.
    """
    written = service.write_config(
        credential, service.RepositoryConfig(full_name="acme/auth", threshold=threshold)
    )
    assert written.threshold == threshold


@pytest.mark.parametrize("threshold", ["HIGH", "High", "catastrophic", "none", "0"])
def test_a_threshold_outside_the_vocabulary_is_refused_with_its_own_message(
    threshold, credential
):
    """`HIGH` is the measured one: it used to raise `KeyError` inside the security
    agent -- "the one stage whose whole purpose is to produce a verdict, dying
    while producing one, with a traceback naming a dict lookup".

    The refusal must carry `resolve_threshold`'s own words, so the API does not
    invent a second explanation of a decision made elsewhere.
    """
    with pytest.raises(Unprocessable) as refused:
        service.write_config(
            credential,
            service.RepositoryConfig(full_name="acme/auth", threshold=threshold),
        )
    assert "not a severity" in refused.value.message


def test_the_threshold_floor_still_refuses_when_the_secret_policy_is_lowered(credential):
    """THE FLOOR IS REACHED THROUGH THIS API, not merely present in scoring.py.

    The floor is `critical` today, so no legal threshold is refused -- which means
    a test that only tried the four legal values could not tell whether this API
    consults the floor at all. So the source of truth is lowered the way
    `test_the_threshold_floor_binds_when_the_secret_policy_is_lowered` does, and a
    previously legal threshold must become a refusal HERE.

    Patched on the module `service` actually calls, so a `write_config` that
    validated the threshold itself -- with its own `Literal` or its own comparison
    -- would keep accepting `high` and fail this test.
    """
    assert scoring.THRESHOLD_FLOOR == "critical", (
        f"the floor is {scoring.THRESHOLD_FLOOR!r}; this test assumes it starts at "
        f"the top of the scale, so re-derive it before trusting the result"
    )
    service.write_config(
        credential, service.RepositoryConfig(full_name="acme/auth", threshold="high")
    )  # legal before

    original = scoring.THRESHOLD_FLOOR
    try:
        scoring.THRESHOLD_FLOOR = "low"
        with pytest.raises(Unprocessable) as refused:
            service.write_config(
                credential,
                service.RepositoryConfig(full_name="acme/auth", threshold="high"),
            )
        assert "above" in refused.value.message
        assert "no longer block" in refused.value.message, (
            "the refusal does not explain that a committed credential would stop "
            "blocking, which is the only reason the floor exists"
        )
    finally:
        scoring.THRESHOLD_FLOOR = original

    assert scoring.THRESHOLD_FLOOR == "critical", "the floor was not restored"


def test_the_security_check_cannot_be_turned_off(credential):
    """The one binding check in the pipeline has no off switch.

    A customer able to disable it gets a pipeline whose three human gates report
    that security passed -- worse than a pipeline with no scanners, because the
    gates would be told the opposite of the truth.
    """
    assert "security" not in service.OPTIONAL_CHECKS, (
        "security is configurable, so this test would pin nothing"
    )
    with pytest.raises(Unprocessable) as refused:
        service.write_config(
            credential,
            service.RepositoryConfig(full_name="acme/auth", checks={"security": False}),
        )
    assert "not configurable" in refused.value.message


def test_the_advisory_checks_can_be_turned_off(credential):
    """THE CONTROL. Without it, a `write_config` that refused every `checks` value
    would satisfy the test above while making the feature useless.
    """
    written = service.write_config(
        credential,
        service.RepositoryConfig(full_name="acme/auth",
                                 checks={"review": False, "sre": True}),
    )
    assert written.checks == {"review": False, "sre": True}


def test_an_unconfigured_repository_reports_the_threshold_the_pipeline_would_use(
    credential,
):
    """A blank would make the caller interpret it; the resolved value does not."""
    default = service.read_config(credential, "never/configured")
    assert default.threshold == scoring.resolve_threshold()
    assert default.checks == dict.fromkeys(service.OPTIONAL_CHECKS, True)


def test_config_is_per_tenant(credential):
    """Two tenants configuring one repository name must not share a row.

    `schema.REPOSITORY` makes `full_name` unique PER TENANT for the stated reason:
    "two customers may both connect a repository called `acme/auth-service`, and a
    global unique constraint would make one customer's onboarding fail with a
    message about a repository they cannot see."
    """
    service.write_config(
        credential, service.RepositoryConfig(full_name="acme/auth", threshold="low")
    )
    _, other_key = auth.issue_key("tenant-beta")
    other = auth.resolve(f"Bearer {other_key}")
    assert service.read_config(other, "acme/auth").threshold == scoring.resolve_threshold()
    assert service.read_config(credential, "acme/auth").threshold == "low"
