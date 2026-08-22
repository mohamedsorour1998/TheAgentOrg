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
from agentorg.common import agent_client, config, llm
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
# EVERY STATUS MAPS TO THE ACTION OF ITS OWN NAME. `failed` used to map to
# "blocked", because the LogEvent vocabulary had no "failed" -- it does now
# (state.py, commit b32ea5c), and so do `timeline._MARK` and `_OUTCOME`.
#
# MEASURED CONSEQUENCE of the old mapping, which is why this is a defect and not
# a tidying: a revision-cap run rendered `⛔ BLOCKED — the change was stopped`
# while its own security verdict was `pass` with 0 blocking findings. The
# ordering in `_stage_develop` guarantees the scanners ran and CLEARED that diff
# before the cap exit is reached, so the banner asserted the pipeline's central
# claim -- the deterministic rule stopped this change -- about a change nothing
# blocked. And `timeline._MARK` gives `failed` a `✗` rather than `⛔` for the same
# reason: at projector distance the glyph is read before the word.
_OUTCOME_ACTIONS = {
    "blocked": "blocked",
    "failed": "failed",
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

# EXIT_NOT_PROMOTABLE is the fifth, and the reasoning in the block above decides
# what it may NOT be. A promote stage that refused an unpromotable run did not
# evaluate the block rule (EXIT_BLOCKED would claim it did, and on a run whose
# security verdict is already `block` that code would be almost right and
# therefore worse -- it would report the block as though this stage found it),
# was not refused by a human (EXIT_REJECTED is the precise lie the recorder
# guard exists to prevent), and did not crash.
#
# It is NOT EXIT_ALREADY_FINAL either, and that distinction is the whole reason
# a fifth code exists. `EXIT_ALREADY_FINAL` means "this run had already ENDED and
# I declined to overwrite its ending" -- a terminal status, nothing left to
# decide. The refusals below include a run that is still `running`: security
# passed, no gate was rejected, and gate3 simply has no approval recorded yet.
# That run has not ended, and reporting it as already-final would be the same
# conflation this project exists to prevent -- "denied" read as "not ready yet".
# So a terminal state DOES return EXIT_ALREADY_FINAL (reusing it exactly as its
# own comment block reasons), and an unfinished but unpromotable one returns this.
EXIT_NOT_PROMOTABLE = 6


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
    """Reload the state a previous job uploaded, from whichever backend holds it.

    Read through `gates.load`, which dispatches on `config.STATE_BACKEND` and
    handles both -- not through `gates._state_path`, which refuses on `dynamodb`
    BY DESIGN (gates.py:116-121) and whose own docstring names this module as the
    caller that should use `gates.load`.

    CORRECTED 2026-08-22, twice, and both corrections are stated rather than
    deleted because each was load-bearing prose a reader would have taken on
    trust. An earlier version claimed Task 6 moves this to DynamoDB "by changing
    gates.py alone" -- never true. Its replacement then recorded, correctly, that
    this function RAISED on the dynamodb backend, and called that KNOWN DEBT
    deliberately unfixed:

        RuntimeError: there is no state FILE on the 'dynamodb' backend; the run's
        state is an item in 'theagentorg-runs'. Read it with gates.load(run_id)...

    MEASURED with `STATE_BACKEND=dynamodb`, that was every cloud stage after
    `plan`. It is fixed now, and it was three lines rather than the rewrite that
    docstring implied, because `gates.load` already did the dispatch.

    THE REFUSAL FOR AN ABSENT RUN IS UNCHANGED, and it is the half worth being
    careful about. `gates.load` raises FileNotFoundError on BOTH backends for a
    run that does not exist -- deliberately the same exception -- and that is
    turned into the named SystemExit below rather than into a fresh RunState. A
    fresh RunState would start a new run and report success for work it invented,
    silently discarding every approval already recorded, with the job green.
    """
    try:
        return gates.load(run_id)
    except FileNotFoundError as absent:
        # Named loudly. The likeliest cause is a broken artifact handoff --
        # an upload that published nothing, or a download that was removed from
        # the next job -- and "no state" must not be recoverable-looking, or a
        # stage would start a fresh run and report success for work it invented.
        #
        # The message keeps the words "no state file" for the local backend even
        # though `gates.load` now also serves dynamodb, where there is no file:
        # `gates.load`'s own exception text is interpolated and names whichever
        # backend actually refused, so the two together are accurate on both.
        # `tests/test_run_pipeline_workflow.py:1381` asserts on this phrase.
        raise SystemExit(
            f"no state file or item for run {run_id!r} ({absent}): the previous "
            f"stage's artifact did not arrive. This job cannot start a new run "
            f"-- that would silently discard everything already approved."
        ) from absent


def _emit(state: RunState, *, pausing_for: str = "") -> None:
    """Save the state and print the two lines a human reads off the job log.

    `ref`, not `path`: gates.save returns a `StateRef`, which is only Path-SHAPED
    to the extent of `read_text()`. The old name worked solely because it is
    consumed by an f-string and StateRef defines __str__ -- a reader who trusted
    it would reach for `.parent` or `.exists()` and get an AttributeError.

    `pausing_for` names the gate this stage is about to hand the run to, and
    routes the write through `gates.pause` INSTEAD of `gates.save` rather than in
    addition to it. `gates.pause` calls `save` itself, so calling both would
    write the state twice -- harmless on the local backend, a second PutItem on
    the other, and misleading either way about how many writers there are.

    WHICH PATH ANSWERED THE MODEL CALLS is stamped here, at the one place every
    stage passes through on its way out. `llm.last_source()` returns None when no
    call was made in this process at all -- a gate stage, or `promote` -- and that
    is recorded as `""` rather than as `"model"`: a stage that never asked the
    model must not claim the model answered. `"fixture"` never downgrades to
    `"model"` inside `llm._record`, so a run where ANY agent fell back reports
    `fixture` for the whole run.

    On the cloud path each stage is a separate PROCESS, so this reads only the
    calls that stage made -- and a stage that served fixtures overwrites an
    earlier stage's `model` with `fixture`, which is the honest direction. The
    reverse would need the guard `llm._record` already carries.
    """
    source = llm.last_source()
    if source:
        state.model_provenance = source
    ref = gates.pause(state, pausing_for) if pausing_for else gates.save(state)
    print(f"run_id={state.run_id}")
    print(f"status={state.status}")
    print(f"state={ref}")
    print(f"_source={state.model_provenance or 'none'}")

    # THE ENDING, REPORTED TO THE ISSUE AND THE ISSUE CLOSED — once, at the one
    # place every terminal stage passes through.
    #
    # `_emit` is the single writer on this path, so a report here covers all of them:
    # a block at `develop`, a revision cap, an SRE no_go, a human refusal recorded by
    # a rejection recorder, and a promotion. Written per-stage instead, the poisoned
    # ending -- the one the demo exists to show -- would be the likeliest to be
    # forgotten, because it is the only one that exits early.
    #
    # GATED ON A TERMINAL STATUS. Every stage calls `_emit`, and most of them leave
    # the run `running`; reporting there would post an outcome comment after each of
    # seven jobs and close the issue before the work had finished.
    if state.status in _TERMINAL_STATUSES:
        github_ops.report_outcome(state)


def _stage_plan(args: argparse.Namespace) -> int:
    """PLAN. The only stage that creates a RunState rather than loading one.

    `trigger` is recorded here, and only here, because this is the stage that
    creates the run -- no later stage knows how it started. No Actions context
    field can answer it: EventBridge dispatches through the same REST API
    `gh workflow run` uses, so `github.event_name` reads `workflow_dispatch`
    either way.
    """
    # BEFORE the first agent call. `llm._record` is module state, and on a laptop
    # the same process can run several stages in a row -- without this a run would
    # inherit the previous one's provenance, which is worse than reporting
    # nothing because it looks like a measurement.
    llm.reset_source()

    state = RunState(ticket_id=args.ticket_id, ticket_text=args.ticket_text,
                     poisoned=flag(args.poisoned),
                     # `getattr`, not `args.trigger`, and the default is the same
                     # `manual` argparse uses. Only `plan` reads this flag, and
                     # several existing tests build their own Namespace for the
                     # stage functions -- requiring every caller to know about a
                     # flag six of the seven stages ignore makes the parser's
                     # shape a dependency of every test that drives a stage.
                     trigger=getattr(args, "trigger", "") or "manual")
    _log(state, "system", "plan", "opened",
         summary=f"run started for {state.ticket_id} (trigger: {state.trigger})")

    state.plan = agent_client.call_agent("planner", state)
    _log(state, "planner", "plan", "proposed", summary=f"{len(state.plan.tasks)} tasks")
    # Posted to the ISSUE: there is no PR until `develop` runs `open_pr`.
    graph._plan_comment(state, state.plan)
    # The run now waits at gate1, so the pause marker goes down here -- see
    # `_GATE_AFTER` for why the gate stage itself cannot write it.
    _emit(state, pausing_for=_GATE_AFTER["plan"])
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
        # NAMES ONE CAUSE, not two. This read "was refused, OR its job did not
        # complete", which was honest hedging when the recorder genuinely could not
        # tell those apart -- and it made the most important sentence on the issue
        # unreadable: a human is told a decision was recorded against their name and
        # then told it might not have been a decision.
        #
        # The workflow now excludes `cancelled`, so the only remaining reason this
        # job runs is a refusal at the Environment. So this says so plainly, and says
        # where to look, since the gate job itself has no log to read -- GitHub
        # SKIPS a job whose Environment was rejected rather than running it with a
        # verdict, which is why a recorder exists at all.
        reason=(
            f"a required reviewer refused this change at {gate}, so the run stopped "
            f"here and was not merged. GitHub skips the {gate} job itself when its "
            f"Environment is rejected, so there is no {gate} job log to read -- this "
            f"comment is the record."
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
        # action="failed", NOT "blocked". The scanners CLEARED this diff -- the
        # ordering above guarantees the security stage ran first and its verdict
        # is `pass` with 0 blocking -- so a `blocked` row renders
        # `⛔ BLOCKED — the change was stopped`, which is the pipeline's central
        # claim asserted about a change nothing blocked. `timeline._outcome`
        # reads this action and never `state.status`, so the row is the only
        # thing that decides what a judge sees.
        _log(state, "system", "review", "failed", verdict=state.review.verdict,
             summary=f"scanners passed, but the reviewer never approved after "
                     f"{state.revision_count} revisions; not promoting")
        _emit(state)
        return EXIT_REJECTED

    # Only the SUCCESSFUL exit pauses. The two exits above ended the run, and a
    # pause marker on an ended run would put it on the approval screen asking a
    # human to decide something already decided -- `_awaiting` filters terminal
    # statuses, but relying on that would make this stage's correctness depend on
    # a filter in another module.
    _emit(state, pausing_for=_GATE_AFTER["develop"])
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
        # THE ENDING IS WRITTEN DOWN. Before this row existed the no_go exit set
        # `state.status = "failed"` and returned, logging nothing -- and NO ROW
        # CARRIES `RunState.status`, so `timeline._outcome` had nothing to read
        # and rendered `… INCOMPLETE — run stopped at sre without an ending`.
        #
        # That is a different claim from the true one. INCOMPLETE says the run
        # stopped without deciding: a crash, an abandoned gate. A no_go is a
        # DECISION, made by a stage that ran to completion. Conflating "did not
        # finish" with "finished and refused" is this project's signature defect
        # on the run's most visible field.
        #
        # `verdict` carries the SRE's own word, because two endings write
        # `failed` -- this one and the revision cap -- and they render the same
        # banner by design, so the row has to say which happened.
        _log(state, "sre", "sre", "failed", verdict=state.sre.verdict,
             summary=f"SRE returned {state.sre.verdict}; not promoting "
                     f"(CI {state.sre.ci_status})")
        _emit(state)
        return EXIT_REJECTED
    # The run now waits at gate3 -- the last gate, and the one whose approval
    # authorises the promotion.
    _emit(state, pausing_for=_GATE_AFTER["sre"])
    return EXIT_OK


def _stage_promote(args: argparse.Namespace) -> int:
    """PROMOTE. Past gate3 -- and it CHECKS that rather than assuming it.

    An earlier version of this function was three lines: load, set
    `status="promoted"`, log. It wrote PROMOTED over whatever it had loaded, with
    no check at all. The job graph makes that unreachable today -- `promote`
    declares `needs: gate3` and a blocked run never reaches gate3 -- but that is
    CONTROL FLOW IN A YAML FILE, with no compiler and no test that can execute
    it. This workflow already carries three rejection recorder jobs for exactly
    that reason: the equivalent guard was dropped in a one-line edit once (run
    32509257195) and the resulting failure was silent on every surface anyone
    reads. `graph.py` is a second caller of the same promote step.

    The rule itself is `graph.not_promotable`, shared with that second caller
    rather than restated here -- see its docstring for why the gate decisions are
    READ rather than counted, and why `overridden` counts as an approval.
    """
    state = _load(args.run_id)

    # A run that has ALREADY ENDED gets EXIT_ALREADY_FINAL, reusing that code
    # exactly as its own comment block reasons: this stage was asked to overwrite
    # an outcome some earlier stage decided, and it declined. An unpromotable run
    # that has NOT ended is a different fact and gets its own code -- see
    # EXIT_NOT_PROMOTABLE. Checked first, because `blocked` and `rejected` are
    # both terminal AND unpromotable, and the terminal reading is the more
    # specific one.
    if state.status in _TERMINAL_STATUSES:
        _log(state, "system", "promote", "opened", verdict=state.status,
             summary=(f"promote declined: this run already ended as "
                      f"status={state.status}. Writing 'promoted' over that "
                      f"would erase the outcome an earlier stage decided."))
        # The run's real ending, RE-STATED AS THE LAST ROW, for the reason
        # `_stage_gate_rejected` records at length: `timeline._outcome` reads its
        # banner off the action of the LAST row and never off `RunState.status`,
        # so the explanatory row above would otherwise downgrade a ⛔ BLOCKED
        # banner to `… INCOMPLETE` while the state file still said blocked. A
        # guard whose own record destroys the evidence it protects is worse than
        # no guard, because it looks like it worked.
        _log(state, "system", "promote", _OUTCOME_ACTIONS[state.status],
             verdict=state.status,
             summary=f"run remains status={state.status}; promote changed nothing")
        print(f"::error::refusing to promote: this run already ended as "
              f"status={state.status}.")
        _emit(state)
        return EXIT_ALREADY_FINAL

    refusal = graph.not_promotable(state)
    if refusal:
        # `failed` rather than `rejected`: no human refused this promotion, the
        # preconditions for it simply are not met. "failed" is also what the
        # revision-cap and SRE no_go endings write, and it is the honest word --
        # this run ended without shipping and without the block rule stopping it.
        state.status = "failed"
        _log(state, "system", "promote", "failed",
             summary=f"refused to promote: {refusal}")
        print(f"::error::refusing to promote: {refusal}")
        _emit(state)
        return EXIT_NOT_PROMOTABLE

    # THE MERGE IS WHAT MAKES THE PROMOTION TRUE, so it happens first and its row
    # goes down first. `github_ops.merge_pr` never raises and always returns a
    # ref, the same contract `post_comment` carries and for the same reason: the
    # next line records this run's ending, and a raise here would lose it.
    #
    # `promoted` MUST BE THE LAST ROW. `timeline._outcome` reads its banner off
    # the last row's action, so logging `merged` after it would render a shipped
    # run as `⇄ MERGED` and ★ PROMOTED would never appear.
    ref = github_ops.merge_pr(state)
    _log(state, "system", "promote", "merged", summary=f"merged {ref}",
         artifact_ref=ref)

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

# For each advancing stage, the gate the run waits at NEXT -- derived from
# STAGE_CHAIN rather than written out, so a stage inserted into the chain cannot
# leave a gate with no pause marker.
#
# WHY THE PAUSE IS WRITTEN BY THE STAGE BEFORE THE GATE, and not by the gate
# stage itself. `approve_server._awaiting` lists a run as awaiting a decision iff
# it has an open pause marker for a gate with NO decision recorded yet
# (`paused - decided`). In the cloud the gate job does not start until somebody
# has already clicked, so a `gates.pause` inside `_stage_gate` would write the
# marker and the decision in the same job -- `paused - decided` would be empty
# and the run would never appear on the screen. The window this marker has to
# describe is the one where GitHub is holding the job at the Environment, and the
# only code that runs before that window opens is the preceding stage.
#
# `graph.py` writes it from inside `ask` for the same reason: `_auto_gate` and
# `_cli_gate` both call `gates.pause` BEFORE returning a decision.
_GATE_AFTER = {
    stage: STAGE_CHAIN[index + 1]
    for index, stage in enumerate(STAGE_CHAIN[:-1])
    if STAGE_CHAIN[index + 1] in REJECTION_STAGES
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
    # HOW THIS RUN STARTED. A free string, not `choices`: a closed enum would
    # REFUSE a future trigger source rather than record it, and an unrecognised
    # trigger name is still better evidence than a rejected dispatch. Unlike
    # `--poisoned` this does not go through `flag` -- it is not a boolean, and
    # nothing branches on it, so an unexpected value costs a mislabelled field
    # rather than the wrong pipeline.
    parser.add_argument("--trigger", default="manual",
                        help="how this run was started: manual, issue, ...")
    args = parser.parse_args(argv)

    if args.stage == "plan":
        if not args.ticket_id or not args.ticket_text:
            parser.error("plan needs --ticket-id and --ticket-text")
    elif not args.run_id:
        parser.error(f"{args.stage} needs --run-id (the run `plan` created)")

    return STAGES[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
