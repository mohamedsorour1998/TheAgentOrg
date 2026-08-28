"""Reviewer agent — approve or request changes on a DevResult.

OWNER: Sorour.

When the verdict is changes_requested the graph loops back to the developer,
capped by config.MAX_REVISION_LOOPS. That loop lives in graph.py; this file
only produces the verdict.

As in planner.py and developer.py there is deliberately no try/except around
the model call. `llm.structured` already absorbs every model-side failure —
unavailable, exception, chatty or unparseable reply — and returns None, which
is the one signal this function acts on. Wrapping it again would also swallow
caller bugs and serve fixture data while the run looked live.
"""

from .. import fixtures_loader, repo_snapshot, retrieval
from ..common import llm
from ..state import RetrievalRecord, ReviewResult, RunState

SYSTEM_PROMPT = """You are the Reviewer in a CI/CD pipeline. Read the unified
diff and judge whether it correctly and safely implements the plan. Respond with
ONE JSON object and nothing else. Shape:
{
  "verdict": "approve" | "changes_requested",
  "comments": [{"file": "<path>", "line": <int>, "note": "<text>"}],
  "must_fix": ["<blocking issue to fix>", ...]
}

THE TARGET IS A PYTHON 3.12 FLASK APPLICATION, and the change is a focused edit to
an existing small file — not a production-grade library.

YOUR REVIEW BUDGET IS SMALL AND REAL: a handful of rounds, then the run ends. A
change you keep sending back does not get better -- it runs out of revisions and
ships nothing, even when the security scanners have already cleared it.

So the standard is not "is this what I would have written". It is "would shipping
this be WRONG or UNSAFE":

  * it does not do what the ticket asked
  * it hardcodes a credential, or logs one
  * it would crash, or it references something undefined
  * it is written in the wrong language

APPROVE EVERYTHING ELSE. If your objection is a matter of degree, taste, robustness
or completeness, approve and put it in `comments`, where it is recorded on the pull
request without costing a round. Specifically approve despite:
  * a different storage or library choice you would have preferred
  * missing headers, cleanup timers, retry logic, locks or error handling the ticket
    did not ask for
  * configurability beyond what the ticket specified
  * missing tests, unless the ticket asked for tests
  * anything you would phrase as "consider", "ideally", "should also" or "could be
    improved"

AND IF YOU HAVE ALREADY ASKED ONCE: a later round is for a problem the developer did
not fix, not for a new preference you noticed on re-reading. If the thing you first
objected to is now addressed, approve — even if the fix is not how
you would have done it.

A change that implements the ticket in Python with no credential in it gets
"approve" and an empty must_fix. `comments` is where the rest goes."""

# ── WHAT A GENERATED TEST PROVES, AND THE ASYMMETRY THAT IS THE WHOLE POINT ──
#
# Lane G's `testgen` runs before the security stage and fills
# `RunState.generated_tests`. `binding` is true ONLY when a failure was OBSERVED, and
# the reviewer must be told that in those terms rather than handed two integers.
#
# The danger is not that the reviewer ignores a red test. It is that it reads a GREEN
# one as evidence and approves a change on the strength of it. `passed=3` looks
# exactly like three tests that verified something; what it actually means is that a
# model wrote three assertions from the ticket and they ran. Lane G says so in
# `testgen.GREEN_PROVES` and it is stated here too, because a caveat that reaches
# `notes` and not the prompt is a caveat the reviewer never sees.
#
# `failed > 0` is a FACT -- something ran and disagreed. That half is worth acting on,
# and it belongs in `must_fix` because it is the one input here that is not a
# judgement.
_GENERATED_TEST_GUIDANCE = """
HOW TO READ THE GENERATED TESTS, if a GENERATED TESTS block appears below. They were
written from the ticket by a model, not derived from a specification, and they are NOT
a review you can defer to:

  * failed > 0 is a FACT: something ran and disagreed with the ticket. Treat a named
    failure as a real defect and put it in must_fix.
  * passed with 0 failures proves only that the generator produced something that RAN.
    It is NOT evidence the change is correct, and it is NOT a reason to approve
    anything you would otherwise have blocked.
  * no tests, or tests that were generated and NOT EXECUTED, say NOTHING about the
    change. Do not treat a missing or unexecuted test as either a pass or a defect.
  * missing tests are not blocking unless the ticket asked for tests -- that rule is
    unchanged and the generated tests do not override it."""

# APPENDED rather than spliced into the literal above, so the two halves stay separable:
# `test_the_reviewer_prompt_distinguishes_wrong_from_merely_different` reads
# `SYSTEM_PROMPT` and the M1 guidance is a different claim from the M4 blocking
# standard. One string with both in it makes either edit look like a change to the other.
SYSTEM_PROMPT = SYSTEM_PROMPT + "\n" + _GENERATED_TEST_GUIDANCE

# Last-resort must_fix line, used only when the model asked for changes and
# named none. See _ensure_actionable.
NO_DETAIL_MUST_FIX = (
    "The reviewer requested changes but named none. Re-check the diff against "
    "the plan's acceptance criteria and fix whatever it does not satisfy."
)


def render_generated_tests(state: RunState) -> str:
    """The GENERATED TESTS block, or `""` when the stage did not run.

    HERE RATHER THAN IN EACH AGENT, and the developer imports it from this module, for
    the reason `security._AWS_KEY_search` imports the developer's pattern instead of
    re-spelling it: two renderings of the same three fields drift, and the copy that
    drifts is by definition the one nobody re-read. The reviewer owns it because the
    reviewer is the consumer whose verdict the wording is calibrated against.

    `binding` IS RENDERED AS A WORD, NOT A BOOLEAN. `binding=False` beside `passed=3`
    invites the reading "not binding, so ignore it"; the honest sentence is that a green
    result is not evidence, which is Lane G's `GREEN_PROVES` claim reaching the prompt
    rather than stopping at `notes`.

    `notes` IS FORWARDED VERBATIM. It carries Lane G's caveat, the quarantine report and
    the `NOT EXECUTED` marker, and a renderer that summarised it would be a second
    declaration of facts Lane G already words carefully. TODAY IT IS THE ONLY THING
    DISTINGUISHING two cases that share `passed=0 failed=0`: generated-but-not-executed
    (`workdir=None`, which is what both pipelines pass) and a genuinely green zero-test
    run.
    """
    generated = state.generated_tests
    if generated is None:
        return ""
    if generated.failed:
        weight = (
            "A FAILURE WAS OBSERVED, so this is BINDING: something ran and disagreed "
            "with the ticket. Name it in must_fix."
        )
    else:
        weight = (
            "No failure was observed, so this is NOT evidence the change is correct -- "
            "these tests were written from the ticket by a model. Do not approve "
            "anything on the strength of it."
        )
    return (
        f"GENERATED TESTS ({generated.source or 'unknown source'}):\n"
        f"{generated.passed} passed, {generated.failed} failed across "
        f"{len(generated.files)} file(s). {weight}\n"
        f"generator notes: {generated.notes}"
    )


def _prompt(state: RunState) -> str:
    diff = state.dev.diff if state.dev else ""
    tasks = "\n- ".join(state.plan.tasks) if state.plan else ""
    parts = [f"PLAN TASKS:\n- {tasks}", f"DIFF UNDER REVIEW:\n{diff}"]

    # THE REPOSITORY WITH THIS DIFF APPLIED, which is not the same thing the
    # developer saw and should not be.
    #
    # The developer wanted the file as it stands, because it was about to change it.
    # The reviewer wants the file AS THE CHANGE WOULD LEAVE IT -- otherwise it is
    # handed an original plus a patch and asked to apply the patch in its head, and
    # that is precisely the work that produced "Missing import for the authenticate
    # function" as a blocking issue about an import defined twenty lines above the
    # hunk.
    #
    # Passing `diff` is what switches `render` from before-view to after-view.
    if state.plan is not None:
        context = repo_snapshot.render(state.plan.target_files, diff=diff)
        if context:
            parts.append(context)

    # THE GENERATED TESTS, and the ordering below them is deliberate: they arrive after
    # the diff so a failure reads as a fact about the change in view.
    #
    # EMPTY ON EVERY RUN THE TWO PIPELINES CURRENTLY PRODUCE, and that is a fact about
    # the wiring rather than about this block. `graph._walk` and `run_stage._stage_develop`
    # both call `testgen.run` AFTER the developer/reviewer loop has finished -- measured
    # over the AST: the `while` loop closes before `state.generated_tests` is assigned.
    # So the reviewer sees `None` here on every pass today and this renders "".
    #
    # WRITTEN FOR THE SHAPE, NOT FOR TODAY'S VALUE, deliberately. Moving `testgen` before
    # the loop is a change to `graph.py` and `run_stage.py`, which are the integrator's
    # files; when it happens this needs no edit. The alternative -- omitting the block
    # until the call sites move -- means the prompt half and the wiring half each look
    # unnecessary while the other is missing, which is how both stay undone.
    generated = render_generated_tests(state)
    if generated:
        parts.append(generated)

    # RETRIEVED CONTEXT -- prior objections and this repository's settled conventions.
    #
    # THE QUERY IS THE DIFF PLUS THE TICKET, and that is measured rather than chosen.
    # Lane H's `measure.py` reports the scores: the diff alone ranks `history-0001` --
    # the per-IP-versus-per-account rejection -- third at 12, while diff+ticket ranks it
    # second at 25. A plan mismatch is a RELATION between what was asked and what was
    # written, and the diff carries only half of it.
    #
    # `retrieval.context_for` refuses any consumer not on its allow-list and reads
    # `config.RETRIEVAL_ENABLED` itself, through the module, so the disabled case returns
    # ("", [disabled per corpus], 0) rather than needing a knob check here. Nothing on
    # this path can reach a verdict: the reviewer's verdict is ADVISORY -- graph.py loops
    # on it and never stops -- and `compute_security_verdict` is reached through a
    # different agent that has no consumer name capable of asking for text.
    text, corpora, count = retrieval.context_for(
        "reviewer", f"{diff} {state.ticket_text}"
    )
    if text:
        parts.append(text)
    # THE RECORD IS WRITTEN WHETHER OR NOT THE TEXT WAS USED, because `context_for`
    # returns provenance for the disabled and empty cases too and those are the facts a
    # reader most needs. Guarded on `state.retrieval is None` so a later consumer in the
    # same run cannot silently erase an earlier one's record -- `state.py` is frozen and
    # `RetrievalRecord` has no per-consumer key, so append-to-the-lists is the only
    # merge available.
    _record_retrieval(state, corpora, count, f"{diff} {state.ticket_text}")

    return "\n\n".join(parts)


def _record_retrieval(
    state: RunState, corpora: list[str], count: int, query: str
) -> None:
    """Accumulate onto `RunState.retrieval`. ADDITIVE, never replacing.

    Two agents retrieve in one run (this one every pass, plus the developer), and
    `RetrievalRecord` declares three list-shaped fields and no consumer key -- `state.py`
    is the frozen contract, so a fourth field is not this lane's to add. Assigning a
    fresh record would therefore make the LAST retrieval look like the only one, which is
    the shape of the bug `graph.py`'s `loop_results` list exists to avoid: append in
    intent, replace in substance, green either way.

    Corpus entries are DEDUPLICATED and queries are not. A corpus reporting `retrieved`
    on three passes is one fact; three identical queries are three real searches, and
    collapsing them would understate what the run did. Order is preserved rather than
    sorted, so the record reads in the order the run retrieved.
    """
    if state.retrieval is None:
        state.retrieval = RetrievalRecord()
    for entry in corpora:
        if entry not in state.retrieval.corpora:
            state.retrieval.corpora.append(entry)
    state.retrieval.documents += count
    state.retrieval.queries.append(query)


def _ensure_actionable(result: ReviewResult) -> ReviewResult:
    """Guarantee a changes_requested verdict never travels with an empty must_fix.

    This is the one invariant this file owes the rest of the pipeline, and the
    reason is not local. graph.py loops back to the developer on any verdict
    that is not "approve", but developer._prompt attaches the previous diff and
    the reviewer's notes only `if state.review.must_fix`. So a changes_requested
    carrying an empty must_fix sends the developer a plain FIRST-PASS prompt:
    it regenerates from the ticket rather than revising the diff that was
    objected to, the run spends all three revisions doing it, and no test, log
    line or exception marks the difference. A loud failure would be fine; this
    one is silent, which is why it is closed here rather than left to chance.

    SYSTEM_PROMPT already instructs the model to list each issue in must_fix.
    That is an instruction, not a guarantee — the field defaults to [] in
    state.py, so a model that returns the verdict and forgets the list still
    validates cleanly.

    The verdict itself is never rewritten. Downgrading a detail-free
    changes_requested to "approve" would also satisfy the developer's guard,
    but it discards a real objection the model did make; synthesising the
    missing detail keeps the objection and costs at most the revisions the cap
    already bounds. Model comments are preferred over the fixed line so the
    developer gets the reviewer's actual words when there are any.

    Applied to the fixture path too, so the invariant is a property of run()
    rather than of the model branch alone. It is a no-op on today's fixture,
    which approves.
    """
    if result.verdict != "changes_requested" or result.must_fix:
        return result
    if result.comments:
        result.must_fix = [f"{c.file}:{c.line} {c.note}" for c in result.comments]
    else:
        result.must_fix = [NO_DETAIL_MUST_FIX]
    return result


def run(state: RunState) -> ReviewResult:
    """Review the diff. Returns the fixture verdict if no model is available.

    `llm.record_fixture_fallback()` is stamped in the fallback branch, for the
    reason planner.py's docstring gives: this suite substitutes `llm.structured`,
    so llm's own recording never runs on the path every offline run takes.
    """
    result = llm.structured(ReviewResult, SYSTEM_PROMPT, _prompt(state))
    if result is None:
        llm.record_fixture_fallback()
        result = fixtures_loader.review()
    return _ensure_actionable(result)
