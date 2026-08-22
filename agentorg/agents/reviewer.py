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

from .. import fixtures_loader
from ..common import llm
from ..state import ReviewResult, RunState

SYSTEM_PROMPT = """You are the Reviewer in a CI/CD pipeline. Read the unified
diff and judge whether it correctly and safely implements the plan. Respond with
ONE JSON object and nothing else. Shape:
{
  "verdict": "approve" | "changes_requested",
  "comments": [{"file": "<path>", "line": <int>, "note": "<text>"}],
  "must_fix": ["<blocking issue to fix>", ...]
}
Use "changes_requested" ONLY for real correctness or safety problems, and then
list each one in must_fix. If the diff is acceptable, return "approve" with an
empty must_fix. Do not request changes for style nitpicks."""

# Last-resort must_fix line, used only when the model asked for changes and
# named none. See _ensure_actionable.
NO_DETAIL_MUST_FIX = (
    "The reviewer requested changes but named none. Re-check the diff against "
    "the plan's acceptance criteria and fix whatever it does not satisfy."
)


def _prompt(state: RunState) -> str:
    diff = state.dev.diff if state.dev else ""
    tasks = "\n- ".join(state.plan.tasks) if state.plan else ""
    return f"PLAN TASKS:\n- {tasks}\n\nDIFF UNDER REVIEW:\n{diff}"


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
