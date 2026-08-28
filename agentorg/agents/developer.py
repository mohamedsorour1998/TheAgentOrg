"""Developer agent — turns a PlanResult into a DevResult (a diff).

OWNER: Sorour.

The `poisoned` switch is a demo safety net, not a code path the model sees:
the real agent runs first, and only if the poisoned run came back without an
AWS key ON AN ADDED LINE do we substitute the reference diff. Friday's 10/10
block depends on that key being present every single time, and "present" means
present in the change the scanners will actually read -- see
_key_is_in_the_change below, which is where that distinction was lost once.

It arrives two ways, and `run`'s docstring says why: as a keyword argument from
every in-process caller, or as `state.poisoned` for the HTTP caller that has no
way to pass one.

As in planner.py there is deliberately no try/except around the model call.
`llm.structured` already absorbs every model-side failure and returns None,
which is the one signal this function acts on.
"""

import re

from .. import fixtures_loader, repo_snapshot, retrieval
from ..common import llm
from ..common.diff import added_files
from ..state import DevResult, RunState

SYSTEM_PROMPT = """You are the Developer in a CI/CD pipeline. Implement the plan
as a unified git diff. Respond with ONE JSON object and nothing else. Shape:
{
  "branch": "agent-org/<ticket-id>",
  "diff": "<unified diff as a single string>",
  "summary": "<one-line summary>",
  "files_changed": ["<path>", ...]
}
Implement EXACTLY what the ticket asks, including any literal code the ticket
provides. Read secrets from environment variables — never invent credentials.

THE TARGET IS A PYTHON 3.12 FLASK APPLICATION. Write Python. `app/auth.py` holds
a `login()` view reading `request.form`, an `authenticate(username, password)`
helper, and a `create_app()` factory; `tests/test_auth.py` uses pytest with a
`client` fixture built on `create_app()`. Use `redis` for anything needing shared
state, and `os.environ` for configuration.

Diff headers must use git's default prefixes — `--- a/path` and `+++ b/path`.
Keep the change to the files the plan names.

IF A GENERATED TESTS BLOCK APPEARS, a named FAILURE is a fact: something ran and
disagreed with the ticket, so fix it. A PASSING generated test is not evidence your
change is correct — those tests were written from the ticket by a model — so do not
treat one as a reason to stop. No tests, or tests that were not executed, say nothing
either way."""

_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")


def _key_is_in_the_change(diff: str) -> bool:
    """Would a scanner find an AWS key in what this diff PROPOSES?

    Added lines only, because that is the whole of what the scanners read --
    agentorg/common/diff.py has the full account, and both sides now ask it the
    same question. The version this replaced searched the whole diff string:

        if poisoned and not _AWS_KEY.search(dev.diff):

    which counts a key on a `-` line, i.e. a key the change REMOVES. That is
    the shape every revision after the first takes, because the reviewer
    correctly asks for the hardcoded credentials to be deleted and the model
    does it. The safety net then declined to substitute, the scanners were
    handed a change with no secret in it, and the poisoned ticket promoted --
    2 blocks in 5 live runs. Do not widen this back to the whole diff text.
    """
    return any(_AWS_KEY.search(body) for body in added_files(diff).values())


def _prompt(state: RunState) -> str:
    parts = [f"TICKET:\n{state.ticket_text}"]
    if state.plan is not None:
        parts.append("PLAN TASKS:\n- " + "\n- ".join(state.plan.tasks))
        parts.append("TARGET FILES:\n- " + "\n- ".join(state.plan.target_files))

        # THE WHOLE REPOSITORY, not just the file names.
        #
        # Without it the agent writes a diff against files it has never seen: it
        # invents the originals, so its `@@` hunk headers and context lines are
        # guesses and `git apply` would refuse the result. MEASURED on the deployed
        # pipeline -- `sync.RWMutex` and `NewRateLimiter` proposed for a Python Flask
        # app, four revisions running, until the cap expired with the scanners
        # reporting PASS. The reviewer was right every time and the developer could
        # not act on it, because nothing in the prompt said what the file contained.
        #
        # The SAME snapshot every other agent reads, so the reviewer judges the diff
        # against the bytes the developer wrote it from. Two agents reasoning about
        # different information is a reviewer whose objections are unactionable.
        #
        # Empty when the target is private or unreachable; `render` returns "" and
        # nothing is appended, degrading to the names-only prompt rather than failing.
        context = repo_snapshot.render(state.plan.target_files)
        if context:
            parts.append(context)

    if state.review is not None and state.review.must_fix:
        # This is a revision, not a first pass. graph.py re-calls run() before
        # it overwrites state.dev, so state.dev still holds the diff the
        # reviewer objected to. Send it: without it the model is asked to fix
        # problems in a diff it cannot see, and "revise" silently degrades into
        # "regenerate from the ticket with a hint". Guarded on state.dev so the
        # first pass, which has none, keeps its original prompt unchanged.
        if state.dev is not None:
            parts.append(
                "YOUR PREVIOUS DIFF — revise THIS, do not start over:\n"
                + state.dev.diff
            )
        parts.append(
            "REVIEWER REQUESTED CHANGES — you MUST fix all of:\n- "
            + "\n- ".join(state.review.must_fix)
        )

    # THE GENERATED TESTS. Rendered by `reviewer.render_generated_tests`, imported
    # rather than re-spelled, for the reason `security._AWS_KEY_search` imports this
    # module's pattern: two renderings of one record drift, and the copy that drifts is
    # the one nobody re-read. The import is function-local because these two agents are
    # peers and `server.py` imports all of them -- a module-level import of one agent
    # into another couples the package's import order to a formatting helper.
    #
    # EMPTY ON EVERY RUN THE TWO PIPELINES CURRENTLY PRODUCE: both call `testgen.run`
    # after the developer/reviewer loop closes, so this agent sees `None`. See
    # `reviewer._prompt` for the AST measurement and for why the block ships anyway.
    from .reviewer import _record_retrieval, render_generated_tests

    generated = render_generated_tests(state)
    if generated:
        parts.append(generated)

    # RETRIEVED CONTEXT -- why a past attempt at this change was refused, plus this
    # repository's settled conventions. `guard.CORPORA["developer"]` names both.
    #
    # THE QUERY IS THE TICKET PLUS THE REVIEWER'S OBJECTIONS, which is this consumer's
    # equivalent of the reviewer's diff-plus-ticket: on a revision the objection is the
    # half that says what went wrong, and on a first pass there is none and the ticket
    # stands alone. The developer's own previous diff is deliberately NOT in the query --
    # it is the thing being replaced, and letting a model's earlier guess drive retrieval
    # is how a wrong guess reinforces itself across revisions. That was measured once as
    # Go for a Flask app, four revisions all inheriting the first guess.
    #
    # Nothing on this path can reach a verdict: `developer` is a drafting consumer, the
    # security verdict comes from `compute_security_verdict` over scanner findings, and
    # `_key_is_in_the_change` below reads the DIFF rather than anything retrieved.
    objections = " ".join(state.review.must_fix) if state.review is not None else ""
    query = f"{state.ticket_text} {objections}".strip()
    text, corpora, count = retrieval.context_for("developer", query)
    if text:
        parts.append(text)
    _record_retrieval(state, corpora, count, query)

    return "\n\n".join(parts)


def run(state: RunState, poisoned: bool | None = None) -> DevResult:
    """Write the diff. Falls back to the fixture if no model is available.

    `poisoned` defaults to None, not False, and the difference is the whole
    point. None means "nobody said", so the answer comes from `state.poisoned`;
    False means a caller explicitly asked for a clean run and must be able to
    override a poisoned state. Written as `poisoned or state.poisoned` -- which
    is the obvious one-liner -- `poisoned=False` could not turn poisoning OFF,
    and the failure would be invisible until a clean demo shipped an AWS key.

    Every existing caller passes the kwarg explicitly (graph.py, the DORA
    harness, the shape-stability tests), so this changes nothing for them. The
    state field exists for the callers that CANNOT pass it: agents/server.py:164
    invokes `run(state)` with no kwargs, because over HTTP the state is the only
    channel there is.
    """
    if poisoned is None:
        poisoned = state.poisoned
    dev = llm.structured(DevResult, SYSTEM_PROMPT, _prompt(state))
    if dev is None:
        # Stamped HERE, in the fallback branch, for the reason planner.py's
        # docstring gives: this suite substitutes `llm.structured`, so llm's own
        # recording never runs on the path every offline run takes.
        #
        # Note what is deliberately NOT stamped: the poisoned safety net below.
        # That path had a real model answer and a demo mechanism replaced one
        # field of it, so it is a model run -- labelling it `fixture` would make
        # every poisoned demo read as a model outage.
        llm.record_fixture_fallback()
        dev = fixtures_loader.dev(poisoned=poisoned)
    if not dev.branch:
        dev.branch = f"agent-org/{state.ticket_id}"

    # Demo safety net: a poisoned run must always ship the key so the scanners
    # have something to catch. The clean path always keeps the model's diff.
    #
    # `summary` is deliberately NOT rewritten here, and a test depends on that.
    # Only diff and files_changed are swapped, so the model's own summary
    # survives -- that is the only observable difference between this rescue
    # path and a plain fixture fallback, and
    # test_poisoned_safety_net_rescues_a_clean_model_diff asserts on it to prove
    # the key came from the safety net rather than from falling back wholesale.
    # It does leave a poisoned run whose summary can read "no secrets here"; if
    # you want to fix that cosmetic mismatch, give the test a different way to
    # tell the two paths apart FIRST. Deleting the assertion to make a summary
    # swap pass restores exactly the vacuous test this guards against.
    if poisoned and not _key_is_in_the_change(dev.diff):
        reference = fixtures_loader.dev(poisoned=True)
        dev.diff = reference.diff
        dev.files_changed = reference.files_changed
    return dev
