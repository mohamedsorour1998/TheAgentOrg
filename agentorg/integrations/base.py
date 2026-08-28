"""The code-host interface — DERIVED from what graph.py calls, not designed fresh.

OWNER: Lane D.  The seam that stops GitHub being the substrate.

WHERE THE FIVE METHODS COME FROM. Read off `agentorg/graph.py` at 38da0c9, which
is the only module that drives the whole pipeline in one process:

    graph.py:114   github_ops.post_comment(state, body)      -> str   (via _comment)
    graph.py:486   github_ops.report_outcome(state)          -> str
    graph.py:549   github_ops.open_pr(state)                 -> DevResult
    graph.py:644   github_ops.ci_status(state)               -> str
    graph.py:691   github_ops.merge_pr(state)                -> str

`scripts/run_stage.py` calls the same five (313, 618, 705, 808, and post_comment
through `graph._comment`), and `agents/sre.py:163` calls `ci_status`. So the set
is closed: five methods, no sixth, and nothing invented.

`github_ops.deploy_note()` is DELIBERATELY NOT HERE. It reads the Bedrock
AgentCore control plane -- `list_agent_runtimes` -- and has nothing to do with
code hosting; it lives in `github_ops.py` for historical reasons only. Putting it
on this interface would oblige a GitLab or plain-git adapter to answer a question
about AWS runtimes, which is how an interface acquires a method every
implementation stubs. It stays a module-level function on `github_ops`.

=========================================================================
AN ABC, NOT A PROTOCOL, AND THAT IS THE LOAD-BEARING CHOICE
=========================================================================

A `typing.Protocol` is structural: an adapter that spells a method `post_commnet`
satisfies nothing and nobody finds out until a run reaches stage nine and dies on
`AttributeError` -- with the state already carrying `status="blocked"`. An ABC
refuses at CONSTRUCTION, before a ticket is touched.

It also buys the thing a Protocol cannot: the never-raises contract below is
enforced by THIS class rather than trusted to each adapter's author. A structural
interface can only document that promise; a nominal one can keep it.

=========================================================================
FOUR METHODS CANNOT RAISE. `open_pr` CAN, AND THAT ASYMMETRY IS DELIBERATE
=========================================================================

`post_comment`, `merge_pr`, `report_outcome` and `ci_status` are wrapped here:
each public method is final, calls the adapter's `_`-prefixed body, and converts
any `Exception` into that method's honest fail-safe value. The reason is the one
`github_ops.post_comment` already carries -- `graph.py` sets `status="blocked"`
and records the returned ref on the NEXT line, so a raise loses the record of the
block itself -- and it now holds for every adapter rather than for one.

`open_pr` IS NOT WRAPPED, and swallowing it would be a real defect. Two of its
failures must reach the caller:

  * `github_ops._ensure_offline_repo` REFUSES a git repository offline mode did
    not create. That refusal exists because the alternative measured out as
    `git checkout -B agent-org/<ticket>` inside a victim's checked-out worktree.
    A wrapper returning a placeholder `DevResult` there would proceed as though a
    branch existed.
  * a run with no branch has nothing for the four other methods to address --
    `_destination` resolves to the issue, `merge_pr` refuses, `ci_status` answers
    `unknown`. Continuing produces a run that reports work it did not do.

So the interface is not uniform on purpose. A uniform one would be tidier and
would convert the one raise that protects somebody's working tree into a silent
success.

`Exception`, never `BaseException`. `tests/conftest.py`'s guards raise
`pytest.fail`'s `Failed`, which derives from BaseException precisely so a blind
handler cannot absorb it -- see `test_the_blind_except_does_not_swallow_the_conftest_github_guard`.
Widening one of these handlers would put every test back on the live API with the
suite green.
"""

from __future__ import annotations

import abc
import hashlib
import logging
from collections.abc import Callable

from ..agents.security import _one_line
from ..state import DevResult, Finding, RunState

# ── THE REF VOCABULARY ────────────────────────────────────────────────────────
#
# The interface's real content is not the five method names -- any two adapters
# would agree on those -- it is WHAT A REF MAY SAY. `agentorg/timeline.py`
# classifies a block reason's delivery by splitting the ref on "://" and looking
# the scheme up in `_DELIVERY`; a scheme it does not recognise renders
# `UNRECOGNISED`, on the artifact a judge reads.
#
# So an adapter inventing `gitlab://` for a delivered comment would be correct in
# its own terms and would degrade the timeline for every run it touched. These
# three names are what an adapter is allowed to produce, and
# `tests/test_integration_interface.py` asserts this set EQUALS
# `timeline._DELIVERY`'s keys.
#
# THAT IS A SECOND DECLARATION, ON PURPOSE, and it is the same deliberate
# exception `tests/test_scoring_determinism.py` makes for the severity ranking: a
# copy is the only instrument that can detect a change in the original. Both
# directions fail -- a scheme added to the renderer and not offered to adapters
# is just as broken as the reverse.
SCHEME_DELIVERED_REMOTE = "https"      # posted to a real issue or pull request
SCHEME_DELIVERED_LOCAL = "local"       # written to disk; bytes landed
SCHEME_UNDELIVERED = "comment"         # attempted, reached nobody

DELIVERY_SCHEMES = frozenset({
    SCHEME_DELIVERED_REMOTE,
    SCHEME_DELIVERED_LOCAL,
    SCHEME_UNDELIVERED,
})

# `merge_pr`'s two failure shapes. WE declined versus THEY declined, kept apart
# because they call for different actions from whoever reads the timeline: one is
# a policy the pipeline enforced, the other is a conflict somebody must resolve.
# Copied from `github_ops.merge_pr`'s docstring rather than reinvented.
MERGE_REFUSED_PREFIX = "merge://refused/"
MERGE_FAILED_PREFIX = "merge://failed/"

# `ci_status`'s three answers. `unknown` is a first-class result, never an error,
# and it is the fail-safe: an unreachable host is `unknown`, never `passing`.
CI_PASSING = "passing"
CI_FAILING = "failing"
CI_UNKNOWN = "unknown"
CI_ANSWERS = frozenset({CI_PASSING, CI_FAILING, CI_UNKNOWN})


def undelivered_ref(state: RunState) -> str:
    """The ref that says "this was attempted and reached nobody".

    ONE writer, because two would drift and the drift is unobservable: a ref in
    the wrong shape still records as a ref, and the timeline would classify it
    `UNRECOGNISED` on exactly the runs worth reading.
    """
    return f"{SCHEME_UNDELIVERED}://{state.run_id}"


def scheme_of(ref: str) -> str:
    """The part before `://`, or `""` for a ref that carries no scheme.

    Same split `timeline._delivery` performs, exposed so a conformance test can
    ask the question without restating the parsing.
    """
    return ref.split("://", 1)[0] if "://" in ref else ""


# The branch a run's change lives on. `agent-org/<ticket>-<7 hex of sha1(diff)>`,
# which is `github_ops.open_pr`'s shape.
#
# A SECOND DECLARATION OF THAT SHAPE, and it is here because the CONFORMANCE
# SUITE FOUND ITS ABSENCE. The first `git.GitHost` kept whatever branch name the
# developer agent had put on `state.dev` -- the fixture's is
# `feat/login-rate-limit` -- and
# `test_open_pr_fills_the_branch_and_pr_url_on_every_adapter` failed by name:
#
#     git left dev.branch as 'feat/rate-limit'; open_pr must replace the agent's
#     branch name with the one it actually created
#
# That is not cosmetic. `github_ops._destination` routes a comment to the PULL
# REQUEST whenever `dev.branch` is truthy, so an adapter that keeps the agent's
# name sends every post-develop comment to a PR lookup for a branch nothing
# created -- and the comment quietly goes nowhere while the run stays green. The
# branch shape is a property of THIS PIPELINE, not of GitHub, so it belongs on the
# interface; `test_the_shared_branch_shape_matches_the_shipped_adapters` is the
# instrument that keeps this copy and `github_ops.open_pr`'s from drifting.
def branch_for(state: RunState, dev: DevResult) -> str:
    """The branch name this run's change belongs on. Stable per (ticket, diff)."""
    return f"agent-org/{state.ticket_id}-{hashlib.sha1(dev.diff.encode('utf-8')).hexdigest()[:7]}"


def _absorbed(method: str, exc: Exception, fallback: str) -> str:
    """Report a swallowed failure and hand back the fail-safe value.

    The shape `github_ops._undelivered` already uses, for the reason it uses it:
    one BOUNDED line at WARNING naming the cause. Bounded matters because a
    PyGithub error carries the whole JSON response body and this prints on a
    projector immediately above `status=blocked`, where a wall of text reads as a
    crash rather than as the block working.

    THE TRACEBACK IS NOT LOGGED HERE, and that is ruff's doing rather than a
    choice. `LOG014` fires on `exc_info=` outside an exception handler -- measured,
    `LOG014 exc_info= outside exception handlers` on the first draft -- and
    `BLE001` is satisfied only by a logging call it can statically resolve to the
    logging module INSIDE the handler. So the traceback stays at the `except`
    itself, in `CodeHost._guard`, exactly as `github_ops` puts it at each of its
    own handlers. The two rules together forbid folding both halves into a helper.

    The inline `logging.getLogger(...)` is not a style choice either: ruff cannot
    resolve a module-level alias, so `_log.warning(...)` turns
    `ruff check agentorg` red. Do not tidy this into `_log`.
    """
    logging.getLogger(__name__).warning(
        "%s raised and the interface absorbed it (%s: %s); answering %r instead",
        method, type(exc).__name__, _one_line(str(exc)), fallback,
    )
    return fallback


class CodeHost(abc.ABC):
    """Where a run's branch, comments, CI answer and merge live.

    Implementations: `integrations/github.py` (the shipped one),
    `integrations/memory.py` (the suite's double), `integrations/git.py` (the
    second adapter, which exists to prove this interface is not GitHub with the
    names changed).

    `name` is what `INTEGRATION_HOST` selects and what a conformance failure
    reports. `shipped` is False for an adapter that must never serve a real run;
    `integrations.host()` REFUSES those rather than falling through to the
    shipped one -- the same choice `QUEUE_BACKEND=sqs` makes, and for the same
    reason: an operator who asked for something we do not support must not get a
    real pull request on somebody's repository instead.
    """

    name: str = ""
    shipped: bool = False

    # ── the one method that may raise ─────────────────────────────────────────

    @abc.abstractmethod
    def open_pr(self, state: RunState) -> DevResult:
        """Create the branch carrying the developer's diff, and propose it.

        Returns the `DevResult` with `branch` and `pr_url` filled. MAY RAISE --
        see the module docstring for the two failures that must not be absorbed.
        """

    # ── the four that may not, each with its own fail-safe ───────────────────

    @abc.abstractmethod
    def _post_comment(self, state: RunState, body: str,
                      finding: Finding | None = None) -> str: ...

    @abc.abstractmethod
    def _merge_pr(self, state: RunState) -> str: ...

    @abc.abstractmethod
    def _report_outcome(self, state: RunState) -> str: ...

    @abc.abstractmethod
    def _ci_status(self, state: RunState) -> str: ...

    def _guard(self, method: str, call: Callable[[], str],
               fallback: Callable[[Exception], str]) -> str:
        """Run one adapter method; absorb any Exception into its fail-safe value.

        ONE HANDLER FOR ALL FOUR, and that is what ruff's own rules push you
        toward here rather than a matter of taste. `BLE001` is satisfied only by a
        logging call it can statically resolve to the logging module, carrying the
        traceback, INSIDE the handler -- measured on the first draft, which put the
        `exc_info` in a helper and got `BLE001` four times plus `LOG014` once for
        the `exc_info=` outside a handler. Four copies of that pair would be four
        chances for one to drift, and a handler that silently stopped logging is
        invisible.

        `fallback` is a CALLABLE of the exception, not a string, because
        `merge_pr`'s honest answer names the exception type
        (`merge://failed/<Type>`) while the other three do not depend on it.
        Passing a pre-computed string would have meant either losing that or
        computing it at every call site whether or not anything raised.

        `Exception`, never `BaseException`. `tests/conftest.py`'s guards raise
        `pytest.fail`'s `Failed`, which derives from BaseException precisely so a
        blind handler cannot absorb it. Widening this would put the whole suite
        back on the live GitHub API with every test green -- pinned by
        `test_the_interface_guard_does_not_swallow_the_conftest_github_guard`.
        """
        try:
            return call()
        except Exception as exc:
            logging.getLogger(__name__).debug(
                "%s failure traceback", method, exc_info=True,
            )
            return _absorbed(method, exc, fallback(exc))

    def post_comment(self, state: RunState, body: str,
                     finding: Finding | None = None) -> str:
        """Post one comment. Returns a ref in EVERY case and never raises.

        The destination -- issue or pull request -- is DERIVED FROM THE STATE by
        the adapter, never passed in. A `target=` parameter would mean every
        existing caller kept the default, so the planner's comment, the one call
        that most needs the issue because no PR exists yet, would silently go to
        the PR lookup. See `github_ops._destination`.
        """
        return self._guard(
            "post_comment",
            lambda: self._post_comment(state, body, finding),
            lambda exc: undelivered_ref(state),
        )

    def merge_pr(self, state: RunState) -> str:
        """Merge the run's change. Returns a ref; never raises.

        The fallback names the exception TYPE, matching `github_ops.merge_pr`
        exactly, so the log row can say what went wrong without the traceback.
        """
        return self._guard(
            "merge_pr",
            lambda: self._merge_pr(state),
            lambda exc: f"{MERGE_FAILED_PREFIX}{type(exc).__name__}",
        )

    def report_outcome(self, state: RunState) -> str:
        """Tell the surface that asked for the work how the run ended. Never raises.

        The last thing a run does, after the status is already decided, so a
        raise here would lose the ending in order to report a failure to report
        it.
        """
        return self._guard(
            "report_outcome",
            lambda: self._report_outcome(state),
            lambda exc: undelivered_ref(state),
        )

    def ci_status(self, state: RunState) -> str:
        """`passing`, `failing` or `unknown` for this run's head. Never raises.

        `unknown` on failure, NEVER `passing`: a commit nothing examined is not a
        green commit, and an outage must not read as a green build.
        """
        return self._guard(
            "ci_status",
            lambda: self._ci_status(state),
            lambda exc: CI_UNKNOWN,
        )
