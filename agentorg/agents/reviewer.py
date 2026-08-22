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

from .. import fixtures_loader, repo_snapshot
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

# Last-resort must_fix line, used only when the model asked for changes and
# named none. See _ensure_actionable.
NO_DETAIL_MUST_FIX = (
    "The reviewer requested changes but named none. Re-check the diff against "
    "the plan's acceptance criteria and fix whatever it does not satisfy."
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

    return "\n\n".join(parts)


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
