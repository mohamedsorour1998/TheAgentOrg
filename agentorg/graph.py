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
from collections.abc import Callable

from . import gates, github_ops, log
from .common import agent_client, config
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
    return github_ops.post_comment(state, body)


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
    """
    return _comment(state, "security", [
        (f"**{security.verdict.upper()}** — {len(security.blocking)} blocking "
         f"finding(s) of {len(security.findings)} total"),
        f"_provenance: {security.scan_provenance or 'unknown'}_",
        "",
        *_bullets([f"`{f.tool}` **{f.rule}** ({f.severity}) at `{f.file}:{f.line}` "
                   f"— {f.description}" for f in security.blocking]),
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
    path = gates.pause(state, gate)
    print(f"\n[{gate}] paused. state saved -> {path}")
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
    """
    state = RunState(ticket_id=ticket_id, ticket_text=ticket_text)
    try:
        return _walk(state, poisoned=poisoned, auto_approve=auto_approve)
    finally:
        gates.save(state)


def _walk(state: RunState, *, poisoned: bool, auto_approve: bool) -> RunState:
    """The pipeline itself. Always call through run_pipeline, never directly."""
    _log(state, "system", "plan", "opened", summary=f"run started for {state.ticket_id}")
    ask = _auto_gate if auto_approve else _cli_gate

    # 1. PLAN ---------------------------------------------------------------
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
    state.dev = github_ops.open_pr(state)
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
        _log(state, "system", "review", "blocked", verdict=state.review.verdict,
             summary=f"scanners passed, but the reviewer never approved after "
                     f"{state.revision_count} revisions; not promoting")
        return state

    # 6. GATE 2 -------------------------------------------------------------
    if not _decide(state, "gate2", ask):
        return state

    # 7. SRE ----------------------------------------------------------------
    state.sre = agent_client.call_agent("sre", state)
    _log(state, "sre", "sre", "reviewed", verdict=state.sre.verdict)
    # Before the no_go branch, for the same reason the security comment is before
    # its block branch: a no_go is the outcome most worth reading on the PR, and
    # a stage that only spoke on the happy path would go silent exactly when it
    # had something to say.
    _sre_comment(state, state.sre)
    if state.sre.verdict == "no_go":
        state.status = "failed"
        return state

    # 8. GATE 3 + PROMOTE ---------------------------------------------------
    if not _decide(state, "gate3", ask):
        return state
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
