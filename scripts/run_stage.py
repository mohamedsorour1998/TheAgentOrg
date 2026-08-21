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
`agent_client.call_agent`, the SAME `github_ops` functions and the SAME
`state.compute_security_verdict` that `graph._walk` calls, in the same order,
with the same block rule. `graph.py` remains the definition of the pipeline for
the local path; a second copy of the block rule -- the one deterministic thing
this whole project is built to demonstrate -- would be the worst possible thing
to duplicate.

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

from agentorg import gates, github_ops, log
from agentorg.common import agent_client, config
from agentorg.state import HumanDecision, LogEvent, RunState

# The exact strings accepted, and nothing else. Lower-cased before lookup so
# GitHub's `True`/`False` (which is what a boolean input renders as in some
# expression contexts) is accepted; NOT stripped, because whitespace around a
# value means something upstream is mangling the input and that is worth a
# failure rather than a silent repair.
_TRUE = frozenset({"true"})
_FALSE = frozenset({"false", ""})

# Exit codes. Three of them, because "the run was blocked", "the run was
# rejected by a human" and "this job crashed" are three different facts and the
# demo's whole point is that the first is a WORKING pipeline reporting a real
# verdict.
#
# All non-zero: a blocked or rejected run must not proceed, and `needs:` in the
# workflow is what stops the next job. But 1 is what an uncaught exception
# already exits with, so a block sharing that code would make the poisoned demo
# run indistinguishable from a broken workflow on the projector.
EXIT_OK = 0
EXIT_BLOCKED = 3
EXIT_REJECTED = 4


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
    the location stays a single definition. Task 6 moves this to DynamoDB by
    changing gates.py alone.
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
    """Save the state and print the two lines a human reads off the job log."""
    path = gates.save(state)
    print(f"run_id={state.run_id}")
    print(f"status={state.status}")
    print(f"state={path}")


def _stage_plan(args: argparse.Namespace) -> int:
    """PLAN. The only stage that creates a RunState rather than loading one."""
    state = RunState(ticket_id=args.ticket_id, ticket_text=args.ticket_text,
                     poisoned=flag(args.poisoned))
    _log(state, "system", "plan", "opened", summary=f"run started for {state.ticket_id}")

    state.plan = agent_client.call_agent("planner", state)
    _log(state, "planner", "plan", "proposed", summary=f"{len(state.plan.tasks)} tasks")
    _emit(state)
    return EXIT_OK


def _stage_gate(args: argparse.Namespace, gate: str) -> int:
    """A GATE. Records the decision the Environment already extracted.

    THE APPROVAL HAPPENED BEFORE THIS RAN, and that is the whole design. GitHub
    held this job at an Environment with a required reviewer; the job did not
    start until somebody clicked. So there is nothing to ask here -- the click IS
    the decision, and this records it so `runs/<run_id>.jsonl` carries the same
    row an interactive `graph._cli_gate` run would have written.

    `--auto-approve` therefore changes only the `by` attribution, never whether
    the pause happens. An Environment is a repository setting and no workflow
    content can argue with it, which is exactly why the gates live there rather
    than in an `if:`.
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
    _emit(state)
    return EXIT_OK


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

    while True:
        state.dev = agent_client.call_agent("developer", state, poisoned=poisoned)
        _log(state, "developer", "develop", "proposed", summary=state.dev.summary)

        state.review = agent_client.call_agent("reviewer", state)
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

    state.security = agent_client.call_agent("security", state)
    # scan_provenance answers "did the scanners run, or is this a fixture?" --
    # which the count in `summary` cannot, because the fixture fallback produces
    # a real count too.
    _log(state, "security", "security",
         "blocked" if state.security.verdict == "block" else "passed",
         verdict=state.security.verdict,
         summary=f"{len(state.security.blocking)} blocking",
         scan_provenance=state.security.scan_provenance)

    if state.security.verdict == "block":
        state.status = "blocked"
        ref = github_ops.post_comment(state, state.security.explanation)
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
