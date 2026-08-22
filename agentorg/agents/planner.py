"""Planner agent — turns a ticket into a PlanResult.

OWNER: Sorour. Falls back to the fixture whenever no model answers, so the
pipeline runs end-to-end on a machine with no AWS credentials.

There is deliberately no try/except here. `llm.structured` already absorbs every
model-side failure — unavailable, exception, chatty or unparseable reply — and
returns None, which is the one signal this function acts on. Wrapping the call
again would also swallow caller bugs (a bad model_cls, a fixture that no longer
validates) and quietly serve fixture data while the run looked live.
"""

from .. import fixtures_loader, repo_snapshot
from ..common import llm
from ..state import PlanResult, RunState

SYSTEM_PROMPT = """You are the Planner in a CI/CD pipeline. Read the ticket and
produce an implementation plan. Respond with ONE JSON object and nothing else —
no prose, no markdown fences. Shape:
{
  "tasks": ["<concrete task>", ...],
  "acceptance_criteria": ["<checkable criterion>", ...],
  "target_files": ["<path likely to change>", ...],
  "notes": "<short optional note>"
}
Do NOT write code. Keep every list non-empty."""


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
    result = llm.structured(PlanResult, SYSTEM_PROMPT, user_prompt)
    if result is None:
        llm.record_fixture_fallback()
        return fixtures_loader.plan()
    return result
