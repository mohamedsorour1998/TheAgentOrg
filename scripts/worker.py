"""The worker: claim -> run one stage -> record -> re-enqueue or pause.

OWNER: Lane A, tasks A5, A6, A8.

    python scripts/worker.py --once                 # claim one job, run it, stop
    python scripts/worker.py                        # loop until the queue is empty
    python scripts/worker.py --start POISON-1 "..." --poisoned   # enqueue a run
    python scripts/worker.py --list                 # what is waiting for a human
    python scripts/worker.py --approve <run> gate1  # release a gate
    python scripts/worker.py --reject  <run> gate1  # refuse a gate

WHAT THIS REPLACES
==================
`.github/workflows/run-pipeline.yml`: seven jobs, three rejection recorders, six
artifact upload/download pairs, and three GitHub Environments. All of it, in one
loop, and the four things Actions was providing are provided here instead:

    sequencing            `needs:`                 -> `queue.next_stage`
    artifact handoff      upload/download-artifact -> `gates.save`/`gates.load`,
                                                      which already existed for
                                                      exactly this handoff
    pausing for approval  a GitHub Environment      -> a durable `paused` row
    per-job isolation     a fresh runner per job    -> a subprocess per stage

THE SEVEN JOBS COLLAPSE. THE THREE GATES DO NOT.
================================================
This is the one property of this lane that is not negotiable, so it is worth being
exact about what makes it true rather than asserting it.

A gate is not enforced by an `if:`, by a status check, or by anything in this file
that could be edited out in a one-line diff. It is enforced by the queue's `claim`,
which will not hand out a job whose status is `paused`, and by `resume` being the
only transition out of that status -- a function that cannot be called without a
`HumanDecision`'s decision string. There is no `--force`, no lease timeout on a
pause, and no sweeper. A worker that wanted to skip a gate would have to write to
the queue's storage directly.

`--auto-approve` IS NOT ACCEPTED BY THIS SCRIPT AT ALL, and that is deliberate.
`run-pipeline.yml` has it, and there it is harmless: an Environment is a repository
setting and no workflow content can argue with it, so the flag changes only the `by`
attribution. Here there is no Environment. A `--auto-approve` would be the whole
gate, so the flag does not exist -- see `queue/runner.run_stage`, which hardcodes
`--auto-approve false`.

WHAT IT DELIBERATELY DOES NOT DO
================================
It does not re-implement the pipeline, and it does not know what a stage means. It
runs `scripts/run_stage.py <stage>` -- the same command, the same bytes, the same
argparse the Actions job runs -- and reads the exit code. Every decision about what
a stage DOES stays in that file, and every decision about what an exit code MEANS
stays in `queue/exit_codes.py`. This file's whole job is: which stage next, and is
this run allowed to advance.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import socket
import sys
import time

# THE REPOSITORY ROOT GOES ON `sys.path` BEFORE `agentorg` IS IMPORTED, AND THIS IS
# NOT BOILERPLATE -- IT IS A MEASURED FAILURE
# ============================================================================
# `python scripts/worker.py` puts `scripts/` on `sys.path[0]`; the repository root
# never gets there. `import agentorg` then resolves through whatever finder answers
# first, and with an EDITABLE INSTALL that is the install's mapping -- which points
# at the checkout `pip install -e` was run from, not necessarily this one. MEASURED
# from a git worktree of this repository, with no PYTHONPATH set:
#
#     ImportError: cannot import name 'queue' from 'agentorg'
#                  (/Users/sorour/sorour/TheAgentOrg/agentorg/__init__.py)
#
# The path in that message is the SHARED checkout. `agentorg.queue` exists in the
# tree this file lives in and not in the tree Python found, so the worker could not
# start at all -- and the failure names a missing submodule, which reads as an
# incomplete package rather than as the wrong package.
#
# The same cause as commit cf5cb83 (`tests/test_trigger_provenance.py`, the only
# failing test in every one of this phase's fourteen lane worktrees, misdiagnosed by
# three lanes) and as the PYTHONPATH note in `queue/runner.py`. Three callers, one
# root cause: a script that imports `agentorg` must put the root on the path itself.
#
# `insert(0, ...)`, so THIS tree wins over the editable install's mapping rather
# than merely being available after it. The `not in` guard keeps `sys.path` honest
# if this module is ever imported as well as run.
#
# NO LINT SUPPRESSION ON THE IMPORTS BELOW, and none is needed -- MEASURED. E402
# (module-level import not at top of file) is what a reader expects here, and ruff
# does not raise it: `ruff check --isolated --select=E402 scripts/worker.py` reports
# `All checks passed!`, because ruff recognises a `sys.path` manipulation as a
# legitimate reason for imports to follow it. That matters because CLAUDE.md forbids
# suppression comments outright, so an unnecessary one here would be a lint
# violation in its own right.
#
# Do not spell the suppression pragma out in a comment on this file either, even to
# explain that it is unnecessary: ruff scans for that token in ANY comment, not only
# on a code line, and answers `Invalid ... directive` for the prose and then
# `Remove unused ... directive` once the syntax is valid. Measured, twice, while
# writing this note.
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agentorg import gates, graph, queue
from agentorg.queue import exit_codes, runner

# How long a worker sleeps when the queue is empty. Short, because a queue that is
# empty for a second is the normal state between a `plan` finishing and its
# successor being claimed, and a demo watching a worker idle for thirty seconds
# reads as a hang. Not a knob: nothing here is worth a knob, and CLAUDE.md's
# config.py has 20 already.
_POLL_SECONDS = 0.25

# The per-stage process ceiling, in seconds. `develop` is the long one -- the
# developer/reviewer loop plus the security scan, with `agent_client`'s data-plane
# read timeout at 300s PER INVOCATION and up to four revision passes -- so this is
# deliberately above the queue's own 600s lease rather than under it. A timeout
# shorter than the lease would kill a stage the queue still believes is running,
# which reads as a crash rather than as a limit.
#
# It exists at all for `SCANNER_TIMEOUT_SECONDS`' reason: a hung stage is worse than
# a crashed one, because on a projector it is indistinguishable from a freeze.
_STAGE_TIMEOUT_SECONDS = 900


def _worker_name() -> str:
    """This worker's identity: host and pid.

    Two processes on one host must not share a name -- the whole of A8 is
    distinguishing "this job is mine" from "this job's previous owner is gone", and
    two workers answering to one name makes a reclaim unattributable and a
    self-reclaim invisible.
    """
    return f"{socket.gethostname()}/{os.getpid()}"


def _log(message: str) -> None:
    """One line, flushed.

    `flush=True` because a worker's stdout is a pipe when it is watched through
    `tee` or a container log, and Python line-buffers to a terminal but BLOCK
    buffers to a pipe. Without it a worker that is working looks like a worker that
    has hung -- which on a projector is the one thing that outranks polish.
    """
    print(f"[worker] {message}", flush=True)


def _already_ran(job: queue.Job) -> bool:
    """Whether this job's stage has already run once, on the evidence.

    ─────────────────────────────────────────────────────────────────────────
    THIS IS A6, AND IT IS THE HALF THE QUEUE CANNOT DO ALONE
    ─────────────────────────────────────────────────────────────────────────

    `queue.claim` refuses to hand one job to two workers at the same time, and the
    UNIQUE index refuses two jobs for one (run_id, stage, attempt). Those close the
    double-CLAIM. They do not close the double-RUN, because of one case that no
    queue can rule out: a lease that expired while its worker was alive but wedged.
    The reclaiming worker is then the second worker to run that stage.

    `agent_client.py:107-110` states the cost precisely, and it is not theoretical:

        An agent invocation is not idempotent: it writes a PR comment and burns
        model tokens, so a silent botocore retry of a call that actually succeeded
        would double both.

    A re-run `develop` posts every develop and review comment a second time and pays
    for every model call again. A re-run `promote` calls `merge_pr` on a PR that is
    already merged.

    SO THE EVIDENCE IS READ RATHER THAN ASSUMED. `queue.claim` records
    `reclaimed_from` when it takes a job from an expired lease -- the only trace
    anywhere that a stage may have run twice -- and this function, for such a job
    only, asks the RUN whether the stage's work is already on the record.

    THE RUN STATE IS THE ANSWER, NOT THE QUEUE. That is the load-bearing choice.
    The queue knows a lease expired; only the run knows whether the stage finished
    its work before it did. `gates.load` reads the state the stage would have
    written, and `_STAGE_EVIDENCE` below says which field each stage fills. A stage
    that got far enough to write its result is not run again.

    WHY THIS ERRS TOWARD REFUSING TO RE-RUN, stated because it is a real trade. If
    the state shows the evidence, this returns True and the stage is skipped -- even
    though the stage might have written its result and then crashed before doing
    something after it. The alternative is running it again, which posts duplicate
    comments and pays twice. Between "a stage may have been skipped after doing its
    work" and "an agent may have been invoked twice", the first fails loudly on the
    next stage (which finds a state it cannot use) and the second fails silently
    while looking correct. This repository's whole discipline is to prefer the loud
    one.
    """
    if not job.reclaimed_from:
        return False
    field = _STAGE_EVIDENCE.get(job.stage)
    if field is None:
        # A gate or recorder stage. It records a decision rather than a result, and
        # `gates.resume` appends to a list -- so a re-run appends a SECOND identical
        # decision rather than overwriting one. That is visible and harmless
        # (`not_promotable` reads the decisions rather than counting them), and
        # there is no result field to read, so there is nothing to check.
        return False
    try:
        state = gates.load(job.run_id)
    except FileNotFoundError:
        # No state at all means the stage did not get as far as saving one, so it
        # did not run. Not an error here: `plan` legitimately has no prior state.
        return False
    return getattr(state, field, None) is not None


# Which RunState field proves a stage did its work. Only the stages that produce a
# result appear; a gate produces a decision, which is a different shape and is
# handled above.
#
# `develop` -> `security`, deliberately, and not `dev`. That stage does four things
# in order -- the developer/reviewer loop, `open_pr`, then the security verdict --
# and `state.dev` is filled by the FIRST of them. Keying on `dev` would skip a
# reclaimed `develop` that had produced a diff and then died before the scanners
# ran, which is a run whose security verdict never happened being treated as
# complete. `security` is the LAST thing that stage writes, so it is the only field
# whose presence means the whole stage finished.
_STAGE_EVIDENCE = {
    "plan": "plan",
    "develop": "security",
    "sre": "sre",
}


def _poisoned_for(job: queue.Job) -> bool:
    """Whether this stage should run poisoned. READ OFF THE RUN, not off argv.

    ─────────────────────────────────────────────────────────────────────────
    MEASURED DEFECT. The first version of this worker passed `poisoned` straight
    from its own `--poisoned` flag to every stage, and the poisoned run DID NOT
    BLOCK:

        [worker] running develop for run 70a4652b-...
        [worker] run 70a4652b-... is PAUSED at gate2
        state.poisoned = True
        security verdict = pass | blocking = 0

    A poisoned run reaching gate2 with a `pass` verdict is the demo's central
    claim failing silently -- every job green, the state correctly recording
    `poisoned=True`, and the diff carrying no key.
    ─────────────────────────────────────────────────────────────────────────

    THE CAUSE IS THE TRAP `state.py:229-240` ALREADY DOCUMENTS, one layer out.
    `developer.run(state, poisoned=None)` defaults to `None`, deliberately, and
    that file says why: "`None` means 'nobody said', so the field decides. `False`
    means a caller explicitly asked for a clean run and must be able to override a
    poisoned state."

    A worker invoked WITHOUT `--poisoned` -- which is what continuing a run looks
    like, since the flag was typed once at `--start` and the process has since
    exited -- turned "nobody said" into an explicit `False`. `run_stage.py`'s
    `--poisoned` defaults to `"false"`, and `_stage_develop` passes
    `flag(args.poisoned)` on as a real bool, so the override reached the developer
    and cancelled the poisoning the state was carrying.

    ON ACTIONS THIS CANNOT HAPPEN, because `run-pipeline.yml` interpolates
    `inputs.poisoned` into every job from one workflow-level input -- there is no
    second invocation to forget it. A queue has exactly that second invocation, by
    design: a run is claimed by a different process each stage, possibly on a
    different host, minutes or hours later. So the value has to come off the run.

    `plan` IS THE EXCEPTION and takes the JOB's value, because `plan` is the stage
    that CREATES the RunState and writes `poisoned` into it. There is nothing to
    read yet -- so the choice travels on the queue row (`Job.poisoned`), which is
    the same "chosen in one process, needed in another" fix. Every stage after it
    reads what plan recorded.
    """
    if job.stage == "plan":
        return job.poisoned
    try:
        return gates.load(job.run_id).poisoned
    except FileNotFoundError:
        # No state to read. `_load` inside the stage will refuse this job loudly
        # anyway; returning the job's own value rather than False keeps a
        # `--start --poisoned` that somehow lost its state from silently going clean.
        return job.poisoned


def run_one(*, worker: str = "") -> queue.Job | None:
    """Claim one job, run it, record what happened. Returns the job, or None.

    IT TAKES NO RUN PARAMETERS, AND THAT IS THE FIX FOR A MEASURED DEFECT. It used
    to accept `ticket_id`, `ticket_text`, `poisoned` and `trigger` and pass them to
    whatever it claimed -- which is correct only while one process runs a whole
    pipeline. MEASURED across two processes, one enqueuing and a bare worker next:

        run_stage: error: plan needs --ticket-id and --ticket-text
        [worker] the plan stage printed no run_id; nothing downstream can find
                 its state

    The ticket text existed only in the argv of a process that had exited. So the
    inputs live on the JOB (`Job.ticket_id`, `ticket_text`, `trigger`, `poisoned`)
    and are read from what was claimed. A worker now needs no arguments to run
    anybody's run, which is what makes a fleet of them possible at all.

    THE FIVE OUTCOMES, and the fact that they are five rather than two is A7:

      done           the stage completed -> its successor is enqueued, or the run
                     is over because `promote` was the last stage
      paused         the successor is a GATE -> enqueued and immediately paused,
                     so nothing claims it until a human resumes
      blocked        exit 3, THE DETERMINISTIC BLOCK RULE. Nothing is enqueued: the
                     run is over, and this is the pipeline working
      rejected       exit 4, a human refused or a cap was spent. Nothing enqueued
      failed         exit 1 or an unrecognised code. Nothing enqueued, and NOT
                     re-tried -- see `queue.fail`
    """
    worker = worker or _worker_name()
    job = queue.claim(worker)
    if job is None:
        return None

    if job.reclaimed_from:
        _log(f"job {job.job_id[:8]} {job.stage} was RECLAIMED from "
             f"{job.reclaimed_from} (its lease expired). This is the only case "
             f"where a stage could run twice; checking the run's own record.")

    if _already_ran(job):
        # Recorded as `already_final`, reusing the status for the reason
        # `run_stage.py:149-155` reuses the exit code: this worker was asked to
        # redo work that had already happened and declined. It did not crash, it
        # was not blocked, and no human refused it.
        #
        # THE EXIT CODE IS READ OFF `run_stage.py` RATHER THAN WRITTEN AS `5`,
        # through the same table `exit_codes` builds. No stage process ran here, so
        # there is no code to report -- but the job needs one, and a hardcoded 5
        # would be a second declaration of `EXIT_ALREADY_FINAL` in a file that
        # imports the first. `exit_codes.code_for` inverts the table so the two
        # cannot drift.
        _log(f"job {job.job_id[:8]} {job.stage} already did its work on run "
             f"{job.run_id}; refusing to run it again. Re-running would invoke "
             f"the agent a second time -- a duplicate PR comment and a second "
             f"model bill.")
        queue.complete(job.job_id, status="already_final",
                       exit_code=exit_codes.code_for("already_final"))
        return queue.get(job.job_id)

    _log(f"running {job.stage} for run {job.run_id or '(new)'} "
         f"[job {job.job_id[:8]}, worker {worker}]")

    try:
        outcome = runner.run_stage(
            job, timeout=_STAGE_TIMEOUT_SECONDS,
            # ALL FOUR OFF THE JOB. See this function's docstring for the measured
            # run where they came from argv and a restart lost the ticket entirely,
            # and `_poisoned_for` for the one where a poisoned run reached gate2
            # with a `pass` verdict.
            ticket_id=job.ticket_id,
            ticket_text=job.ticket_text,
            poisoned=_poisoned_for(job),
            trigger=job.trigger,
            # WHO DECIDED AT THE GATE. Empty for every non-gate stage, and
            # `run_stage.py`'s `--approver` already defaults to the Environment's
            # name for that case. Without this a queued approval records
            # `github-environment-reviewer`, naming a GitHub Environment that never
            # held this job, on the field whose whole purpose is attribution.
            approver=job.decided_by,
        )
    except Exception as crash:
        # A CRASH IN THE RUNNER, not in the stage: a timeout, or a subprocess that
        # could not be started. Recorded as `failed` with exit 1, which is what an
        # uncaught exception exits with anyway, so the two read the same -- which
        # is correct, because they are the same fact.
        #
        # `except Exception` is broad on purpose and the logger is fetched INLINE:
        # CLAUDE.md records that ruff's BLE001 cannot resolve a module-level alias,
        # so `_log.exception(...)` turns `ruff check agentorg` red -- and that
        # narrowing the except satisfies the rule with NO logging at all, which is
        # the worse option.
        import logging

        logging.getLogger(__name__).exception(
            "the %s stage could not be run for job %s", job.stage, job.job_id
        )
        _log(f"FAILED to run {job.stage}: {type(crash).__name__}: {crash}")
        queue.fail(job.job_id, exit_code=1)
        return queue.get(job.job_id)

    # The stage's own output, verbatim. A worker that summarised it would hide the
    # two lines that matter -- `_source=` and the block's finding list -- on the
    # surface a judge reads.
    if outcome.stdout.strip():
        print(outcome.stdout, end="" if outcome.stdout.endswith("\n") else "\n",
              flush=True)
    if outcome.exit_code != 0 and outcome.stderr.strip():
        print(outcome.stderr, end="" if outcome.stderr.endswith("\n") else "\n",
              file=sys.stderr, flush=True)

    # THE RUN ID COMES BACK FROM `plan`, and nothing else knows it. Same problem
    # `run-pipeline.yml` solves by grepping `^run_id=` out of the job log, and the
    # same guard: an empty value is a named failure, not a silently empty
    # `--run-id` handed to the next stage.
    if job.stage == "plan":
        if not outcome.run_id:
            _log("the plan stage printed no run_id; nothing downstream can find "
                 "its state, so this run stops here rather than continuing with "
                 "an empty --run-id.")
            queue.fail(job.job_id, exit_code=1)
            return queue.get(job.job_id)
        job = _adopt_run_id(job, outcome.run_id)

    status = exit_codes.status_for(outcome.exit_code)
    if exit_codes.unclassified_exit(outcome.exit_code):
        # SAID OUT LOUD rather than absorbed into `failed`. `1` lands here and is a
        # crash; a future code with no table entry also lands here and is a bug in
        # `queue/exit_codes.py`. Both deserve naming -- see that module on why a
        # classifier that guesses is worse than one that admits it did not know.
        _log(f"exit {outcome.exit_code} has no meaning in run_stage.py's "
             f"vocabulary, so it is recorded as `failed` rather than guessed into "
             f"the nearest neighbour.")

    queue.complete(job.job_id, status=status, exit_code=outcome.exit_code)

    if status != "done":
        _log(f"{job.stage} ended the run: status={status} (exit "
             f"{outcome.exit_code}). Nothing further is enqueued.")
        return queue.get(job.job_id)

    _enqueue_successor(job)
    return queue.get(job.job_id)


def _adopt_run_id(job: queue.Job, run_id: str) -> queue.Job:
    """Record the run id `plan` generated onto the job that generated it.

    One line, because the work is `queue.adopt_run_id`'s -- see there for why a
    new row is not created, and why this used to reach through two layers of
    backend privates and branch on the backend's type.
    """
    updated = queue.adopt_run_id(job.job_id, run_id)
    _log(f"plan created run {run_id}")
    return updated


def _enqueue_successor(job: queue.Job) -> None:
    """Enqueue the stage after this one -- PAUSED if it is a gate.

    THE PAUSE IS WRITTEN HERE, BY THE STAGE BEFORE THE GATE, and that placement is
    inherited rather than invented. `run_stage.py:856-867` records the reason:
    `approve_server._awaiting` lists a run as waiting iff it has an open pause
    marker for a gate with no decision yet, and in the cloud the gate job does not
    start until somebody has already clicked -- so a pause written by the gate
    stage itself would write the marker and the decision in the same job, and the
    run would never appear on the screen.

    The same logic holds here for a different reason: the job must be unclaimable
    from the moment it exists. Enqueuing it `ready` and pausing it on the next line
    leaves a window in which a second worker can claim and run it, which is a gate
    that did not hold. So `status` and `awaiting_gate` are passed to `enqueue`
    itself -- the row is born paused.

    THE RUN'S INPUTS ARE COPIED ONTO THE SUCCESSOR, and that is what makes the
    chain survive a restart. They are not re-derived and not read from argv: the
    claimed job carries them, so the next one does too. A successor enqueued
    without them would be a `plan`-shaped hole three stages later -- and for
    `poisoned` specifically, `_poisoned_for` reads the run's state rather than this
    field for every stage after `plan`, so the copy is belt to that brace rather
    than the only record.

    A RECORDER STAGE HAS NO SUCCESSOR, and `queue.next_stage` raises on one rather
    than returning `""`. That is correct: a recorder records an ending, so reaching
    here with one would mean the pipeline continued past a refusal. It is caught
    rather than allowed to raise into the worker loop, because a run that ended is
    not a worker fault.
    """
    if job.stage in queue.REJECTION_STAGES.values():
        _log(f"{job.stage} recorded a refusal; the run stops here.")
        return

    successor = queue.next_stage(job.stage)
    if not successor:
        _log(f"{job.stage} was the last stage; run {job.run_id} is complete.")
        return

    carried = {
        "tenant_id": job.tenant_id,
        "ticket_id": job.ticket_id,
        "ticket_text": job.ticket_text,
        "trigger": job.trigger,
        "poisoned": job.poisoned,
    }

    if successor in queue.GATES:
        queue.enqueue(job.run_id, successor, status="paused",
                      awaiting_gate=successor, **carried)
        _log(f"run {job.run_id} is PAUSED at {successor}, awaiting a human. "
             f"Release it with:  python scripts/worker.py --approve "
             f"{job.run_id} {successor}")
        return

    queue.enqueue(job.run_id, successor, **carried)
    _log(f"enqueued {successor} for run {job.run_id}")


def start_run(ticket_id: str, ticket_text: str, *, poisoned: bool = False,
              trigger: str = "manual") -> queue.Job:
    """Put a new run's `plan` stage on the queue, with its inputs on the row.

    The run id is a PLACEHOLDER (`pending-<ticket>`) because the real one does not
    exist until `plan` runs -- `RunState.run_id` is a `default_factory` uuid, and
    generating one here and handing it to the stage would mean two places minting
    run ids. `_adopt_run_id` corrects the row afterwards.

    The placeholder still passes `log.is_safe_run_id`, deliberately: it is a real
    value in a real column and every id in this system is validated, including the
    ones it invents. `ticket_id` is included so two pending runs do not collide on
    the UNIQUE index, and it is sanitised for exactly that reason -- a ticket id
    like `a/b` would otherwise make an unsafe run id out of a legitimate ticket.

    THE TICKET TEXT GOES ON THE ROW, and it is what makes this function usable from
    a process that is not the one that will run the stage. MEASURED before it did:
    a `start_run` in one process and a bare worker in the next produced
    `run_stage: error: plan needs --ticket-id and --ticket-text`, because the text
    was in the first process's argv and that process had exited.
    """
    safe_ticket = "".join(ch for ch in ticket_id if ch.isalnum() or ch in "-_") or "run"
    return queue.enqueue(f"pending-{safe_ticket}-{int(time.time())}", "plan",
                         status="ready", ticket_id=ticket_id,
                         ticket_text=ticket_text, trigger=trigger,
                         poisoned=poisoned)


def approve(run_id: str, gate: str, *, by: str = "", reason: str = "") -> None:
    """Release a run past a gate. THE DECISION IS RECORDED BY THE GATE STAGE.

    ─────────────────────────────────────────────────────────────────────────
    MEASURED DEFECT. The first version of this function called `gates.resume`
    here, arguing that the decision had to be on disk before the job became
    claimable. It is, and the state then carried the decision TWICE:

        status: blocked
        decisions:
           gate1 approved by tester
           gate1 approved by github-environment-reviewer

    Two rows for one click, the second attributed to a reviewer who does not exist
    on this path -- `_stage_gate` hardcodes that `by` for the GitHub Environment it
    is named after. On a timeline a judge reads, one human decision renders as two.
    ─────────────────────────────────────────────────────────────────────────

    THE CAUSE was writing at both layers. `queue.resume` makes the gate's job
    claimable and the gate STAGE then runs -- and `_stage_gate`'s entire body is
    `gates.resume(...)` with `decision="approved"`. That stage is the recorder on
    Actions and it is still the recorder here; the queue's job is to decide WHEN it
    may run, not to duplicate WHAT it writes.

    So this function performs exactly one write, to the queue. `gates.py:37`'s "one
    writer" rule is the same principle: two modules writing one fact is how a
    single writer quietly becomes two.

    `by` AND `reason` ARE ACCEPTED AND FORWARDED, not dropped -- `--approver` is
    already `run_stage.py`'s argument for exactly this, and without it the queue's
    approvals would all read `github-environment-reviewer`, naming an Environment
    that never held this job. They travel to the stage, which is the one writer.
    """
    queue.resume(run_id, gate=gate, decision="approved",
                 approver=by or "queue-operator", reason=reason)
    _log(f"{gate} APPROVED for run {run_id} by {by or 'queue-operator'}; the "
         f"{gate} stage will record the decision when it runs.")


def reject(run_id: str, gate: str, *, by: str = "", reason: str = "") -> None:
    """Refuse a run at a gate. The gate's RECORDER stage records it.

    THE DECISION IS AN ARGUMENT HERE, WHICH IS THE WHOLE IMPROVEMENT OVER ACTIONS.
    `run-pipeline.yml` needs three separate recorder jobs whose `if:` conditions
    GUESS from `needs.<stage>.result` whether a human refused -- and measured on run
    32575709109 that guess posted `REJECTED by mohamedsorour1998` for a run that
    was merely cancelled, naming a person who never saw the gate. A third cause
    (`cancelled`) had to be excluded from three conditions to fix it.

    A queue has no such ambiguity. This function is called BY the refusal, so there
    is nothing to infer: `queue.resume` repoints the job at `<gate>-rejected` and
    the recorder runs because a human refused, not because a job did not run.

    ─────────────────────────────────────────────────────────────────────────
    MEASURED DEFECT, AND IT WAS WORSE THAN THE APPROVAL'S. This function also
    called `gates.resume` first, which sets `status="rejected"` on the run
    (gates.py:86). The recorder stage then loaded a run that was ALREADY TERMINAL
    and refused to record the refusal, by design:

        ::error::gate1 was not refused by a human -- this run already ended as
        status=rejected, so gate1 was skipped because the run stopped earlier,
        not because anybody rejected it. Refusing to overwrite that outcome.
        [worker] gate1-rejected ended the run: status=already_final (exit 5)

    So the refusal path exited 5 instead of 4, and the recorder -- whose entire
    reason to exist is preserving a human refusal -- refused the only refusal it
    would ever be handed. `_stage_gate_rejected`'s guard is CORRECT and is not the
    bug: it exists because a rejection recorder once overwrote a poisoned run's
    `status=blocked` (run 32509257195). This caller was tripping it by writing the
    terminal status the guard is there to protect.
    ─────────────────────────────────────────────────────────────────────────

    One write again, for `approve`'s reason. The recorder is the writer.
    """
    queue.resume(run_id, gate=gate, decision="rejected",
                 approver=by or "queue-operator", reason=reason)
    _log(f"{gate} REJECTED for run {run_id} by {by or 'queue-operator'}; "
         f"{queue.REJECTION_STAGES[gate]} will record it.")


def list_awaiting() -> list[queue.Job]:
    """Every run paused at a gate, printed. What an approval screen lists."""
    waiting = queue.awaiting()
    if not waiting:
        _log("nothing is awaiting a human decision.")
        return waiting
    _log(f"{len(waiting)} run(s) awaiting a human:")
    for job in waiting:
        print(f"  {job.run_id}  paused at {job.awaiting_gate}  "
              f"(since {job.updated_at})", flush=True)
    return waiting


def loop(*, once: bool = False, max_jobs: int = 0,
         idle_exit: bool = True) -> int:
    """Claim and run jobs until there is nothing claimable.

    IT TAKES NO RUN PARAMETERS. Every input a stage needs is on the job it claims
    -- see `run_one`. That is what lets one worker serve runs it did not start, and
    what makes a restart mid-pipeline a non-event.

    RETURNS THE LAST TERMINAL EXIT CODE, so a blocked run makes the WORKER exit 3.
    That is A7 reaching all the way out to the shell: `scripts/demo_poisoned.sh`
    and any CI step wrapping this see the same 3 that `run-pipeline.yml`'s
    `develop` job produces, and it is still not 1.

    `idle_exit` is what makes this usable in a script AND as a daemon. A demo runs
    until the queue is empty and stops; a long-lived worker polls forever. The
    default is the demo's, because that is the path that has to be reliable on a
    projector.

    IT DOES NOT RUN PAST A PAUSE, and nothing here enforces that -- `queue.claim`
    does, by refusing a paused job. So a `--start` followed by this loop stops at
    gate1 with the run intact and the process exiting 0, which is exactly what a
    gate should do to an unattended worker.
    """
    ran = 0
    last_exit = 0
    while True:
        job = run_one()
        if job is None:
            if idle_exit:
                waiting = queue.awaiting()
                if waiting:
                    _log(f"queue is empty except for {len(waiting)} run(s) paused "
                         f"at a gate. A human decides next; this worker is done.")
                else:
                    _log("queue is empty.")
                return last_exit
            time.sleep(_POLL_SECONDS)
            continue

        if job.exit_code:
            last_exit = job.exit_code
        ran += 1
        if once or (max_jobs and ran >= max_jobs):
            return last_exit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worker",
        description="Run queued pipeline stages. Replaces run-pipeline.yml.",
    )
    parser.add_argument("--start", nargs=2, metavar=("TICKET_ID", "TICKET_TEXT"),
                        help="enqueue a new run's plan stage")
    parser.add_argument("--poisoned", action="store_true",
                        help="run the poisoned ticket (the demo's block beat)")
    parser.add_argument("--trigger", default="manual",
                        help="how this run started: manual, issue, ...")
    parser.add_argument("--once", action="store_true",
                        help="claim and run exactly one job, then stop")
    parser.add_argument("--max-jobs", type=int, default=0,
                        help="stop after this many jobs (0 = until the queue drains)")
    parser.add_argument("--forever", action="store_true",
                        help="poll instead of exiting when the queue is empty")
    parser.add_argument("--list", action="store_true",
                        help="show every run awaiting a human decision")
    parser.add_argument("--approve", nargs=2, metavar=("RUN_ID", "GATE"),
                        help="record an approval and release the run")
    parser.add_argument("--reject", nargs=2, metavar=("RUN_ID", "GATE"),
                        help="record a refusal; the gate's recorder runs")
    parser.add_argument("--by", default="",
                        help="who decided, for the record")
    # NO --auto-approve. See this module's docstring: on the queue there is no
    # Environment to hold the job, so that flag would BE the gate.
    args = parser.parse_args(argv)

    if args.list:
        list_awaiting()
        return 0

    if args.approve:
        approve(args.approve[0], args.approve[1], by=args.by)
        return 0

    if args.reject:
        reject(args.reject[0], args.reject[1], by=args.by)
        return 0

    if args.start:
        ticket_id, ticket_text = args.start
        job = start_run(ticket_id, ticket_text, poisoned=args.poisoned,
                        trigger=args.trigger)
        _log(f"enqueued plan for ticket {ticket_id} (job {job.job_id[:8]}, "
             f"poisoned={args.poisoned})")

    return loop(once=args.once, max_jobs=args.max_jobs,
                idle_exit=not args.forever)


# THE QUEUE'S GATES AND THE GRAPH'S MUST BE THE SAME THREE, checked at import.
#
# A queue whose gate names drifted from `graph.not_promotable`'s would pause at
# gates the promote step does not require, or fail to pause at one it does -- and
# the second reads as a run that promoted past a gate nobody approved. Both
# failures are silent: every job green, one fewer human in the loop.
#
# `raise`, NOT `assert`, and the difference is load-bearing: `python -O` strips
# assert statements entirely, so an `assert` here is a guard that disappears under
# a flag anybody may set on a production worker. The one property this lane is not
# allowed to weaken must not be optional. (`agentorg/security/_run.py` and the rest
# of this repository's real guards all raise for the same reason; asserts in this
# codebase live only in tests.)
if set(queue.GATES) != set(graph.REQUIRED_GATES):
    raise RuntimeError(
        f"the queue pauses at {queue.GATES} but graph.not_promotable requires "
        f"{graph.REQUIRED_GATES}. A run would then promote past a gate nobody "
        f"approved, or pause at one nothing checks. Raised at import rather than "
        f"asserted, because `python -O` removes an assert and this is the one "
        f"property this file is not allowed to lose."
    )


if __name__ == "__main__":
    sys.exit(main())
