"""K1, K2, K3: submit a run, watch it, cancel it, configure a repository.

OWNER: Lane K. Transport-free on purpose -- every function here takes a
`Credential` and plain values and returns a pydantic model. `api/server.py` is the
only module that knows about HTTP, so these are testable without a socket and the
refusals are assertable as exceptions rather than as status codes.

NOTHING HERE ADVANCES A RUN. Read `api/__init__.py` for the argument; the
mechanical form is that this module imports `queue` for `enqueue`, `get`,
`jobs_for_run` and `complete`, and imports NEITHER `queue.resume` NOR anything
from `gates`. `test_no_api_module_can_reach_a_gate_resume` asserts that over the
AST, because a comment naming `resume` satisfies a grep while the call stays --
CLAUDE.md records that exact failure twice in one lane.

WHY `submit_run` CHECKS A BUDGET AND THE CHECK IS BEFORE THE ENQUEUE
===================================================================
`tenancy.budgets.check` is documented as "checked BEFORE a run starts", and this
is the surface where a run starts. Checked after the enqueue, a worker could claim
the job before the refusal was recorded and the run would proceed -- the same
ordering defect `test_the_sre_stage_measures_ci_before_invoking_the_agent` pins
over the AST for `ci_status_measured`, and its lesson applies verbatim: "order is
the requirement, not the call", and measuring afterwards "reads exactly like
correct code".

The budget check is SKIPPED when no database connection is supplied, and that is
the one place this module is permissive. Stated plainly rather than buried: with
no connection there is no budget row to read, so `budgets.check` would refuse
every submission and the API would be unusable in the single-tenant deployment
that has no tenancy database yet. The refusal direction is preserved where it
matters -- given a connection, a tenant with no budget row is REFUSED, because
that is `budgets.check`'s own decision and this module does not soften it.
"""

from __future__ import annotations

import secrets
import time

from pydantic import BaseModel, Field

from .. import log, queue
from ..security import scoring
from ..tenancy import budgets, tenant_zero
from .auth import (
    SCOPE_CONFIG_READ,
    SCOPE_CONFIG_WRITE,
    SCOPE_RUNS_READ,
    SCOPE_RUNS_WRITE,
    Credential,
)
from .errors import Conflict, Forbidden, NotFound, Unprocessable
from .idempotency import Record, idempotency_store

# The checks a repository can turn off, and the one it cannot. See
# `RepositoryConfig.checks` -- `security` is absent from this tuple deliberately.
OPTIONAL_CHECKS = ("review", "sre")

# A ticket longer than this is refused. It goes straight into an agent prompt, and
# `modules/ingress`'s transformer already declines to forward an issue BODY for
# this reason: "unbounded, may hold anything, and goes straight into an agent
# prompt". A title-sized bound keeps that property on the API path.
MAX_TICKET_TEXT = 4000
MAX_TICKET_ID = 200


class RunSubmission(BaseModel):
    """K1's request body.

    `poisoned` IS ACCEPTED AND IS NOT A HAZARD ON THIS PATH, which is worth
    saying because the ingress transformer hardcodes it to `"false"`. There, the
    reason is that a label attaches after an issue opens so the payload's labels
    are reliably empty -- reading them "would produce a clean run while appearing
    to honour the label". Here the caller states it directly in a JSON body, so
    there is nothing to infer and no gap between what was asked and what happens.

    A real JSON boolean, unlike `workflow_dispatch`, whose inputs "arrive as
    STRINGS, booleans included". This is our own API and can have the type; the
    string-parsing rule belongs to the Actions boundary, and importing it here
    would mean a caller sending `true` got a 422 for being correct.
    """

    ticket_id: str = Field(min_length=1, max_length=MAX_TICKET_ID)
    ticket_text: str = Field(min_length=1, max_length=MAX_TICKET_TEXT)
    poisoned: bool = False
    # `trigger` defaults to `api`, and the value MUST DIFFER from `manual` and
    # `issue` for the reason `tests/test_trigger_provenance.py` gives about those
    # two: identical values would make a run recording the value
    # indistinguishable from one whose trigger was never set, so the field would
    # be present, populated and worthless.
    trigger: str = "api"


class RunStatus(BaseModel):
    """K2's response: what the queue knows about a run.

    DERIVED FROM `queue.jobs_for_run`, never stored. A control plane holding its
    own copy of a run's progress is a second writer of a fact that already has
    one, and the two disagree exactly when it matters -- during the run.

    `stages` is every job the run has had, oldest first, which is the run's
    history as the queue saw it including a reclaim. `reclaimed` is surfaced
    rather than hidden because it is the ONLY trace that a stage may have run
    twice (CLAUDE.md), and a status view that dropped it would be the one place
    an operator could have noticed.
    """

    run_id: str
    tenant_id: str
    status: str
    stage: str
    awaiting_gate: str = ""
    exit_code: int | None = None
    reclaimed: bool = False
    stages: list[dict] = Field(default_factory=list)
    cost_usd: float | None = None
    cost_stages: int = 0


class RepositoryConfig(BaseModel):
    """K3's body: the threshold and which checks are on.

    `threshold` GOES THROUGH `scoring.resolve_threshold` AND IS NOT VALIDATED
    HERE. That function refuses a value outside the vocabulary and refuses one
    above `THRESHOLD_FLOOR`, and its docstring states why the second matters: "a
    knob that can disable the product's core guarantee is a defect rather than a
    feature". A pydantic `Literal` here would duplicate the first refusal and
    MISS the second, and the duplicate is the more dangerous half -- a caller
    whose value passed our Literal would believe it was accepted.

    `security` IS NOT IN `OPTIONAL_CHECKS`, so it cannot be turned off. The
    reviewer is advisory and the SRE is advisory; the security verdict is the one
    binding check in the pipeline, and `compute_security_verdict` is the whole
    thesis of this repository. A configuration API that could disable it would
    hand a customer the ability to ship a credential to `main` past three humans
    who were told a scanner had cleared it.
    """

    full_name: str = Field(min_length=1, max_length=200)
    threshold: str = ""
    checks: dict[str, bool] = Field(default_factory=dict)


def _tenant(credential: Credential) -> str:
    """The credential's tenant, translated once.

    `tenant_zero.for_run_state` is THE one translation point for the blank
    single-tenant marker. `auth.issue_key` already refuses a blank, so this is
    belt-and-braces -- and it is the right kind: it means a future key store that
    forgot that refusal cannot produce a blank scope here.
    """
    return tenant_zero.for_run_state(credential.tenant_id)


def _placeholder_run_id(tenant_id: str, ticket_id: str) -> str:
    """The placeholder `plan` is enqueued under.

    Mirrors `worker.start_run`'s shape (`pending-<ticket>-...`) rather than
    inventing a second one, and sanitises the ticket for the same stated reason:
    "a ticket id like `a/b` would otherwise make an unsafe run id out of a
    legitimate ticket".

    THE TENANT IS IN THE PLACEHOLDER, which `worker.start_run` has no need for.
    Two tenants submitting the same ticket id at the same moment would otherwise
    collide on the queue's UNIQUE index, and the second would receive a refusal
    naming the FIRST TENANT'S job id -- a cross-tenant disclosure through an error
    message.

    AND IT ENDS IN RANDOM BYTES RATHER THAN ONLY A TIMESTAMP, which is a MEASURED
    fix. `worker.start_run` uses `int(time.time())`, whole seconds, which is
    correct for a human typing one command; two API submissions arrive far closer
    together than that. Measured with millisecond precision and no random suffix,
    `test_two_submissions_without_a_key_are_two_runs` failed:

        ValueError: job for run 'pending-tenant-alpha-7-1787910797807' stage
        'plan' attempt 1 is already queued as 70a9d75a-... (status 'ready')

    So two legitimate keyless submissions of one ticket became a 500. Any clock
    resolution has this bug -- it only moves the window -- and the caller cannot
    avoid it, because the collision is between two requests they made deliberately.
    The timestamp STAYS because it makes a placeholder sortable and legible in a
    log; the random suffix is what makes it unique.
    """
    safe_ticket = "".join(c for c in ticket_id if c.isalnum() or c in "-_") or "run"
    safe_tenant = "".join(c for c in tenant_id if c.isalnum() or c in "-_") or "t"
    unique = secrets.token_hex(4)
    candidate = f"pending-{safe_tenant}-{safe_ticket}-{int(time.time())}-{unique}"
    if not log.is_safe_run_id(candidate):
        # Reachable through a long tenant id plus a long ticket id: MAX_TICKET_ID
        # is 200 and `log.MAX_RUN_ID_LENGTH` is smaller, so the sum can exceed it.
        # Truncating silently would produce a placeholder that collides with
        # another long ticket's, so it is a 422 naming the field instead.
        raise Unprocessable(
            "ticket_id is too long to build a run id from once the tenant is "
            "included; shorten it. The value is not echoed: it is untrusted "
            "input and this message can reach a rendered page."
        )
    return candidate


def submit_run(
    credential: Credential,
    submission: RunSubmission,
    *,
    idempotency_key: str = "",
    connection: object = None,
) -> tuple[RunStatus, bool]:
    """K1: accept a ticket, enqueue its `plan` stage, return an id.

    Returns `(status, replayed)`. `replayed` is True when an idempotency key
    matched an earlier submission, and the transport puts it on the response --
    a client that retried needs to know it did not start something new, and a
    200 that looked identical either way would hide it.

    THE ORDER IS: scope, then budget, then idempotency, then enqueue. Each step
    is before the one that costs something:

      * the scope check costs nothing and refuses the wrong credential first;
      * the budget check is `budgets.check`'s documented "before a run starts";
      * the idempotency lookup is before the enqueue, because an enqueue is the
        thing being deduplicated;
      * the record is written AFTER the enqueue succeeds. Written before, a
        failed enqueue would leave a record naming a job that does not exist, and
        every retry would then replay a run nobody can watch.
    """
    credential.require(SCOPE_RUNS_WRITE)
    tenant_id = _tenant(credential)

    if connection is not None:
        # `would_spend_cents=0` because nothing here can predict a run's cost --
        # `agentorg/cost/` prices a run from tokens it has already consumed. So
        # this asks the honest question a control plane can ask before a run:
        # does this tenant have a budget at all, and is it already over? A made
        # up estimate would be a number with no measurement behind it, which
        # rule 4 of CLAUDE.md forbids in prose and which is worse in a gate.
        decision = budgets.check(_BudgetScope(connection, tenant_id), 0)
        if not decision.allowed:
            raise Forbidden(decision.explain())

    store = idempotency_store()
    if idempotency_key:
        existing = store.get(tenant_id, idempotency_key)
        if existing is not None:
            job = queue.get(existing.job_id)
            if job is None:
                # The record names a job the queue does not have. Possible only
                # if the queue was reset under us. Refusing is the honest answer:
                # replaying a run nobody can watch is the "green response meaning
                # the check did not run" shape.
                raise Conflict(
                    "an earlier submission with this idempotency key recorded a "
                    "job the queue no longer has, so its run cannot be reported. "
                    "Retry with a new idempotency key."
                )
            return _status_from_jobs(job.run_id, tenant_id, [job]), True

    run_id = _placeholder_run_id(tenant_id, submission.ticket_id)
    job = queue.enqueue(
        run_id,
        "plan",
        tenant_id=tenant_id,
        ticket_id=submission.ticket_id,
        ticket_text=submission.ticket_text,
        trigger=submission.trigger,
        poisoned=submission.poisoned,
    )

    if idempotency_key:
        store.put(
            tenant_id,
            idempotency_key,
            Record(job_id=job.job_id, run_id=job.run_id,
                   ticket_id=submission.ticket_id),
        )

    return _status_from_jobs(job.run_id, tenant_id, [job]), False


class _BudgetScope:
    """The two fields `budgets.check` reads: a connection and a tenant id.

    Deliberately NOT `tenancy.accessors.TenantScope`, following
    `tests/test_tenancy_budgets.py`'s own `_Scope`, which says why: this module
    needs budget arithmetic and not the accessor module's constructor, and
    coupling to it would mean a change there breaks a route about neither.
    """

    def __init__(self, connection: object, tenant_id: str) -> None:
        self.connection = connection
        self.tenant_id = tenant_id


def _jobs_for_tenant(run_id: str, tenant_id: str) -> list[queue.Job]:
    """Every job for a run, refusing one that belongs to another tenant.

    THE TENANT CHECK IS HERE AND NOT IN THE ROUTE, so both `run_status` and
    `cancel_run` get it from one place. A per-route check is a check one route can
    be written without.

    A run whose jobs carry a DIFFERENT tenant raises `Forbidden`; a run with no
    jobs raises `NotFound`. That split is the one `tests/test_tenancy_leak.py`
    documents: a run id is an unguessable uuid, so "not yours" reveals nothing the
    caller did not supply, and answering `404` for it would make a legitimate
    caller's typo indistinguishable from someone else's live run.

    A job with a BLANK `tenant_id` is treated as tenant zero's, through the same
    `for_run_state` translation everything else uses. Runs enqueued by
    `scripts/worker.py --start` carry no tenant, and reading a blank as "belongs
    to nobody" would make every pre-API run invisible to the API -- while reading
    it as "belongs to whoever asks" would be the leak. Translated, it belongs to
    exactly one tenant.
    """
    jobs = queue.jobs_for_run(run_id)
    if not jobs:
        raise NotFound(f"no run {run_id!r} is known to the queue")
    owners = {tenant_zero.for_run_state(job.tenant_id) for job in jobs}
    if owners != {tenant_id}:
        raise Forbidden("that run belongs to another tenant")
    return jobs


def _status_from_jobs(run_id: str, tenant_id: str, jobs: list[queue.Job]) -> RunStatus:
    """Fold a run's jobs into one status. The LAST job decides the headline.

    The last row is the run's current position, and `queue.jobs_for_run` returns
    them oldest first. Deliberately not "the first non-terminal", which would
    report a run as running after it ended, and not a derived summary word of our
    own -- `exit_codes.status_for` already turned five exit codes into five
    statuses and inventing a sixth vocabulary here is how the poisoned run's
    `blocked` becomes indistinguishable from a crash on a surface a judge reads.
    """
    last = jobs[-1]
    return RunStatus(
        run_id=run_id,
        tenant_id=tenant_id,
        status=last.status,
        stage=last.stage,
        awaiting_gate=last.awaiting_gate,
        exit_code=last.exit_code,
        reclaimed=any(job.reclaimed_from for job in jobs),
        stages=[
            {
                "stage": job.stage,
                "status": job.status,
                "attempt": job.attempt,
                "exit_code": job.exit_code,
                "enqueued_at": job.enqueued_at,
                "updated_at": job.updated_at,
                "reclaimed_from": job.reclaimed_from,
            }
            for job in jobs
        ],
    )


def run_status(credential: Credential, run_id: str) -> RunStatus:
    """K2's read: where is this run, and what has it done.

    THE RUN ID IS VALIDATED BEFORE IT IS USED, through `log.is_safe_run_id` --
    the same positive test for "one safe path component" that `queue.enqueue` and
    `approve_server` apply. It arrives from a URL path here, so it is the least
    trusted string in this module, and `gates._state_path` does no containment
    check of its own.
    """
    credential.require(SCOPE_RUNS_READ)
    tenant_id = _tenant(credential)
    if not log.is_safe_run_id(run_id):
        raise NotFound("that is not a usable run id")
    jobs = _jobs_for_tenant(run_id, tenant_id)
    return _status_from_jobs(run_id, tenant_id, jobs)


def cancel_run(credential: Credential, run_id: str, *, reason: str = "") -> RunStatus:
    """K2's write, and the ONLY terminal transition this API can drive.

    A cancel ends a run. It cannot advance one, cannot approve a gate, and cannot
    turn a `blocked` run into anything else -- `queue.complete` refuses to
    overwrite a terminal status underneath, and that refusal is surfaced as a 409
    rather than absorbed.

    WHAT IS AND IS NOT GUARANTEED MID-RUN, stated precisely because "cancellation
    is honoured mid-run" is K7's second half and the honest answer has an edge:

      * A run PAUSED at a gate is cancelled completely. The job is a durable row
        and nothing is executing, so there is nothing to race. Verified: after
        the cancel, `queue.awaiting()` no longer lists it and `queue.claim`
        returns a different job.
      * A run whose next stage is READY is cancelled before that stage runs. The
        job becomes terminal, and `queue.claim` skips terminal statuses, so no
        worker starts it.
      * A stage ALREADY CLAIMED AND EXECUTING is not killed. The subprocess
        `queue/runner.py` started keeps running to completion -- this is a
        control plane and it does not signal processes. What the cancel does
        guarantee is that the stage's result cannot advance the run: the job is
        already terminal, and `queue.complete` REFUSES the worker's later write
        with a ValueError rather than accepting it, so nothing further is
        enqueued. MEASURED: a second `complete` on a job already recorded
        `rejected` raises "already ended as 'rejected' with exit 4; refusing to
        record 'done'".

    So the guarantee is "no further stage runs", not "the current stage stops".
    Killing the subprocess would be the stronger promise and this module does not
    make it: a stage half-killed may have opened a PR and posted two comments,
    and `queue.fail`'s docstring already refuses to re-run such a stage for that
    exact reason. A cancel that left a PR open while reporting the run cancelled
    is a truthful status; a cancel that claimed the work was undone would not be.
    """
    credential.require(SCOPE_RUNS_WRITE)
    tenant_id = _tenant(credential)
    if not log.is_safe_run_id(run_id):
        raise NotFound("that is not a usable run id")

    jobs = _jobs_for_tenant(run_id, tenant_id)
    live = [job for job in jobs if job.status not in queue.TERMINAL_STATUSES]
    if not live:
        # Every job has ended. Answering 200 here would report a cancellation
        # that did not happen, which is the shape this project exists to prevent
        # -- and a caller retrying a cancel after a run blocked needs to be able
        # to tell "already over" from "cancelled by me".
        raise Conflict(
            f"run {run_id} has already ended (its last stage {jobs[-1].stage} is "
            f"{jobs[-1].status}); there is nothing left to cancel"
        )

    for job in live:
        # `rejected` with `EXIT_REFUSED`'s code, read off the table rather than
        # written as `4`. A cancel is a refusal by a caller, which is the same
        # class of ending as a human refusing a gate -- and `exit_codes.code_for`
        # inverts run_stage.py's own constants, so a hardcoded 4 here would be a
        # second declaration of a code this repository is careful about.
        queue.complete(
            job.job_id,
            status="rejected",
            exit_code=_rejected_exit_code(),
        )

    cancelled = queue.jobs_for_run(run_id)
    if reason:
        # LOGGED, NOT WRITTEN INTO THE RUN'S STATE. `gates.py` is the one writer
        # of a `RunState`, and a control plane that appended a decision would be
        # the second -- the defect `worker.approve` measured, where writing at
        # both layers recorded one click as two decisions, "the second attributed
        # to a reviewer who does not exist on this path".
        #
        # The logger is fetched INLINE, per CLAUDE.md: ruff's BLE001 cannot
        # resolve a module-level alias, so `_log.info(...)` turns the lint gate
        # red. The reason is not echoed into the log at full length for the same
        # reason it is not echoed into an error message -- it is caller-supplied.
        import logging

        logging.getLogger(__name__).info(
            "run %s cancelled by key %s (reason given, %d chars)",
            run_id, credential.key_id, len(reason),
        )
    return _status_from_jobs(run_id, tenant_id, cancelled)


def _rejected_exit_code() -> int:
    """`run_stage.py`'s code for a refusal, read through the queue's table.

    A function rather than a module constant so the import stays lazy: the table
    is built by loading `scripts/run_stage.py` through `spec_from_file_location`,
    and doing that at import time would make every `agentorg.api` import pay for
    it -- including in the five agent containers, which do not ship `scripts/`.
    """
    from ..queue import exit_codes

    return exit_codes.code_for("rejected")


# ── K3: repository configuration ──────────────────────────────────────────────
#
# Held in the same in-process shape as the key and idempotency stores, and the
# limit is the same one: it does not survive a restart. The durable home is
# `agentorg/db/schema.py`'s `repository` table, which is already tenant-scoped
# and already has a `full_name` unique per tenant -- Lane B's file, so it is named
# here rather than reached into.
_CONFIG: dict[tuple[str, str], RepositoryConfig] = {}


def _config_store() -> dict[tuple[str, str], RepositoryConfig]:
    """The process's config store, read through a function. See auth.key_store."""
    return _CONFIG


def read_config(credential: Credential, full_name: str) -> RepositoryConfig:
    """K3's read. The effective configuration, with the threshold resolved.

    An unconfigured repository returns the DEFAULTS rather than a 404, and the
    threshold in them is `scoring.resolve_threshold()`'s answer for "nobody
    asked" -- which reads `config.SECURITY_BLOCK_THRESHOLD`. So the value a
    caller sees is the value the pipeline would use, rather than a blank they
    have to interpret.
    """
    credential.require(SCOPE_CONFIG_READ)
    tenant_id = _tenant(credential)
    stored = _config_store().get((tenant_id, full_name))
    if stored is not None:
        return stored
    return RepositoryConfig(
        full_name=full_name,
        threshold=scoring.resolve_threshold(),
        checks=dict.fromkeys(OPTIONAL_CHECKS, True),
    )


def write_config(credential: Credential, config: RepositoryConfig) -> RepositoryConfig:
    """K3's write. Refuses a threshold `resolve_threshold` refuses.

    THE FLOOR IS NOT RE-IMPLEMENTED HERE. `resolve_threshold` raises for a value
    outside the vocabulary and for one above `THRESHOLD_FLOOR`, and this function
    lets that `ValueError` become a 422 carrying the refusal's own message. A
    local `if` comparing against `THRESHOLD_FLOOR` would be a second decision
    path whose only job is to agree with the first -- `scoring.score_findings`
    refuses to write `>=` for exactly that reason, and its comment applies: "an
    audit artifact that can disagree with the decision it describes is worse than
    none: it reads as proof."

    AND IT REFUSES TO DISABLE `security`. `OPTIONAL_CHECKS` names the two
    advisory checks; anything else in `checks` is a 422. A customer able to turn
    the scanners off would get a pipeline whose three human gates report that
    security passed, which is worse than a pipeline with no scanners at all.
    """
    credential.require(SCOPE_CONFIG_WRITE)
    tenant_id = _tenant(credential)

    try:
        resolved = scoring.resolve_threshold(config.threshold or None)
    except ValueError as refusal:
        raise Unprocessable(str(refusal)) from refusal

    unknown = sorted(set(config.checks) - set(OPTIONAL_CHECKS))
    if unknown:
        raise Unprocessable(
            f"these checks are not configurable: {', '.join(unknown)}. Only "
            f"{', '.join(OPTIONAL_CHECKS)} may be turned off, because they are "
            f"advisory. The security verdict is the one binding check in the "
            f"pipeline and there is no configuration that disables it -- a "
            f"credential could then reach main past three humans who were told a "
            f"scanner had cleared it."
        )

    effective = RepositoryConfig(
        full_name=config.full_name,
        threshold=resolved,
        checks={name: bool(config.checks.get(name, True)) for name in OPTIONAL_CHECKS},
    )
    _config_store()[(tenant_id, config.full_name)] = effective
    return effective


def reset() -> None:
    """Drop the config store. For tests, and for tests only -- see queue.reset."""
    _CONFIG.clear()
