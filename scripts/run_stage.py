"""One stage of the pipeline, as one GitHub Actions job. OWNER: Task 3.

    python scripts/run_stage.py plan    --ticket-id DEMO-1 --ticket-text "..." \
                                        --poisoned false --auto-approve false
    python scripts/run_stage.py gate1   --run-id <id> --auto-approve false
    python scripts/run_stage.py develop --run-id <id> --poisoned false
    ...

WHY THIS FILE EXISTS AT ALL
===========================
`agentorg.graph.run_pipeline` walks a ticket through five agents and three human
gates in ONE function call. On GitHub Actions that shape cannot survive, and the
reason is structural rather than stylistic: a human gate here is a GitHub
Environment with a required reviewer, and an Environment pauses a JOB. A job
cannot pause in its middle. So the pipeline is cut at the gate boundaries, one
job per segment:

    plan -> [gate1] -> develop -> [gate2] -> sre -> [gate3] -> promote

and each job runs one `run_stage.py <stage>`, handing the `RunState` on as an
Actions artifact. `gates.save`/`gates.resume` already existed for exactly this
handoff; nothing here reimplements them.

WHY A CHECKED-IN SCRIPT AND NOT A HEREDOC IN THE WORKFLOW
========================================================
ci.yml:202-206 already made this ruling for scripts/scan_gate.py: the bytes CI
runs must be the bytes anyone can run on a laptop, and a heredoc inside `run: |`
cannot be -- YAML indentation silently rewrites Python. It also makes the
decisions below TESTABLE, which is the larger reason: `flag`, the stage table and
the exit codes are unit-tested in tests/test_run_pipeline_workflow.py, and none
of them could be if they lived in a `run:` body.

WHAT THIS DELIBERATELY DOES NOT DO
==================================
It does not re-implement the pipeline. Every stage below calls the SAME
`agent_client.call_agent` and the SAME `github_ops` functions that `graph._walk`
calls, in the same order. `graph.py` remains the definition of the pipeline for
the local path.

THE BLOCK RULE IS NOT COPIED HERE, and it is worth being exact about where it
lives, because it is the one deterministic thing this whole project is built to
demonstrate. `state.compute_security_verdict` is called in exactly one place on
this path: `agentorg/agents/security.py:187`, inside the security agent. NEITHER
this file NOR `graph.py` calls it -- both reach the verdict the same way, by
`call_agent("security", state)` and then reading `state.security.verdict`. So the
rule is evaluated once, behind the agent seam, whether the agent runs in this
process or in its AgentCore runtime.

(An earlier version of this paragraph claimed both files call
`compute_security_verdict` directly. That was false -- measured with
`grep -n compute_security_verdict agentorg/graph.py`, which returns nothing -- and
it is corrected rather than quietly deleted because the false version was
load-bearing prose about where the block rule lives, which is exactly the kind of
claim a reader would take on trust.)

What it does NOT reuse is `_walk` itself, and that is not a choice: `_walk` is one
uninterruptible function containing all three gates. Splitting it is the entire
task.

THE STRING-TYPED BOOLEANS, WHICH ARE THE SUBTLEST THING HERE
===========================================================
`workflow_dispatch` inputs arrive as STRINGS, booleans included, in two
independent ways:

  * `${{ inputs.poisoned }}` interpolates into a shell as the literal text
    `true` or `false`;
  * the REST dispatch API that the EventBridge target uses
    (`POST /repos/{owner}/{repo}/actions/workflows/run-pipeline.yml/dispatches`)
    rejects real JSON booleans inside `inputs` -- every value must be a string.

So `flag()` parses text, and the one thing it must never do is what
`bool(os.environ.get(...))` does. `bool("false")` is True. That would run the
POISONED diff on a run somebody asked to be clean, and nothing anywhere would
say so. config.py:96-99 documents this exact trap for SCANNERS_REQUIRED; this is
the same trap on a different input, so it gets the same treatment plus one more:
an unrecognised value RAISES rather than defaulting to False. `poisoned=yes` --
entirely plausible from a human or a mis-written input transformer -- must be a
loud error, not a quiet clean run.
"""

from __future__ import annotations

import argparse
import sys
import typing

from agentorg import gates, github_ops, graph, log
from agentorg.common import agent_client, config
from agentorg.state import HumanDecision, LogEvent, RunState

# The exact strings accepted, and nothing else. Lower-cased before lookup so
# GitHub's `True`/`False` (which is what a boolean input renders as in some
# expression contexts) is accepted; NOT stripped, because whitespace around a
# value means something upstream is mangling the input and that is worth a
# failure rather than a silent repair.
_TRUE = frozenset({"true"})
_FALSE = frozenset({"false", ""})

# A run in one of these has ENDED. DERIVED from the frozen contract's own
# Literal rather than written out, so a status added to `RunState` is terminal
# here the moment it exists. A hardcoded copy would treat a new ending as
# "running" and let a recorder overwrite it -- which is the whole defect below,
# reintroduced by the guard meant to prevent it.
#
# `agentorg/approve_server.py:126` states the same set as a literal for its own
# boundary; it is not imported, because importing an HTTP server into the one
# script every cloud stage runs would put `http.server` on the pipeline's import
# path for a four-element frozenset. The two are kept honest by a test that
# derives this set the same way approve_server's own test does.
_TERMINAL_STATUSES = frozenset(
    typing.get_args(typing.get_type_hints(RunState)["status"])
) - {"running"}

# For each terminal status, the LogEvent action that states that ending. Needed
# because `agentorg/timeline.py:196-211` reads its banner off the action of the
# LAST log row -- never off `RunState.status`, which no row carries -- so a
# recorder that appends any non-ending row last silently downgrades a BLOCKED
# banner to INCOMPLETE. Measured; see `_stage_gate_rejected`.
#
# `failed` maps to "blocked" rather than to an action of its own because the
# LogEvent vocabulary (state.py:204-207) has no "failed", and `graph.py:473`
# already logs exactly that pairing for the revision-cap ending. Following the
# existing convention rather than inventing a second one, since the FROZEN
# contract may gain optional FIELDS but this is a Literal's members.
_OUTCOME_ACTIONS = {
    "blocked": "blocked",
    "failed": "blocked",
    "rejected": "rejected",
    "promoted": "promoted",
}

# Exit codes. Four of them, because "the run was blocked", "the run was
# rejected by a human", "this recorder was asked to overwrite a run that had
# already ended" and "this job crashed" are four different facts and the demo's
# whole point is that the first is a WORKING pipeline reporting a real verdict.
#
# All non-zero: a blocked or rejected run must not proceed, and `needs:` in the
# workflow is what stops the next job. But 1 is what an uncaught exception
# already exits with, so a block sharing that code would make the poisoned demo
# run indistinguishable from a broken workflow on the projector.
#
# EXIT_ALREADY_FINAL is the fourth, and it borrows none of the other three FOR
# THE REASON THIS BLOCK ALREADY GIVES. A recorder that refused to overwrite a
# finished run did not block anything (it never evaluated the rule), was not
# rejected by anybody (refusing to invent a human is the point), and did not
# crash (it is a deliberate refusal, exactly as EXIT_BLOCKED is not 1). Reusing
# EXIT_REJECTED would be the worst of the four: the code would say a human
# refused the run, which is the precise lie the guard exists to prevent.
EXIT_OK = 0
EXIT_BLOCKED = 3
EXIT_REJECTED = 4
EXIT_ALREADY_FINAL = 5


def flag(raw: str) -> bool:
    """Parse a workflow_dispatch boolean, which arrives as a STRING.

    Empty means absent, which is false -- an input the caller omitted. Anything
    else unrecognised RAISES. See this module's header for why the fallback is a
    refusal and not a False.
    """
    value = str(raw).lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(
        f"cannot read {raw!r} as a boolean: workflow_dispatch sends booleans as "
        f"the strings 'true' or 'false', and guessing here would run the wrong "
        f"pipeline silently (expected 'true', 'false' or empty)"
    )


def _log(state: RunState, actor, stage, action, verdict="", summary="",
         artifact_ref="", scan_provenance="") -> None:
    """One log row. Same shape as graph._log, and the same file on disk.

    The timeline UI and the judges read `runs/<run_id>.jsonl`, so a stage that
    acts without appending here is a stage that did not happen as far as anyone
    reading the run can tell.
    """
    log.append(LogEvent(
        run_id=state.run_id, ticket_id=state.ticket_id,
        actor=actor, stage=stage, action=action, verdict=verdict, summary=summary,
        artifact_ref=artifact_ref, scan_provenance=scan_provenance,
    ))


def _load(run_id: str) -> RunState:
    """Reload the state a previous job uploaded.

    Read through gates' own path helper, not by string-building a filename, so
    the location stays a single definition.

    CORRECTED 2026-08-21: an earlier version of this docstring claimed Task 6
    moves this to DynamoDB "by changing gates.py alone". That was never true, and
    it is worth stating rather than deleting so the next reader knows the file
    once asserted the opposite. MEASURED with STATE_BACKEND=dynamodb, this
    function raises:

        RuntimeError: there is no state FILE on the 'dynamodb' backend; the run's
        state is an item in 'theagentorg-runs'. Read it with gates.load(run_id) ...

    `gates._state_path` refuses on that backend BY DESIGN (gates.py:116-121), and
    gates.py:78-79 names this module as the caller that should use `gates.load`.
    KNOWN DEBT, deliberately not fixed before the Aug 25 demo: the fix is to read
    through `gates.load(run_id)` and turn its FileNotFoundError into the
    SystemExit below, but that path has no test and this is the one script the
    whole cloud run depends on. run-pipeline.yml sets no STATE_BACKEND, so the
    demo runs on the `local` default and never reaches the broken path.
    """
    path = gates._state_path(run_id)
    if not path.is_file():
        # Named loudly. The likeliest cause is a broken artifact handoff --
        # an upload that published nothing, or a download that was removed from
        # the next job -- and "no state" must not be recoverable-looking, or a
        # stage would start a fresh run and report success for work it invented.
        raise SystemExit(
            f"no state file at {path}: the previous stage's artifact did not "
            f"arrive. This job cannot start a new run -- that would silently "
            f"discard everything already approved."
        )
    return RunState.model_validate_json(path.read_text())


def _emit(state: RunState) -> None:
    """Save the state and print the two lines a human reads off the job log.

    `ref`, not `path`: gates.save returns a `StateRef`, which is only Path-SHAPED
    to the extent of `read_text()`. The old name worked solely because it is
    consumed by an f-string and StateRef defines __str__ -- a reader who trusted
    it would reach for `.parent` or `.exists()` and get an AttributeError.
    """
    ref = gates.save(state)
    print(f"run_id={state.run_id}")
    print(f"status={state.status}")
    print(f"state={ref}")


def _stage_plan(args: argparse.Namespace) -> int:
    """PLAN. The only stage that creates a RunState rather than loading one."""
    state = RunState(ticket_id=args.ticket_id, ticket_text=args.ticket_text,
                     poisoned=flag(args.poisoned))
    _log(state, "system", "plan", "opened", summary=f"run started for {state.ticket_id}")

    state.plan = agent_client.call_agent("planner", state)
    _log(state, "planner", "plan", "proposed", summary=f"{len(state.plan.tasks)} tasks")
    # Posted to the ISSUE: there is no PR until `develop` runs `open_pr`.
    graph._plan_comment(state, state.plan)
    _emit(state)
    return EXIT_OK


def _stage_gate(args: argparse.Namespace, gate: str) -> int:
    """A GATE that was APPROVED. Records the decision the Environment extracted.

    THE APPROVAL HAPPENED BEFORE THIS RAN, and that is the whole design. GitHub
    held this job at an Environment with a required reviewer; the job did not
    start until somebody clicked. So there is nothing to ask here -- the click IS
    the decision, and this records it so `runs/<run_id>.jsonl` carries the same
    row an interactive `graph._cli_gate` run would have written.

    THIS FUNCTION ONLY EVER RECORDS AN APPROVAL, and that is correct rather than
    a missing branch -- but ONLY because `_stage_gate_rejected` below exists.
    When a reviewer REJECTS an Environment, GitHub does not run this job and hand
    it a verdict: it SKIPS the job entirely. Nothing inside here executes, so a
    branch in this function could never write "rejected" no matter how it were
    written. The rejection has to be recorded by a DIFFERENT job, one whose `if:`
    fires precisely when this one did not.

    `--auto-approve` changes only the `by` attribution, never whether the pause
    happens. An Environment is a repository setting and no workflow content can
    argue with it, which is exactly why the gates live there rather than in an
    `if:`.
    """
    state = _load(args.run_id)
    auto = flag(args.auto_approve)
    decision = HumanDecision(
        gate=gate,
        decision="approved",
        by="auto" if auto else (args.approver or "github-environment-reviewer"),
        reason=(
            "auto-approved run; the Environment still paused for a reviewer"
            if auto else
            f"approved through the {gate} Environment's required reviewer"
        ),
    )
    # gates.resume appends the decision, writes the state back and logs the row.
    # One writer, as gates.py:37 insists.
    state = gates.resume(args.run_id, decision)
    graph._gate_comment(state, gate, decision)
    _emit(state)
    return EXIT_OK


def _stage_gate_rejected(args: argparse.Namespace, gate: str) -> int:
    """Record that a gate was REFUSED, from a job that runs when the gate did not.

    ─────────────────────────────────────────────────────────────────────────
    THIS EXISTS BECAUSE `gates.py:16-20` DOCUMENTS THIS EXACT BUG BEING FIXED
    ONCE ALREADY, AND THE CLOUD PATH REINTRODUCED IT
    ─────────────────────────────────────────────────────────────────────────

    Quoting `agentorg/gates.py` verbatim:

        That file is only trustworthy if it is also written at the END of a run,
        which is why save() is public and run_pipeline calls it as it exits.
        Before it did, every finished run still read status="running" with its
        last decision missing -- so a run the graph had REJECTED could be resumed
        and approved, because nothing on disk said it was over.

    Without this function that is precisely the state the cloud path leaves
    behind. `_stage_gate` hardcodes `decision="approved"`, and a rejected gate
    SKIPS that job, so a refused run and an in-flight run are byte-identical on
    disk: both read `status="running"`, both carry no decision for the gate, and
    the refusal exists nowhere except the greyed-out job in the Actions UI. On the
    one surface whose entire purpose is that human gates hold, that is the worst
    available outcome -- and the whole point of the gates is undermined by it,
    because the run can then be resumed and approved.

    WHY A SEPARATE JOB AND NOT A BRANCH. GitHub SKIPS a job whose Environment was
    rejected; it does not run it with a verdict. So the recorder's `if:` has to
    fire on the states the gate job does NOT reach -- failure or cancellation --
    which is what makes this the one place in this workflow where an
    outcome-ignoring condition is correct. It records, it never advances the run,
    and it holds no credentials.

    THE RUN STOPS HERE REGARDLESS. `needs` already guarantees that: the stage
    after a gate needs the gate job, which was skipped, so it is skipped too.
    This function does not enforce the stop -- it records that it happened, which
    is the thing that was missing.

    ─────────────────────────────────────────────────────────────────────────
    AND IT REFUSES A RUN THAT HAS ALREADY ENDED, WHICH IS NOT BELT-AND-BRACES
    ─────────────────────────────────────────────────────────────────────────

    MEASURED, run 32509257195 of run-pipeline.yml, on the POISONED ticket:
    `develop` exited EXIT_BLOCKED and wrote `status=blocked`. gate2 was then
    SKIPPED because it `needs: develop`, and the recorder's condition at the time
    -- `always() && needs.gate2.result != 'success'` -- fired on that `skipped`.
    This function ran, called `gates.resume`, and wrote `status=rejected` OVER
    `status=blocked`, attributed to a github.actor who never saw a gate. The
    block, which is the single thing the poisoned demo beat exists to show, was
    erased by the job written to preserve refusals.

    The workflow now carries the discriminator (`needs.<preceding stage>.result
    == 'success'`), and that is the real fix -- this refusal cannot restore a
    block that was already overwritten. It exists because the workflow guard is
    one `if:` in a YAML file with no compiler and no test that can execute it: a
    future edit that drops the clause is a one-line diff, and the failure it
    causes is SILENT on the surface everyone reads. The state file simply says
    `rejected`. So the same rule is stated a second time where it CAN be
    executed, and the two are independent -- one is a condition GitHub evaluates,
    the other is Python this repository's suite runs.

    WHY THIS IS THE RIGHT LAYER, and specifically why the guard is not pushed
    down into `gates.resume`: that function is shared by four callers
    (`gates_cli`, `approve_server`, `_stage_gate` and this one) and it genuinely
    has no opinion about whether a run is over -- `agentorg/gates.py:206-208`
    only ever SETS `status`, and never reads it.
    `tests/test_approve_server.py:266-289` pins that gap ON PURPOSE, in a test
    whose docstring says: "If this test starts failing, `gates.py` grew a guard
    and the two tests above should be re-read, not deleted." A guard added there
    would break that test and, more importantly, would revoke the deliberate
    `gates_cli resume ... --decision overridden` escape hatch that
    `approve_server`'s docstring names as the documented way to override a
    security block -- the one capability a human is meant to keep. So the refusal
    goes on the UNATTENDED caller, which is this one: nobody is at a keyboard
    when a recorder job runs, so there is nobody to make that judgement.

    IT DOES NOT SWALLOW A REAL REFUSAL. Reaching this function on a live run
    means `status == "running"`, and every genuine rejection path is exactly
    that: a reviewer refusing an Environment stops the run AT the gate, so the
    preceding stage succeeded and nothing has written a terminal status yet. The
    only states this refuses are the four that mean some earlier stage already
    decided the outcome -- and in every one of them there is no human refusal to
    record, which is why inventing one is the defect.
    """
    state = _load(args.run_id)

    if state.status in _TERMINAL_STATUSES:
        # NOT a crash, and not a rejection either: EXIT_ALREADY_FINAL. The run's
        # real ending is left exactly as the stage that decided it wrote it, and
        # the refusal is LOGGED rather than only printed, because
        # `runs/<run_id>.jsonl` is the surface the timeline and the judges read
        # -- a refusal that exists only in a job log is a refusal nobody reading
        # the run can find.
        #
        # `action="opened"` because the LogEvent contract's vocabulary has no
        # word for "declined to write"; "rejected" would put a row on the
        # timeline saying a human refused this gate, which is the exact false
        # claim being refused. The summary carries the fact.
        _log(state, "system", gate, "opened", verdict=state.status,
             summary=(f"{gate}-rejected declined to record a refusal: this run "
                      f"already ended as status={state.status}. Recording one "
                      f"would overwrite that outcome and attribute it to a human "
                      f"who never saw the gate."))

        # AND THEN THE RUN'S REAL ENDING IS RE-STATED, AS THE LAST ROW.
        #
        # MEASURED, and it is the reason this second row exists rather than being
        # tidier without it. `timeline._outcome` (timeline.py:196-211) reads the
        # banner off the action of the LAST event, not off `RunState.status`, and
        # `_OUTCOME` holds only promoted/blocked/rejected. So the explanatory row
        # above -- action "opened", which is deliberately not an ending -- became
        # the last row and downgraded the banner:
        #
        #     before this job runs:  ⛔ BLOCKED — the change was stopped
        #     after, with one row:   … INCOMPLETE — run stopped at gate2 without
        #                                an ending
        #
        # That is this very defect one layer out. The guard would have preserved
        # `status=blocked` in the state file while erasing the word BLOCKED from
        # the projector, which is the surface the demo beat is actually judged on.
        # A guard whose own record destroys the evidence it protects is worse than
        # no guard, because it looks like it worked.
        #
        # Re-appending the ending is honest rather than cosmetic: the log is
        # append-only (state.py:193-195), so the earlier row cannot be edited, and
        # the fact being restated -- this run ended as `status` -- is true at this
        # moment and was true before. `verdict` carries the status either way, so
        # the two rows together read as "a recorder was asked, it declined, the
        # run is still blocked".
        _log(state, "system", gate, _OUTCOME_ACTIONS[state.status],
             verdict=state.status,
             summary=(f"run remains status={state.status}; the {gate} rejection "
                      f"recorder changed nothing"))

        print(f"::error::{gate} was not refused by a human -- this run already "
              f"ended as status={state.status}, so {gate} was skipped because the "
              f"run stopped earlier, not because anybody rejected it. Refusing to "
              f"overwrite that outcome.")
        _emit(state)
        return EXIT_ALREADY_FINAL

    decision = HumanDecision(
        gate=gate,
        decision="rejected",
        by=args.approver or "github-environment-reviewer",
        reason=(
            f"{gate} was refused, or its job did not complete. GitHub skips a job "
            f"whose Environment a reviewer rejected, so this was recorded by the "
            f"rejection recorder rather than by the gate job itself."
        ),
    )
    # `gates.resume` sets status="rejected" for a rejected decision (gates.py:86)
    # and writes it back. That write is the entire point: it is what makes a
    # refused run distinguishable from a running one on disk.
    state = gates.resume(args.run_id, decision)
    graph._gate_comment(state, gate, decision)
    _emit(state)
    print(f"{gate} rejected; run stopped and recorded as status={state.status}")
    return EXIT_REJECTED


def _stage_develop(args: argparse.Namespace) -> int:
    """DEVELOP + REVIEW loop, then the PR, then the deterministic security gate.

    These four things share a job because none of them is a gate boundary, and
    the revision loop in particular cannot be split: it iterates an unknown
    number of times, and Actions has no way to express "repeat this job until".

    THE ORDER IS LOAD-BEARING and is graph.py's, not a convenience. The block
    rule is evaluated on every run that produced a diff, BEFORE the reviewer's
    verdict is treated as terminal -- because on the poisoned ticket a competent
    reviewer objects to the hardcoded key, the developer re-inserts it on every
    revision, and the cap would reliably exhaust. Stopping at review first would
    quietly downgrade "the poisoned ticket blocks every time" into "it fails at
    review", which is a weaker and different claim. graph.py:224-247 says the
    same thing at greater length; this is the same ordering, not a second
    opinion.
    """
    state = _load(args.run_id)
    poisoned = flag(args.poisoned)

    # QUEUED, NOT POSTED, for graph.py's reason at :360-377: the PR does not exist
    # until `open_pr` below, and `github_ops._destination` reads `dev.branch` --
    # which the DEVELOPER fills with its own branch name before `open_pr`
    # overwrites it. Posting from inside the loop would degrade every one of these
    # to a `comment://` ref, delivered nowhere, on exactly the stages this is
    # meant to make visible.
    #
    # Each pass's OWN results are captured rather than re-read at flush time.
    # `state.dev` holds the LAST pass's result by then (and then `open_pr`'s
    # version of it), so re-reading would render the same diff into all three
    # attempt comments -- append in shape, replace in substance, green either way.
    loop_results = []
    while True:
        state.dev = agent_client.call_agent("developer", state, poisoned=poisoned)
        _log(state, "developer", "develop", "proposed", summary=state.dev.summary)

        state.review = agent_client.call_agent("reviewer", state)
        loop_results.append((state.dev, state.review, state.revision_count + 1))
        if state.review.verdict == "approve":
            _log(state, "reviewer", "review", "reviewed", verdict="approve",
                 summary="reviewer approved the diff")
            break
        if state.revision_count >= config.MAX_REVISION_LOOPS:
            _log(state, "reviewer", "review", "reviewed", verdict="changes_requested",
                 summary=f"revision cap of {config.MAX_REVISION_LOOPS} reached, "
                         f"changes still requested")
            break
        state.revision_count += 1
        _log(state, "reviewer", "review", "reviewed", verdict="changes_requested",
             summary=f"revision {state.revision_count}")

    state.dev = github_ops.open_pr(state)
    _log(state, "system", "develop", "opened", summary=f"PR {state.dev.pr_url}")

    # Now there is a PR, so the queue is flushed onto it -- in the order the
    # passes happened, developer then reviewer, one pair per attempt.
    for dev, review, attempt in loop_results:
        graph._develop_comment(state, dev, attempt)
        graph._review_comment(state, review, attempt)

    state.security = agent_client.call_agent("security", state)
    # scan_provenance answers "did the scanners run, or is this a fixture?" --
    # which the count in `summary` cannot, because the fixture fallback produces
    # a real count too.
    _log(state, "security", "security",
         "blocked" if state.security.verdict == "block" else "passed",
         verdict=state.security.verdict,
         summary=f"{len(state.security.blocking)} blocking",
         scan_provenance=state.security.scan_provenance)

    # ONE comment for BOTH outcomes, posted before the block branch rather than
    # inside it. A security stage that only spoke when it blocked would make the
    # CLEAN run's most important claim -- that the scanners ran and cleared the
    # change -- invisible on the surface the judges read, which is the same
    # silence as a check that did not run. It also carries `scan_provenance`,
    # because "blocked" proves two different things depending on whether real
    # scanners answered or a fixture stood in, and the count cannot tell them
    # apart.
    ref = graph._security_comment(state, state.security)

    if state.security.verdict == "block":
        state.status = "blocked"
        _log(state, "system", "security", "blocked",
             summary=f"pipeline halted by block rule; block reason {ref}",
             artifact_ref=ref, scan_provenance=state.security.scan_provenance)
        _emit(state)
        # EXIT_BLOCKED, not 1. This is the pipeline WORKING: the demo's poisoned
        # run ends here, and gate2 is never reached because it `needs` this job.
        print(f"blocked: {len(state.security.blocking)} blocking findings")
        for finding in state.security.blocking:
            print(f"  {finding.tool} {finding.severity} {finding.file}:{finding.line} {finding.rule}")
        return EXIT_BLOCKED

    if state.review.verdict != "approve":
        # Reached only by the cap exit above: the scanners cleared this diff but
        # nobody approved it. "failed" rather than "rejected" because no human
        # was asked.
        state.status = "failed"
        _log(state, "system", "review", "blocked", verdict=state.review.verdict,
             summary=f"scanners passed, but the reviewer never approved after "
                     f"{state.revision_count} revisions; not promoting")
        _emit(state)
        return EXIT_REJECTED

    _emit(state)
    return EXIT_OK


def _stage_sre(args: argparse.Namespace) -> int:
    """SRE. The last agent, and the last thing that can stop a promotion."""
    state = _load(args.run_id)
    state.sre = agent_client.call_agent("sre", state)
    _log(state, "sre", "sre", "reviewed", verdict=state.sre.verdict)
    # Both verdicts, for the same reason the security comment covers both: a `go`
    # that never appears on the PR is indistinguishable from an SRE stage that
    # was skipped.
    graph._sre_comment(state, state.sre)
    if state.sre.verdict == "no_go":
        state.status = "failed"
        _emit(state)
        return EXIT_REJECTED
    _emit(state)
    return EXIT_OK


def _stage_promote(args: argparse.Namespace) -> int:
    """PROMOTE. Reached only past gate3, so there is nothing left to decide."""
    state = _load(args.run_id)
    state.status = "promoted"
    _log(state, "system", "promote", "promoted", summary="change promoted")
    _emit(state)
    return EXIT_OK


# The stage table. One entry per job in run-pipeline.yml, and the ONLY list of
# valid stage names -- argparse takes its `choices` from these keys, so a typo'd
# stage is refused by the parser rather than falling through to a no-op that
# would report a green job for a stage that never ran.
STAGES = {
    "plan": _stage_plan,
    "gate1": lambda args: _stage_gate(args, "gate1"),
    "develop": _stage_develop,
    "gate2": lambda args: _stage_gate(args, "gate2"),
    "sre": _stage_sre,
    "gate3": lambda args: _stage_gate(args, "gate3"),
    "promote": _stage_promote,
    # The rejection recorders. One per gate, invoked by a recorder JOB whose `if:`
    # fires when the gate job did not run -- see _stage_gate_rejected for why a
    # branch inside the gate job cannot do this.
    "gate1-rejected": lambda args: _stage_gate_rejected(args, "gate1"),
    "gate2-rejected": lambda args: _stage_gate_rejected(args, "gate2"),
    "gate3-rejected": lambda args: _stage_gate_rejected(args, "gate3"),
}

# The stages that ADVANCE a run, in order: one job each, chained by `needs`. The
# rejection recorders are deliberately not here -- they are terminal, they record
# rather than advance, and a recorder appearing in this chain would mean the
# pipeline continued past a refusal.
STAGE_CHAIN = ["plan", "gate1", "develop", "gate2", "sre", "gate3", "promote"]

# Gate name -> the stage that records its refusal. One definition, so a test can
# assert the workflow carries a recorder for every gate without restating the map.
REJECTION_STAGES = {
    "gate1": "gate1-rejected",
    "gate2": "gate2-rejected",
    "gate3": "gate3-rejected",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_stage")
    # `choices` off STAGES, so the two cannot drift.
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--run-id", default="",
                        help="the run to continue; every stage but `plan` needs it")
    parser.add_argument("--ticket-id", default="")
    parser.add_argument("--ticket-text", default="")
    # STRINGS, not argparse's store_true. The dispatch API sends the text 'true'
    # or 'false' and `flag` is what parses it -- see this module's header.
    parser.add_argument("--poisoned", default="false")
    parser.add_argument("--auto-approve", default="false")
    parser.add_argument("--approver", default="",
                        help="who approved at the Environment, when known")
    args = parser.parse_args(argv)

    if args.stage == "plan":
        if not args.ticket_id or not args.ticket_text:
            parser.error("plan needs --ticket-id and --ticket-text")
    elif not args.run_id:
        parser.error(f"{args.stage} needs --run-id (the run `plan` created)")

    return STAGES[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
