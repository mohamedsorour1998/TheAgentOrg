"""The pipeline graph — walks a ticket through all five agents and three gates.

OWNER: Sorour.

This is the spine of The Agent Org. On day 1 it runs end-to-end on STUBS: every
node returns a validated fixture, so the whole path works before any real agent,
scanner, or GitHub call exists. As each teammate replaces their stub, this file
does not change — the function signatures are frozen in state.py.

Flow:
    plan -> gate1 -> develop -> review -(loop)-> open_pr -> security -> gate2 -> sre -> gate3 -> promote

Human gates are handled by pause()/resume() in gates.py. The demo runner
AUTO-APPROVES them (auto_approve=True) so a single call walks the whole path;
auto_approve=False asks a real human on the terminal, and agentorg/gates_cli.py
records the same decision out of band for anyone who walked away.

A run reaches the end of this file only by being approved at every step. There
are four ways it does not, and they are checked in this order:
    security verdict "block"      -> status "blocked"   (deterministic rule)
    reviewer never approved       -> status "failed"    (revision budget spent)
    sre verdict "no_go"           -> status "failed"
    a human said no at any gate   -> status "rejected"
"rejected" is reserved for the human ones. An agent's refusal is a "failed" run,
because nobody was asked.

EVERY ONE OF THOSE FOUR WRITES A LOG ROW NAMING ITS ENDING, and that is a
requirement rather than a courtesy. `timeline._outcome` reads its banner off the
action of the LAST log row and never off `RunState.status` -- no row carries
that field -- so an exit that only sets `status` renders as
`… INCOMPLETE — run stopped at <stage> without an ending`. The two `failed`
endings each write `action="failed"`, not `"blocked"`: the scanners CLEARED
those changes, and `⛔ BLOCKED` over one of them is this pipeline's central claim
asserted about a change nothing blocked.

The order is load-bearing: the deterministic block is evaluated on every run
that produced a diff and wins over every other stop, because "the poisoned
ticket blocks" is a claim about code, not about what a model thought of the
diff. See the comment at step 5b.

EVERY STAGE POSTS ITS OUTPUT to the target repo, through the one function
github_ops.post_comment. Plan and gate1 land on the ISSUE because the PR does
not exist until `open_pr`; everything from the developer onward lands on the PR.
The develop/review comments are QUEUED during the revision loop and flushed once
that PR exists -- see step 3. That surface, not this file's log, is what a judge
reads, so a stage that ran silently is a stage that did not run as far as anyone
watching can tell.

Run it:
    python -m agentorg.graph            # clean ticket  -> promoted
    python -m agentorg.graph --poisoned # poisoned      -> blocked
"""

from __future__ import annotations

import os
import typing
from collections.abc import Callable

from . import gates, integrations, log
from .common import agent_client, config, llm
from .security import scoring
from .state import (
    DevResult,
    HumanDecision,
    LogEvent,
    PlanResult,
    ReviewResult,
    RunState,
    SecurityResult,
    SREResult,
)
from .tenancy import run_index

# ==========================================================================
# EVERY STAGE'S OUTPUT REACHES THE TARGET REPO. See _comment.
# ==========================================================================

# The label every comment opens with. THE FIRST LINE, and machine-readable on
# purpose: the repo's surface is a list of comments, and a reader -- human or
# test -- has to be able to attribute one to a stage without inferring it from
# the prose. Inference is exactly what fails: "review" is a substring of
# "reviewer", which appears in the DEVELOP comment, so a matcher keyed on the
# word alone reports the review stage as present when it posted nothing.
#
# tests/test_agent_comments.py deliberately RESTATES this string rather than
# importing it, so a change to the format shows up there as a failure instead of
# moving on both sides at once and pinning a format nobody posts any more.
COMMENT_HEADER = "### Agent Org · "


def _comment(state: RunState, stage: str, lines: list[str]) -> str:
    """Post one labelled comment for one stage, and hand back its delivery ref.

    WHY EVERY STAGE POSTS. The PR (and before it, the issue) is the timeline a
    judge actually reads. Until this existed, exactly one stage spoke -- the
    security block explanation -- so a planner that produced nothing, a reviewer
    that never ran, and an SRE agent that was skipped all rendered identically on
    that surface: as silence. That is this project's signature defect sitting in
    the one place the audience is looking.

    ONE FUNCTION, NOT NINE CALL SITES. The label format, the bullet rendering and
    the fact that a delivery failure must not propagate are each one decision;
    nine copies would be nine chances for one of them to drift, and the drift is
    invisible because a comment that posts in a different shape still posts.

    `github_ops.post_comment` is EXTENDED rather than duplicated -- it already
    exists, already cannot raise, and already carries the ref contract the
    timeline reads (`https://` or `local://` delivered, `comment://` not). This
    function adds no error handling of its own for exactly that reason: there is
    nothing to handle, and a try/except here would only be able to make things
    worse by catching the conftest guard that keeps the suite off the live API.
    """
    body = f"{COMMENT_HEADER}{stage}\n\n" + "\n".join(lines)
    return integrations.host().post_comment(state, body)


def _bullets(items: list[str]) -> list[str]:
    """Render a list as markdown bullets, or say plainly that it was empty.

    The empty case is not cosmetic. An empty list renders as nothing at all, so a
    section header with no bullets under it reads as a stage that had no output
    when what actually happened is that this particular list was empty -- the
    reviewer's `must_fix` on an approval, for instance, which is the NORMAL case.
    """
    return [f"- {item}" for item in items] or ["- (none)"]


def _plan_comment(state: RunState, plan: PlanResult) -> None:
    """The planner's output: what it intends to do, and how it will be judged."""
    _comment(state, "plan", [
        f"**{len(plan.tasks)} tasks** for `{state.ticket_id}`:",
        *_bullets(plan.tasks),
        "",
        "**Acceptance criteria:**",
        *_bullets(plan.acceptance_criteria),
        "",
        "**Target files:** " + (", ".join(f"`{f}`" for f in plan.target_files) or "(none)"),
        *([f"\n{plan.notes}"] if plan.notes else []),
    ])


def _gate_comment(state: RunState, gate: str, decision: HumanDecision) -> None:
    """Who let this run through the gate, or who stopped it, and why.

    Posted for approvals as well as refusals. A gate that only spoke when it
    refused would leave the demo's central claim -- that a human decided --
    provable only by its absence, and "no comment" is not evidence of a decision.
    """
    _comment(state, gate, [
        f"**{decision.decision.upper()}** by `{decision.by}`",
        f"at {decision.at}",
        *([f"\n{decision.reason}"] if decision.reason else []),
    ])


def _develop_comment(state: RunState, dev: DevResult, attempt: int) -> None:
    """The developer's diff, one comment per attempt. See _flush in _walk.

    `dev` is a PARAMETER rather than read off `state.dev`, and that is what makes
    the queued posting correct. By the time these are flushed, `state.dev` holds
    the LAST pass's result (and then `open_pr`'s version of it), so a function
    reading it would render the same diff into all three attempt comments --
    append in shape, replace in substance, and green either way. `state` is still
    what gets posted THROUGH, because its `dev.branch` is the branch `open_pr`
    actually created and therefore the one carrying the PR.
    """
    _comment(state, "develop", [
        f"**attempt {attempt}** — {dev.summary}",
        "",
        "**Files changed:** " + (", ".join(f"`{f}`" for f in dev.files_changed)
                                 or "(none)"),
        "",
        "<details><summary>diff</summary>",
        "",
        "```diff",
        dev.diff.rstrip("\n"),
        "```",
        "",
        "</details>",
    ])


def _review_comment(state: RunState, review: ReviewResult, attempt: int) -> None:
    """The reviewer's verdict on one attempt, with what it wants fixed."""
    _comment(state, "review", [
        f"**attempt {attempt}** — verdict `{review.verdict}`",
        "",
        "**Must fix:**",
        *_bullets(review.must_fix),
        "",
        "**Comments:**",
        *_bullets([f"`{c.file}:{c.line}` {c.note}" for c in review.comments]),
    ])


def _security_comment(state: RunState, security: SecurityResult) -> str:
    """The security verdict, its evidence, and WHERE the verdict came from.

    Returns the delivery ref, because this is the one comment whose fate is
    already written into the run's log row -- see the block branch in _walk.

    `scan_provenance` is on the comment for the same reason it is on the log row:
    "blocked" proves two different things depending on whether real scanners ran
    or a fixture stood in for them, and the count of blocking findings is
    produced identically by both paths. A reader of the PR alone would have no
    way to tell, and the fixture's explanation names a real file and a real
    remediation -- it is indistinguishable from real gitleaks output.

    THE SCORING TABLE IS THE LITERAL ANSWER TO A JUDGE'S QUESTION, asked at the
    pre-final: "gitleaks and trivy -- how do we score the response so we know it is
    go or no-go, as you claimed it is deterministic". The verdict was already
    deterministic; what was missing was showing the arithmetic. Lane C built the
    renderer and could not call it -- this file is not in its ownership row -- so
    until the integrator wired it, no deployed run carried a scoring row.

    Rendered AFTER the bullets and BEFORE the explanation, deliberately. The
    bullets are what blocked; the table is why, per finding; the explanation is the
    model's prose. Prose last, because a reader who stops early should stop on the
    arithmetic rather than on the paragraph -- `explanation` does not set the
    verdict and must not be the last word on it.
    """
    return _comment(state, "security", [
        (f"**{security.verdict.upper()}** — {len(security.blocking)} blocking "
         f"finding(s) of {len(security.findings)} total"),
        f"_provenance: {security.scan_provenance or 'unknown'}_",
        "",
        *_bullets([f"`{f.tool}` **{f.rule}** ({f.severity}) at `{f.file}:{f.line}` "
                   f"— {f.description}" for f in security.blocking]),
        "",
        *scoring.render_scoring_table(security.scoring),
        "",
        security.explanation,
    ])


def _sre_comment(state: RunState, sre: SREResult) -> None:
    """The SRE's go/no-go, the SLO checks behind it, and the cost note."""
    _comment(state, "sre", [
        f"**{sre.verdict.upper()}** — CI {sre.ci_status}",
        "",
        "**SLO checks:**",
        *_bullets([f"{'PASS' if c.passed else 'FAIL'} {c.name}"
                   f"{f' — {c.detail}' if c.detail else ''}"
                   for c in sre.slo_checks]),
        *([f"\n**Cost:** {sre.estimated_cost_note}"] if sre.estimated_cost_note else []),
        *([f"\n{sre.notes}"] if sre.notes else []),
    ])


def _log(state: RunState, actor, stage, action, verdict="", summary="",
         artifact_ref="", scan_provenance=""):
    """Append one row to the run's log.

    WIDENED IN WEEK 3, by ADDITION only -- both new parameters default to "", so
    every existing call site writes the same SEMANTIC row as before.

    NOT the same BYTES, and the difference is worth stating because an earlier
    version of this docstring claimed it was. `model_dump()` emits defaults, and
    this helper passes both parameters unconditionally, so every row now carries
    a `"scan_provenance": ""` key -- MEASURED on a full run of each kind: 14/14
    rows on a clean run and 9/9 on a poisoned one carry the key, while only 1 and
    2 respectively carry a non-empty value. Old readers ignore an extra key and
    rows validate in both directions, so nothing breaks; but "identical bytes"
    was false and a later reader would have relied on it.

    `gates.py:58` and `:80` construct LogEvent directly rather than through here,
    so their rows carry the key too. That is correct -- a gate row has no scan
    provenance to report, and "" is exactly what it should say.

    The two parameters exist because agentorg/timeline.py may read nothing but
    log.read(run_id), which makes this helper the only route by which a fact can
    reach the judges:

      * artifact_ref  the delivery ref for a block reason. It was already being
        recorded, but only INSIDE a summary sentence, so reading it back meant
        string-parsing prose. It now goes in the field state.py named for it
        ("PR url, branch, path to findings json") as well.
      * scan_provenance  whether the verdict came from real scanners or a
        fixture. See state.ScanProvenance.

    The summary keeps carrying the ref too, deliberately: every run already on
    disk has it there and nowhere else, and the timeline must render those.
    """
    log.append(LogEvent(
        run_id=state.run_id, ticket_id=state.ticket_id,
        actor=actor, stage=stage, action=action, verdict=verdict, summary=summary,
        artifact_ref=artifact_ref, scan_provenance=scan_provenance,
    ))


def _auto_gate(state: RunState, gate: str) -> HumanDecision:
    """Demo helper: record an auto-approval. The real UI replaces this."""
    gates.pause(state, gate)
    return HumanDecision(gate=gate, decision="approved", by="auto", reason="demo auto-approve")


# Exact words, not a prefix. A prefix match on "a" made "abort" — the most
# natural way to bail out of a prompt you did not mean to be at — mean APPROVE,
# on the three prompts in this system where being misread is most expensive.
# Everything not in this set rejects, so bare Enter still fails closed.
APPROVAL_WORDS = frozenset({"a", "approve", "approved", "y", "yes"})


def _cli_gate(state: RunState, gate: str) -> HumanDecision:
    """Real gate: pause, ask a human on the terminal, record their decision."""
    ref = gates.pause(state, gate)
    print(f"\n[{gate}] paused. state saved -> {ref}")
    answer = input(f"[{gate}] approve / reject? [a = approve, anything else rejects] ")
    decision = "approved" if answer.strip().lower() in APPROVAL_WORDS else "rejected"
    return HumanDecision(gate=gate, decision=decision,
                         by=os.environ.get("USER", "human"))


def _decide(state: RunState, gate: str, ask: Callable[[RunState, str], HumanDecision]) -> bool:
    """Take the human decision at one gate. False means the run stops here.

    Recording the decision and honouring it live together rather than at each
    of the three call sites, because the two failures that matter are a gate
    that was recorded but not honoured and a gate that was honoured but not
    recorded — and both are easy to write by hand three times in a row.

    Every decision is logged, not only the refusals. gates.resume() already logs
    the async ones, so without this the timeline would show a run pausing at a
    gate and then simply carrying on, with no record of who let it through.

    POSTING the decision lives here for the same reason, and it is the same
    argument one step further: three hand-written call sites are three chances
    for one gate to be honoured and logged but never mentioned on the surface a
    judge reads. The comment goes out for approvals and refusals alike -- see
    _gate_comment.
    """
    decision = ask(state, gate)
    state.decisions.append(decision)
    stopping = decision.decision == "rejected"
    _log(state, "human", gate, decision.decision, verdict=decision.decision,
         summary=decision.reason or (f"run stopped at {gate}" if stopping else ""))
    _gate_comment(state, gate, decision)
    if stopping:
        state.status = "rejected"
    return not stopping


# The three gates a run must carry an approval for before it may promote. Same
# names as `HumanDecision.gate`'s Literal.
REQUIRED_GATES = ("gate1", "gate2", "gate3")

# A run in one of these has ENDED. DERIVED from the frozen contract's own Literal
# rather than written out, so a status added to `RunState` is terminal here the
# moment it exists -- a hardcoded copy would treat a new ending as "running" and
# let the promote step overwrite it. `scripts/run_stage.py` derives the same set
# the same way for its own boundary, and `approve_server.py:126` states it as a
# literal for a third.
_TERMINAL_STATUSES = frozenset(
    typing.get_args(typing.get_type_hints(RunState)["status"])
) - {"running"}

# The decisions that count as "a human let this through". `overridden` is here
# DELIBERATELY: it is the one capability a human is meant to keep --
# approve_server's docstring names `gates_cli resume ... --decision overridden`
# as the shell-only route for accepting a risk the unauthenticated screen
# refuses to click through -- and a guard that rejected it would delete that
# route while looking like it tightened something.
_APPROVING_DECISIONS = frozenset({"approved", "overridden"})


def not_promotable(state: RunState) -> str:
    """Why this run must NOT be promoted, or `""` if it may be.

    Both promote sites call this: step 8 below, and `scripts/run_stage.py`'s
    `_stage_promote`. ONE predicate with two callers rather than two hand-written
    checks, because CLAUDE.md records THREE mutations that survived 793 tests for
    exactly one reason -- `run_stage.py` inherited this file's COMMENT about a
    hazard without inheriting its TEST. A shared predicate cannot drift that way,
    and a test of either caller exercises the same rule.

    WHY THIS EXISTS WHEN THE STRUCTURE ALREADY PREVENTS IT. It does not prevent
    it; it makes it unreachable, which is a different thing. Here, a block
    `return`s at step 5 so step 8 is never reached. In the cloud, `promote`
    declares `needs: gate3` and a blocked run never reaches gate3. Both are
    CONTROL FLOW -- one an early return, the other an `if:` in a YAML file with no
    compiler and no test that can execute it. `run-pipeline.yml`'s three rejection
    recorders exist because that second kind of guard was dropped in a one-line
    edit once already (run 32509257195), and the failure was SILENT on every
    surface anyone reads: the state file simply said the wrong thing. So the rule
    is stated a second time where Python runs it.

    THE DECISIONS ARE READ, NOT COUNTED, and that is the load-bearing half.
    `gates.resume` sets `status="rejected"` for a rejection and NEVER un-sets it
    (gates.py:206-208) while still appending any later approval -- so a run can
    carry three decision rows one of which is a refusal, and `len(decisions) >= 3`
    would call that promotable. `tests/test_approve_server.py:266-289` pins that
    gap ON PURPOSE, because closing it inside `gates.resume` would revoke the
    `overridden` escape hatch. So the read happens at the promote sites, which are
    unattended: nobody is at a keyboard when a promote job runs.

    A MISSING RESULT IS A REFUSAL, NOT A PASS. `state.security is None` means
    nothing ever evaluated the block rule on this change, and that must not read
    the same as `verdict == "pass"` -- "did not run" versus "passed" is the defect
    this whole project exists to prevent.

    A TERMINAL STATUS IS ALSO A REFUSAL, and it is checked first because it is the
    only condition here that can be true while every other one is satisfied. A run
    can read `status="blocked"` while carrying a `pass` verdict, a `go` and three
    approvals: `gates.resume` writes `status` independently of the results, so the
    two can disagree, and MEASURED before this check existed `not_promotable`
    returned `""` for exactly that state. `run_stage._stage_promote` checks
    terminality itself as well, BEFORE calling this, because it needs to return a
    different exit code for it -- "this run had already ended" and "this run has
    not earned a promotion" are different facts. This check is what makes the
    predicate honest for the caller that does not make that distinction.
    """
    if state.status in _TERMINAL_STATUSES:
        return (f"this run already ended as status={state.status!r}; promoting "
                f"would overwrite an outcome an earlier stage decided")
    if state.security is None:
        return ("no security verdict on this run: nothing evaluated the block "
                "rule on this change, which is not the same as it passing")
    if state.security.verdict != "pass":
        return (f"the security verdict is {state.security.verdict!r}, not 'pass' "
                f"({len(state.security.blocking)} blocking finding(s))")
    if state.sre is None:
        return "no SRE verdict on this run: the sre stage never recorded one"
    if state.sre.verdict != "go":
        return f"the SRE verdict is {state.sre.verdict!r}, not 'go'"

    # READ every decision. One refusal refuses, whatever was appended after it.
    refused = sorted(d.gate for d in state.decisions if d.decision == "rejected")
    if refused:
        return (f"a human rejected {', '.join(refused)}, and gates.resume never "
                f"un-sets a rejection, so a later approval does not undo it")

    approved = {d.gate for d in state.decisions
                if d.decision in _APPROVING_DECISIONS}
    missing = [gate for gate in REQUIRED_GATES if gate not in approved]
    if missing:
        return (f"no approval recorded for {', '.join(missing)}: promoting would "
                f"claim a human decided something nobody was asked")
    return ""


def run_pipeline(ticket_id: str, ticket_text: str, *, poisoned: bool = False,
                 auto_approve: bool = True) -> RunState:
    """Walk one ticket through the whole pipeline. Returns the final RunState.

    The walk itself is _walk; this wrapper exists only to guarantee the ending
    is written down. _walk has seven `return state` exits, so a `gates.save`
    before each one would be seven chances to forget one — and the eighth exit
    somebody adds next month would be wrong by default. A finally clause is
    wrong by default in the safe direction instead: it also persists a run that
    died on an exception, which is exactly the run someone needs to inspect
    afterwards, and which no `return`-site save can reach at all.

    state is built here rather than in _walk so the finally clause has something
    to save even if _walk raises on its first line.

    WHICH PATH ANSWERED THE MODEL CALLS is reset before the walk and stamped in
    the `finally`, which is the same argument as the save one step further: seven
    `return` sites are seven chances to forget, and the run that raised is the one
    whose provenance is most worth knowing. `llm.last_source()` returns None when
    nothing called the model, and that is recorded as `""` rather than `"model"` --
    a run that never asked must not claim the model answered. `"fixture"` never
    downgrades to `"model"` inside `llm._record`, so any agent falling back labels
    the whole run `fixture`.
    """
    llm.reset_source()
    state = RunState(ticket_id=ticket_id, ticket_text=ticket_text)
    try:
        return _walk(state, poisoned=poisoned, auto_approve=auto_approve)
    finally:
        state.model_provenance = llm.last_source() or ""
        gates.save(state)
        # The index follows the run to its ending, in the `finally` beside `gates.save`
        # for the same reason that call is here: `_walk` has seven `return state` exits
        # and a call at each would be seven chances to forget one.
        run_index.update_status(state)
        # THE ENDING, REPORTED TO THE ISSUE THAT ASKED FOR IT, and closed.
        #
        # In the `finally` beside `gates.save` for the same reason that call is here:
        # `_walk` has seven `return state` exits, and a report at each would be seven
        # chances to forget one -- so the poisoned ending, the one that matters most,
        # would be the likeliest to go unreported.
        #
        # AFTER `gates.save`, deliberately. The saved state is the run's record; the
        # comment is a rendering of it. If the report somehow failed loudly the record
        # would still be on disk.
        #
        # `report_outcome` never raises, so this cannot swallow an exception `_walk`
        # was propagating -- a `finally` that raises replaces the original error, and
        # losing a real traceback to a failed comment would be the worse trade.
        integrations.host().report_outcome(state)


def _walk(state: RunState, *, poisoned: bool, auto_approve: bool) -> RunState:
    """The pipeline itself. Always call through run_pipeline, never directly."""
    _log(state, "system", "plan", "opened", summary=f"run started for {state.ticket_id}")
    ask = _auto_gate if auto_approve else _cli_gate

    # 1. PLAN ---------------------------------------------------------------
    # INDEX THE RUN AGAINST ITS TENANT. See run_stage.py's identical call and
    # tenancy/run_index.py: a no-op unless TENANT_DB is set, and it never raises.
    run_index.record_run(state)

    state.plan = agent_client.call_agent("planner", state)
    _log(state, "planner", "plan", "proposed", summary=f"{len(state.plan.tasks)} tasks")
    # ON THE ISSUE, necessarily: no PR exists yet. github_ops decides that from
    # the state rather than being told -- see github_ops._destination.
    _plan_comment(state, state.plan)

    # 2. GATE 1 -------------------------------------------------------------
    # _decide posts the gate comment; also on the issue, for the same reason.
    if not _decide(state, "gate1", ask):
        return state

    # 3. DEVELOP + REVIEW LOOP ---------------------------------------------
    # Two exits, and they are not the same outcome, so they do not share a log
    # line: one says the reviewer approved this diff, the other says the run
    # ran out of chances to make one it would.
    #
    # THE COMMENTS ARE QUEUED, NOT POSTED, and the reason is ordering rather than
    # efficiency: the PR does not exist until `open_pr` below, so posting from
    # inside this loop would resolve to a PR lookup for a branch that has no PR
    # (github_ops._destination reads `dev.branch`, which the DEVELOPER fills with
    # its own branch name before `open_pr` overwrites it). Every one of those
    # comments would degrade to a `comment://` ref -- delivered nowhere, on the
    # exact stages this task exists to make visible. So they are held and flushed
    # once there is a PR to hold them.
    #
    # A LIST, one entry per pass, which is what "append rather than replace"
    # means concretely: three revisions is part of the story a judge reads, and a
    # loop that kept only its last attempt would render a run that argued with
    # itself three times as a run that got it right first time.
    #
    # The PASS'S OWN results are captured, not re-read at flush time -- see
    # _develop_comment for what re-reading would silently produce.
    loop_results: list[tuple[DevResult, ReviewResult, int]] = []
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

    # 4. OPEN PR (Mariam's seam) -------------------------------------------
    state.dev = integrations.host().open_pr(state)
    _log(state, "system", "develop", "opened", summary=f"PR {state.dev.pr_url}",
         )

    # Now there is a PR, so the loop's queue can be flushed onto it -- in the
    # order the passes happened, developer then reviewer, one pair per attempt.
    for dev, review, attempt in loop_results:
        _develop_comment(state, dev, attempt)
        _review_comment(state, review, attempt)

    # 5. SECURITY (deterministic block rule) -------------------------------
    state.security = agent_client.call_agent("security", state)
    # scan_provenance is the answer to "did the scanners actually run, or did
    # this verdict come from a fixture?" -- a question the count in `summary`
    # cannot answer, because the fixture fallback produces a real count too.
    _log(state, "security", "security", "blocked" if state.security.verdict == "block" else "passed",
         verdict=state.security.verdict, summary=f"{len(state.security.blocking)} blocking",
         scan_provenance=state.security.scan_provenance)
    # ONE comment for both outcomes, and it is posted before the block branch
    # below rather than inside it. A security stage that only spoke when it
    # blocked would make the CLEAN run's most important claim -- that the
    # scanners ran and cleared the change -- invisible on the surface the judges
    # read, which is the same silence as a check that did not run.
    security_ref = _security_comment(state, state.security)
    if state.security.verdict == "block":
        state.status = "blocked"
        # The ref is written down rather than dropped on the floor. post_comment
        # cannot raise, so a delivery failure leaves no trace unless it is
        # recorded: it returns the comment's https:// URL when the reason
        # reached the PR, and comment://<run_id> when it did not. This log row
        # is the artifact -- runs/<run_id>.jsonl is what log.py calls the source
        # of truth the timeline UI renders -- and without the ref that file is
        # byte-identical whether the block was reported or evaporated into a 502.
        #
        # The ref comes from the comment posted just above rather than from a
        # SECOND post of the same explanation. Two posts would put the block
        # reason on the PR twice, and the timeline would classify the delivery of
        # whichever one happened to be logged -- so a run where the first
        # succeeded and the second failed would render as "nobody was told"
        # while the reason sat on the PR.
        ref = security_ref
        # The ref goes in artifact_ref AND stays in the summary. Both, because
        # they answer to different readers: artifact_ref is the field state.py
        # names for exactly this ("PR url, branch, path to findings json") and
        # is what agentorg/timeline.py classifies without parsing prose, while
        # the summary sentence is what every run already on disk carries and
        # what `cat runs/<run_id>.jsonl` shows in demo beat 5. Dropping it from
        # the summary would rewrite the demo script to save a duplicated string.
        _log(state, "system", "security", "blocked",
             summary=f"pipeline halted by block rule; block reason {ref}",
             artifact_ref=ref, scan_provenance=state.security.scan_provenance)
        return state

    # 5b. THE REVIEWER'S VERDICT IS TERMINAL --------------------------------
    # Reached only by the cap exit from the loop above: the revision budget is
    # spent and the reviewer is still asking for changes, so nobody has approved
    # this change and it must not promote. Treated as sre.verdict == "no_go" is
    # below, and "failed" rather than "rejected" because no human was asked.
    #
    # Placed AFTER the security stage, deliberately. The block rule is
    # deterministic code, and the whole premise of this pipeline is that the
    # block is not a model's judgement — so it must be evaluated on every run
    # that produced a diff, whatever the reviewer thought of that diff. Stopping
    # here first would invert that: on the poisoned ticket a competent reviewer
    # SHOULD object to the hardcoded credentials, the developer's safety net
    # re-inserts the key on every revision, so the cap would reliably exhaust
    # and the run would end "failed" without the scanners ever running. That
    # quietly downgrades "the poisoned ticket blocks every single time" into
    # "it fails at review", which is a different and weaker claim.
    #
    # So a block wins above and returns; only a run the scanners CLEARED can be
    # stopped here for never having been approved.
    if state.review.verdict != "approve":
        state.status = "failed"
        # action="failed", NOT "blocked". The scanners CLEARED this diff -- the
        # ordering above guarantees it -- so a `blocked` row renders
        # `⛔ BLOCKED — the change was stopped` on the timeline, which is this
        # pipeline's central claim asserted about a change nothing blocked.
        # `timeline._outcome` reads this action and never `state.status`.
        _log(state, "system", "review", "failed", verdict=state.review.verdict,
             summary=f"scanners passed, but the reviewer never approved after "
                     f"{state.revision_count} revisions; not promoting")
        return state

    # 6. GATE 2 -------------------------------------------------------------
    if not _decide(state, "gate2", ask):
        return state

    # 7. SRE ----------------------------------------------------------------
    # Measured HERE, not inside the agent, for the reason `RunState.poisoned` is a
    # field: under REMOTE_AGENTS=true `sre.run` executes in a container with no GitHub
    # token, so it would answer `unknown` without asking. This process holds the token
    # when there is one. Harmless on the local path -- `sre.run` prefers the field and
    # measures only when it is blank, so the answer is identical either way, taken one
    # call earlier.
    state.ci_status_measured = integrations.host().ci_status(state)
    state.sre = agent_client.call_agent("sre", state)
    _log(state, "sre", "sre", "reviewed", verdict=state.sre.verdict)
    # Before the no_go branch, for the same reason the security comment is before
    # its block branch: a no_go is the outcome most worth reading on the PR, and
    # a stage that only spoke on the happy path would go silent exactly when it
    # had something to say.
    _sre_comment(state, state.sre)
    if state.sre.verdict == "no_go":
        state.status = "failed"
        # THE ENDING IS WRITTEN DOWN. Before this row existed this branch set
        # `state.status = "failed"` and returned, logging nothing -- and no log
        # row carries `RunState.status`, so `timeline._outcome` had nothing to
        # read and rendered `… INCOMPLETE — run stopped at sre without an
        # ending`. INCOMPLETE says the run stopped without deciding; a no_go is a
        # DECISION by a stage that ran to completion. `verdict` carries the SRE's
        # own word because the revision-cap exit above writes `failed` too, and
        # the two render the same banner by design.
        _log(state, "sre", "sre", "failed", verdict=state.sre.verdict,
             summary=f"SRE returned {state.sre.verdict}; not promoting "
                     f"(CI {state.sre.ci_status})")
        return state

    # 8. GATE 3 + PROMOTE ---------------------------------------------------
    if not _decide(state, "gate3", ask):
        return state

    # THE PROMOTION IS CHECKED, NOT ASSUMED. Every stop above `return`s, so this
    # line is unreachable on any run that should not promote -- and that is
    # control flow, not a guard. See `not_promotable` for why the rule is stated
    # a second time where a test can execute it, and for why the decisions are
    # READ rather than counted.
    refusal = not_promotable(state)
    if refusal:
        state.status = "failed"
        _log(state, "system", "promote", "failed",
             summary=f"refused to promote: {refusal}")
        return state

    # THE MERGE IS WHAT MAKES THE PROMOTION TRUE, so it happens first and its row
    # goes down first. `github_ops.merge_pr` never raises and always returns a
    # ref, the same contract `post_comment` carries and for the same reason: the
    # next line records this run's ending, and a raise here would lose it.
    #
    # `promoted` MUST BE THE LAST ROW. `timeline._outcome` reads its banner off
    # the last row's action, so logging `merged` after it would render a shipped
    # run as `⇄ MERGED` and the ★ PROMOTED banner would never appear.
    ref = integrations.host().merge_pr(state)
    _log(state, "system", "promote", "merged", summary=f"merged {ref}",
         artifact_ref=ref)

    state.status = "promoted"
    _log(state, "system", "promote", "promoted", summary="change promoted")
    return state


if __name__ == "__main__":
    import sys
    poisoned = "--poisoned" in sys.argv
    # Without this flag the interactive gate is reachable only by importing
    # run_pipeline, which makes "the gates are real" a claim nobody can check
    # from a terminal. The default is unchanged: no flag, no prompts.
    interactive = "--interactive" in sys.argv
    tid = "DEMO-POISON" if poisoned else "DEMO-CLEAN"
    final = run_pipeline(tid, "Add a per-IP login rate limit.", poisoned=poisoned,
                         auto_approve=not interactive)
    print(f"\nrun_id={final.run_id}")
    print(f"status={final.status}")
    if final.security:
        print(f"security verdict={final.security.verdict}, blocking={len(final.security.blocking)}")
