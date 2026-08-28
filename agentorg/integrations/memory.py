"""The in-memory adapter — the suite's double, and a recorder.

OWNER: Lane D.  Plan D4: "an in-memory adapter for tests, replacing per-test
stubs".

WHAT IT REPLACES, and why the existing stubs were not enough. Across the suite,
tests reach the code host by patching one function at a time:

    monkeypatch.setattr(github_ops, "_repo", lambda: FakeRepo())
    monkeypatch.setattr(github_ops, "_note_locally", _record_note)
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "open_pr", _open_pr_on_local_git)

Each is correct for its own test. Together they are this repository's named
pattern -- "a test double that cannot express the failing case" -- because a stub
of ONE function leaves the other four on the real path, so a test asserting on
comments still does real local git, and no double in the suite can answer "how
many comments did this run post, and where did each go?" without reading a file.

TWO PROPERTIES A PER-FUNCTION STUB CANNOT HAVE:

  * NO I/O AT ALL. No network, no git subprocess, no disk. `github_ops`' offline
    path does REAL git -- conftest guard 3 exists because 22 tests that never
    mention git spawned 224 git child processes through it -- and this adapter
    spawns none.
  * A RECORD, not a side effect. `comments`, `pulls`, `merges` and `outcomes` are
    lists on the instance, so a test asserts on what was asked for rather than on
    what a file ended up containing.

=========================================================================
IT IS NOT A YES-MAN, AND THAT IS THE REASON IT IS USEFUL
=========================================================================

A double that always succeeds is the failing case it cannot express. So this one
reproduces the REFUSALS the real adapter makes, from the same inputs:

  * a ticket id that is not an issue number, before any PR exists, has nowhere to
    post -- so `post_comment` returns `comment://<run_id>`. It calls
    `github_ops._issue_number` to decide, rather than parsing the id itself,
    because that regex is the one this repository has already paid for
    (`\\A#?([0-9]+)\\Z`, `[0-9]` not `\\d`, both anchors load-bearing) and a
    second parse would be a second answer with nothing recording which was used.
  * a comment before `open_pr` goes to the ISSUE; after, to the PULL REQUEST. It
    calls `github_ops._destination` for the same reason.
  * `merge_pr` refuses a run that has not earned it, through
    `github_ops._merge_refusal` -- the security verdict, the SRE verdict and the
    three gate decisions. A double that merged anything would make
    `test_promote_guard`-shaped tests vacuous.

So the "adapter" is in-memory in its EFFECTS, not in its POLICY. Policy is shared
with the shipped adapter deliberately: a double with its own copy of the rules is
a double that can disagree with production and stay green.

`failures` is the opt-in for the other direction: name a method and it raises, so
a test can drive the never-raises contract without patching anything. That is what
makes `tests/test_integration_conformance.py`'s never-raises assertions
non-vacuous -- without it they would pass against an interface with no handlers at
all.
"""

from __future__ import annotations

from .. import fixtures_loader, github_ops
from ..state import DevResult, Finding, RunState
from .base import (
    CI_UNKNOWN,
    MERGE_REFUSED_PREFIX,
    SCHEME_DELIVERED_LOCAL,
    CodeHost,
    branch_for,
    undelivered_ref,
)


class MemoryHost(CodeHost):
    """A code host that records instead of writing. No network, no git, no disk.

    NOT SHIPPED. `integrations.host("memory")` refuses it, because a run served
    by this adapter would report a merged pull request that does not exist -- the
    exact fabricated success `deploy_note` and `merge_pr`'s `merged` check exist
    to prevent, one layer up.
    """

    name = "memory"
    shipped = False

    def __init__(self, *, ci: str = CI_UNKNOWN) -> None:
        """`ci` is what `ci_status` answers; the default is the fail-safe.

        `unknown` rather than `passing`, deliberately, and it is the same
        reasoning `github_ops.ci_status` gives: a double defaulting to `passing`
        would let every test that never mentions CI assert against a green build
        nothing measured. `RunState.ci_status_measured` is still honoured first,
        so a test that sets the field gets the field.
        """
        self.ci = ci
        # One entry per call, in order. `(destination, body, finding)` rather than
        # the body alone: the issue/PR split is what `_destination` decides, and a
        # record that dropped it could not tell a planner comment that reached the
        # issue from one that went to a PR lookup and vanished.
        self.comments: list[tuple[str, str, Finding | None]] = []
        self.pulls: list[DevResult] = []
        self.merges: list[str] = []
        self.outcomes: list[str] = []
        # Method name -> the exception to raise from it. The opt-in that lets a
        # test drive the never-raises contract.
        self.failures: dict[str, Exception] = {}

    def _maybe_fail(self, method: str) -> None:
        """Raise if this test asked this method to fail."""
        exc = self.failures.get(method)
        if exc is not None:
            raise exc

    def open_pr(self, state: RunState) -> DevResult:
        """Record a pull request. MAY RAISE, exactly as the interface allows.

        `dev.branch` is OVERWRITTEN with the branch this adapter created, which is
        what makes `_destination` answer `pull request` for every later comment.
        The developer agent fills `dev.branch` with a name of its own
        (`feat/login-rate-limit` in the fixture) and that branch has no PR, so a
        double leaving it alone would put every post-develop comment through the
        issue path and quietly pass tests about PR routing.
        """
        self._maybe_fail("open_pr")
        dev = state.dev or fixtures_loader.dev()
        # The real adapter's branch shape, through the ONE function that spells it,
        # so a test asserting on the branch name is asserting on the same thing
        # whichever adapter served the run. See `base.branch_for`.
        dev.branch = branch_for(state, dev)
        dev.pr_url = f"{SCHEME_DELIVERED_LOCAL}://{dev.branch}"
        self.pulls.append(dev)
        return dev

    def _post_comment(self, state: RunState, body: str,
                      finding: Finding | None = None) -> str:
        """Record one comment, and refuse the cases the real adapter refuses."""
        self._maybe_fail("post_comment")
        destination = github_ops._destination(state)
        # THE SAME REFUSAL, from the same predicate. Before any PR exists a
        # comment can only land on the issue, and a ticket id that is not an
        # issue number has no issue -- every `CLEAN-1`, `POISON-1`, `DEMO-1` run
        # this suite performs. Answering `local://` there would make the double
        # claim a delivery the real adapter reports as `comment://`.
        if destination == github_ops.ON_ISSUE and \
                github_ops._issue_number(state.ticket_id) is None:
            return undelivered_ref(state)
        self.comments.append((destination, body, finding))
        # `local://` because the bytes are in `self.comments` -- the double's
        # equivalent of reaching disk, and the point at which the real adapter
        # returns that scheme too.
        return f"{SCHEME_DELIVERED_LOCAL}://memory/{state.run_id}/{len(self.comments)}"

    def _merge_pr(self, state: RunState) -> str:
        """Merge, or say why not -- through the REAL refusal predicate."""
        self._maybe_fail("merge_pr")
        refusal = github_ops._merge_refusal(state)
        if refusal is not None:
            return f"{MERGE_REFUSED_PREFIX}{refusal}"
        branch = state.dev.branch if state.dev else ""
        self.merges.append(branch)
        return f"{SCHEME_DELIVERED_LOCAL}://merged/{branch}"

    def _report_outcome(self, state: RunState) -> str:
        """Record the run's ending. Always the ISSUE, never routed by _destination.

        `github_ops.report_outcome` bypasses `_destination` on purpose -- the
        issue is the surface that stays open and that a reader returns to, so an
        outcome posted only to the PR leaves the issue with no ending. A double
        that routed this through `_destination` would stamp it `pull request` and
        make the online and offline records disagree.
        """
        self._maybe_fail("report_outcome")
        self.outcomes.append(state.status)
        if github_ops._issue_number(state.ticket_id) is None:
            return undelivered_ref(state)
        return f"{SCHEME_DELIVERED_LOCAL}://memory/outcome/{state.run_id}"

    def _ci_status(self, state: RunState) -> str:
        """This adapter's answer. DOES NOT read `RunState.ci_status_measured`.

        THE CONFORMANCE SUITE CAUGHT THE OPPOSITE OF THIS, and the distinction is
        worth the paragraph. The first draft returned
        `state.ci_status_measured or self.ci`, which reads as obviously correct --
        and `test_a_measured_ci_status_is_carried_through_not_re_derived[github]`
        failed, because `github_ops.ci_status` does NOT read that field:

            assert 'unknown' == 'failing'   <- github, with the field set

        `ci_status` IS THE MEASUREMENT. The field is where a measurement TRAVELS,
        from the runner that holds a token to the container that does not, and the
        one reader is `sre.run` (`agents/sre.py:163`: `state.ci_status_measured or
        github_ops.ci_status(state)`). An adapter reading it too would mean the
        answer came from the field on one adapter and from a lookup on another,
        with nothing recording which -- and a double that honoured it would pass
        tests the shipped adapter fails.

        So this stays the adapter's own answer, and `unknown` by default: a double
        defaulting to `passing` would let every test that never mentions CI assert
        against a green build nothing measured.
        """
        self._maybe_fail("ci_status")
        return self.ci
