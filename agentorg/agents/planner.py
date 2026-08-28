"""Planner agent — turns a ticket into a PlanResult.

OWNER: Sorour. Falls back to the fixture whenever no model answers, so the
pipeline runs end-to-end on a machine with no AWS credentials.

There is deliberately no try/except here. `llm.structured` already absorbs every
model-side failure — unavailable, exception, chatty or unparseable reply — and
returns None, which is the one signal this function acts on. Wrapping the call
again would also swallow caller bugs (a bad model_cls, a fixture that no longer
validates) and quietly serve fixture data while the run looked live.
"""

from .. import fixtures_loader, repo_snapshot, retrieval
from ..common import llm
from ..state import PlanResult, RunState
from .reviewer import _record_retrieval

SYSTEM_PROMPT = """You are the Planner in a CI/CD pipeline. Read the ticket and
produce an implementation plan. Respond with ONE JSON object and nothing else —
no prose, no markdown fences. Shape:
{
  "tasks": ["<concrete task>", ...],
  "acceptance_criteria": ["<checkable criterion>", ...],
  "target_files": ["<path likely to change>", ...],
  "notes": "<short optional note>"
}
Do NOT write code. Keep every list non-empty.

THE TARGET IS A PYTHON 3.12 FLASK APPLICATION. `app/auth.py` holds a `login()` view
reading `request.form`, an `authenticate(username, password)` helper and a
`create_app()` factory; `tests/test_auth.py` uses pytest with a `client` fixture built
on `create_app()`. `redis` carries shared state and `os.environ` carries
configuration.

EVERY PATH IN `target_files` MUST BE ONE YOU CAN SEE IN THE REPOSITORY BELOW, or a new
file beside those. Do not name a path from a layout the repository does not use — a
plan naming files that do not exist makes the developer write a diff against a project
that is not there.

Write `acceptance_criteria` as things a PYTEST TEST COULD CHECK: a request, a response
status, a stored value. Another agent generates tests from these, so a criterion that
cannot be executed produces a test that cannot be either."""


def run(state: RunState) -> PlanResult:
    """Plan the ticket. Returns the fixture plan if no model is available.

    The fallback branch stamps `llm.record_fixture_fallback()`, and it has to be
    HERE rather than left to `llm`: nearly every test in this suite substitutes
    `llm.structured` itself, so on that path none of llm's internal recording
    runs and the provenance would read *unknown* on the one path every offline
    run takes. This branch is the fact -- it is where the fixture is loaded.
    """
    # THE REPOSITORY, so the plan names files that exist.
    #
    # MEASURED before this: for a Python Flask target the planner named
    # `app/controllers/password_resets_controller.rb`,
    # `config/initializers/rate_limit_config.rb` and `spec/requests/...` -- a RAILS
    # layout, and nothing in the repository resembles it. A plan naming files that do
    # not exist is not harmless: the developer then writes a diff against a project
    # that is not there.
    #
    # This agent needs the snapshot MORE than the others, because it is the one that
    # CHOOSES the paths every later stage works from.
    context = repo_snapshot.render()
    user_prompt = (
        f"{state.ticket_text}\n\n{context}" if context else state.ticket_text
    )

    # RETRIEVED CONTEXT -- how a change like this was planned, and refused, before.
    # `guard.CORPORA["planner"]` names `repo-history` only: this agent chooses paths and
    # writes criteria, and the `conventions` corpus records rulings about a DIFF that
    # does not exist yet.
    #
    # THE QUERY IS THE TICKET, which is all this stage has -- there is no diff, no plan
    # and no review at this point in the run. Stated because the other consumers' queries
    # are deliberately two-part and this one's being one-part is a fact about the stage
    # rather than an omission.
    #
    # Nothing on this path can reach a verdict. `planner` is a drafting consumer, and the
    # security verdict is reached three stages later from scanner findings over a diff
    # this agent never sees.
    text, corpora, count = retrieval.context_for("planner", state.ticket_text)
    if text:
        user_prompt = f"{user_prompt}\n\n{text}"
    _record_retrieval(state, corpora, count, state.ticket_text)

    result = llm.structured(PlanResult, SYSTEM_PROMPT, user_prompt)
    if result is None:
        llm.record_fixture_fallback()
        return fixtures_loader.plan()
    return result
