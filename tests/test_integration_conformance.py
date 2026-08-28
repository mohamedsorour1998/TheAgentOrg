"""D5 — THE CONFORMANCE SUITE. Every adapter passes the SAME tests.

OWNER: Lane D.

WHY THIS FILE IS THE LANE'S REAL DELIVERABLE. An interface extracted from one
implementation is that implementation with the names changed, and nothing in a
green suite says so. The only instrument that can tell the difference is a test
body that never mentions which adapter it is driving -- so every test here is
parametrized over `ADAPTERS_UNDER_TEST` and asserts on the CONTRACT, not on the
adapter.

    github   the shipped one, delegating to github_ops (conftest guard 2 in force)
    memory   the double: no network, no git, no disk
    git      plain git, no vendor at all -- NOT SHIPPED

`test_the_conformance_suite_covers_every_registered_adapter` is what keeps that
honest: it fails if `integrations.ADAPTERS` grows an entry this file does not
drive. Without it, a fourth adapter could ship untested and the suite would go on
reporting the same number of passes -- this repository's named pattern, arriving
as a parametrize list that silently stopped covering the thing it names.

=========================================================================
WHAT `github` IS AND IS NOT DOING IN HERE
=========================================================================

It runs against `github_ops`' LOCAL path, because conftest guard 2 forces
`config.OFFLINE = True` and puts a `pytest.fail` raiser on `github_ops._repo`. So
these tests exercise real local git and the real NOTES file, in `tmp_path` --
which is exactly the path the demo takes (`OFFLINE=true`) and the one every other
test in the suite runs. They do NOT exercise the PyGithub branch; that is
`tests/test_agent_comments.py`'s and `tests/test_merge_pr.py`'s job, with their
own fake repo objects, and duplicating it here would test their fakes twice
rather than testing this interface once.

Stated because the alternative reading -- "conformance proves the GitHub adapter
works against GitHub" -- is false and would be a worse claim than the true one.
"""

from __future__ import annotations

import pytest

from agentorg import github_ops, integrations
from agentorg.common import config
from agentorg.integrations import (
    CI_ANSWERS,
    DELIVERY_SCHEMES,
    MERGE_FAILED_PREFIX,
    GitHost,
    GitHubHost,
    MemoryHost,
    scheme_of,
)
from agentorg.state import (
    DevResult,
    Finding,
    HumanDecision,
    RunState,
    SecurityResult,
    SREResult,
)

# Every adapter, by name. The parametrize ids are the adapter names, so a
# conformance failure reads `[git]` rather than `[2]`.
ADAPTERS_UNDER_TEST = ("github", "memory", "git")


@pytest.fixture(params=ADAPTERS_UNDER_TEST)
def adapter(request, tmp_path, monkeypatch):
    """One adapter under test, with its workspace pointed at tmp_path.

    `OFFLINE_REPO` is redirected by conftest guard 3 already; this re-points it at
    a per-adapter subdirectory so the three cannot share one repository within a
    single parametrized test. Sharing would make a later adapter pass on a branch
    an earlier one committed -- order-dependent, and green.
    """
    workspace = tmp_path / f"host-{request.param}"
    monkeypatch.setattr(config, "OFFLINE_REPO", str(workspace))
    monkeypatch.setattr(config, "OFFLINE_NOTES", str(workspace / "NOTES.md"))
    return integrations.ADAPTERS[request.param]()


def _state(ticket_id: str = "7", **kwargs) -> RunState:
    """A run state with an issue-numbered ticket, which is the deliverable case.

    `7` rather than `CLEAN-1`, deliberately: `_issue_number` refuses anything that
    is not an issue number, so a default of `CLEAN-1` would send every test in
    this file down the undelivered path and the delivery assertions would pin
    nothing.
    """
    return RunState(ticket_id=ticket_id, ticket_text="add a rate limit", **kwargs)


def _dev(diff: str = "+++ b/app/auth.py\n@@\n+limit = 5\n") -> DevResult:
    return DevResult(branch="feat/rate-limit", diff=diff, summary="rate limit",
                     files_changed=["app/auth.py"])


def _promotable(state: RunState) -> RunState:
    """Fill in everything `_merge_refusal` demands, so a merge may proceed.

    Every field it reads: a `pass` security verdict, a `go` SRE verdict, and an
    approval for each of gate1/gate2/gate3. Built here rather than in each test so
    a change to the refusal rule fails in one place.
    """
    state.security = SecurityResult(verdict="pass", findings=[], blocking=[],
                                    explanation="clean")
    state.sre = SREResult(verdict="go", ci_status="unknown", slo_checks=[])
    state.decisions = [
        HumanDecision(gate=gate, decision="approved", by="tester")
        for gate in ("gate1", "gate2", "gate3")
    ]
    return state


# ── the parametrize list must not silently stop covering the adapters ──────────


def test_the_conformance_suite_covers_every_registered_adapter():
    """A fourth adapter must not be able to ship without conformance tests.

    THE GUARD AGAINST THIS FILE, not against the adapters. A parametrize list is a
    matcher, and this repository's rule is that any matcher which can match
    nothing must assert that it matched -- otherwise adding `gitlab` to `ADAPTERS`
    leaves the suite reporting exactly as many passes as before while the new
    adapter is untested.
    """
    assert integrations.ADAPTERS, "ADAPTERS is empty; this whole file would pin nothing"
    assert set(ADAPTERS_UNDER_TEST) == set(integrations.ADAPTERS), (
        "the conformance parametrize list and integrations.ADAPTERS disagree: "
        f"tested={sorted(ADAPTERS_UNDER_TEST)} registered={sorted(integrations.ADAPTERS)}. "
        "Every registered adapter must pass the same tests (plan D5)."
    )


# ── the ref vocabulary, which is the interface's real content ──────────────────


def test_every_adapter_returns_a_ref_whose_scheme_the_timeline_understands(adapter):
    """A delivered comment's scheme must be one `timeline._DELIVERY` recognises.

    THE SCHEME IS THE CONTRACT, not the surface. `timeline._delivery` splits the
    ref on `://` and looks the scheme up; an unrecognised one renders
    `UNRECOGNISED` on the artifact a judge reads. So an adapter inventing
    `gitlab://` for a delivered comment would be correct in its own terms and
    would degrade the timeline for every run it touched.
    """
    state = _state()
    ref = adapter.post_comment(state, "### Agent Org · plan\n\nbody")
    assert scheme_of(ref) in DELIVERY_SCHEMES, (
        f"{adapter.name} returned ref {ref!r}, whose scheme is not one of "
        f"{sorted(DELIVERY_SCHEMES)} -- the timeline would render it UNRECOGNISED"
    )


def test_the_offered_schemes_are_exactly_the_ones_the_timeline_renders():
    """DELIVERY_SCHEMES and timeline._DELIVERY must not drift apart.

    A SECOND DECLARATION, ON PURPOSE, and the same deliberate exception
    `test_scoring_determinism.py` makes for the severity ranking: a copy is the
    only instrument that can detect a change in the original.

    BOTH DIRECTIONS FAIL. A scheme added to the renderer but not offered to
    adapters is just as broken as the reverse -- the first means the renderer
    handles a case nothing produces, the second means an adapter can produce a ref
    the renderer calls UNRECOGNISED.
    """
    from agentorg import timeline

    assert set(timeline._DELIVERY), "timeline._DELIVERY is empty; this would pin nothing"
    assert DELIVERY_SCHEMES == set(timeline._DELIVERY), (
        "the interface offers schemes the timeline does not render, or vice versa: "
        f"interface={sorted(DELIVERY_SCHEMES)} "
        f"timeline={sorted(timeline._DELIVERY)}"
    )


def test_a_comment_before_any_pull_request_still_gets_a_ref(adapter):
    """The planner's comment runs before `open_pr`. It must still return a ref.

    `graph.py:499` posts the plan comment with `state.dev` still None, and
    `graph.py` records whatever comes back. A `None` here would be a TypeError
    inside `timeline._delivery`'s `split`, on the artifact rather than at the fault.
    """
    ref = adapter.post_comment(_state(), "plan")
    assert isinstance(ref, str) and ref, (
        f"{adapter.name} returned {ref!r} for a pre-PR comment; post_comment is "
        f"annotated -> str and the timeline splits the value on '://'"
    )


# ── D3: the never-raises contract, driven by an adapter that DOES raise ────────


@pytest.mark.parametrize("method", ["post_comment", "merge_pr", "report_outcome",
                                    "ci_status"])
def test_the_four_wrapped_methods_absorb_an_adapter_that_raises(method):
    """An adapter body that raises must not reach the caller. See base.CodeHost.

    DRIVEN BY A REAL RAISE, which is what makes this non-vacuous. Asserting
    "post_comment did not raise" against an adapter that never raises passes
    against an interface with no handlers at all -- this repository's named
    pattern: a double that cannot express the failing case.

    `MemoryHost.failures` exists for exactly this. Only the double gets it: making
    `github_ops` raise would mean patching its internals, and making plain git
    raise would mean breaking a real repository, so the honest test of the
    interface's handler is against the adapter whose body it can control.
    """
    host = MemoryHost()
    host.failures[method] = RuntimeError("the host went away mid-call")
    state = _promotable(_state())
    state.dev = _dev()

    answer = getattr(host, method)(state, "body") if method == "post_comment" \
        else getattr(host, method)(state)

    assert isinstance(answer, str) and answer, (
        f"{method} absorbed the exception but answered {answer!r}; every wrapped "
        f"method must return its fail-safe VALUE, not None or ''"
    )


def test_an_absorbed_failure_answers_the_undelivered_ref_not_a_delivery():
    """A comment that raised must not come back looking delivered.

    THE DIRECTION IS THE WHOLE POINT. Returning `local://` after a failure would
    be the artifact claiming a delivery that did not happen -- and the timeline
    would render it `reported`, so the run's log row would say a human was told
    something nobody was told.
    """
    host = MemoryHost()
    host.failures["post_comment"] = RuntimeError("boom")
    state = _state()

    ref = host.post_comment(state, "the block explanation")

    assert ref == f"comment://{state.run_id}", (
        f"an absorbed post_comment answered {ref!r}; it must be "
        f"comment://<run_id>, the ref that says the reason reached nobody"
    )
    assert scheme_of(ref) == "comment", "the timeline classifies by scheme"


def test_an_absorbed_merge_names_the_exception_type_in_its_ref():
    """`merge://failed/<Type>` — GITHUB declined, not WE declined.

    The two prefixes are kept apart because they call for different actions from
    whoever reads the timeline: `merge://refused/` is a policy this pipeline
    enforced, `merge://failed/` is something somebody has to go and fix. Collapsing
    them would make an unreachable host look like a policy refusal.
    """
    host = MemoryHost()
    host.failures["merge_pr"] = TimeoutError("read timed out")
    state = _promotable(_state())
    state.dev = _dev()

    ref = host.merge_pr(state)

    assert ref == f"{MERGE_FAILED_PREFIX}TimeoutError", (
        f"got {ref!r}; an absorbed merge must name the exception type so the log "
        f"row can say what went wrong without the traceback"
    )


def test_an_absorbed_ci_lookup_answers_unknown_and_never_passing():
    """An unreachable host is `unknown`. `passing` would be fail-open.

    THE FAIL-SAFE DIRECTION, and it is the one assertion in this file that is
    about safety rather than about shape: a commit nothing examined is not a green
    commit, and an outage that read as a green build is the fail-open shape the
    security lane exists to prevent, one seam over.
    """
    host = MemoryHost(ci="passing")
    host.failures["ci_status"] = ConnectionError("no route to host")

    answer = host.ci_status(_state())

    assert answer == "unknown", (
        f"a failed CI lookup answered {answer!r}. The adapter was configured to "
        f"say 'passing', so this proves the value came from the interface's "
        f"fail-safe rather than from the adapter"
    )


def test_every_adapter_answers_ci_from_the_three_word_vocabulary(adapter):
    """`passing`, `failing`, `unknown` — nothing else, ever."""
    answer = adapter.ci_status(_state())
    assert answer in CI_ANSWERS, (
        f"{adapter.name} answered {answer!r}; the vocabulary is "
        f"{sorted(CI_ANSWERS)} and `sre.py` derives its verdict from it"
    )


def test_ci_status_is_the_MEASUREMENT_and_never_reads_the_carried_field(adapter):
    """No adapter reads `RunState.ci_status_measured`. `sre.run` is its one reader.

    THIS TEST IS THE CORRECTED FORM OF ONE THAT FAILED, and the failure was the
    conformance suite doing its job. The first version asserted the opposite --
    that a populated `ci_status_measured` is "carried through, not re-derived" --
    which is true of `sre.run` and FALSE of `ci_status`. Two of the three adapters
    had been written to honour the field, and `[github]` failed by name:

        assert 'unknown' == 'failing'

    The layering, read off the code rather than assumed. `graph.py:644` and
    `run_stage.py:705` MEASURE on the runner (which holds a token) and store the
    answer on the field; `agents/sre.py:163` READS it -- `state.ci_status_measured
    or github_ops.ci_status(state)` -- because under `REMOTE_AGENTS=true` the agent
    body runs in a container with no token. So the field is the WIRE, and
    `ci_status` is what is put on it.

    An adapter reading the field too would mean the answer came from the field on
    one adapter and from a lookup on another, with nothing recording which -- and
    the double would pass a test the shipped adapter fails, which is the whole
    class of defect a conformance suite exists to catch.
    """
    state = _state()
    state.ci_status_measured = "failing"
    answer = adapter.ci_status(state)
    assert answer != "failing", (
        f"{adapter.name}.ci_status returned the value sitting on "
        f"ci_status_measured. That field is where a measurement TRAVELS from the "
        f"runner to the container; ci_status is what produces one. Its only "
        f"reader is agents/sre.py:163."
    )
    assert answer in CI_ANSWERS, f"{adapter.name} answered {answer!r}"


def test_the_one_reader_of_the_carried_field_still_reads_it():
    """`sre.run` prefers the field, and that must not have been broken here.

    THE OTHER HALF OF THE TEST ABOVE, and without it that one is a licence to
    ignore the field everywhere. Asserted over `agents/sre.py`'s AST rather than
    its text, because a comment naming `ci_status_measured` would satisfy a
    substring check while the read was gone -- this repository's most repeatable
    failure in a codebase that is 40% commentary.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "agentorg" / "agents" / "sre.py"
    tree = ast.parse(source.read_text())
    reads = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "ci_status_measured"
    ]
    assert reads, (
        "agents/sre.py no longer reads state.ci_status_measured over the AST. "
        "Under REMOTE_AGENTS=true the SRE agent runs in a container with no "
        "GitHub token, so without that read it answers `unknown` for a repository "
        "whose checks are green -- measured on the clean demo run, 49 seconds after "
        "both checks succeeded."
    )


# ── open_pr is the ONE method that may raise, and it must ──────────────────────


def test_open_pr_fills_the_branch_and_pr_url_on_every_adapter(adapter):
    """`_destination` reads `dev.branch`, so open_pr must overwrite it.

    THE OVERWRITE IS LOAD-BEARING. The developer agent fills `dev.branch` with a
    name of its own -- the fixture's is `feat/login-rate-limit` -- and that branch
    has no PR. An adapter leaving it alone would route every post-develop comment
    through a PR lookup for a branch nothing created.
    """
    state = _state()
    state.dev = _dev()
    before = state.dev.branch

    dev = adapter.open_pr(state)

    assert dev.branch and dev.branch != before, (
        f"{adapter.name} left dev.branch as {dev.branch!r}; open_pr must replace "
        f"the agent's branch name with the one it actually created"
    )
    assert dev.pr_url, f"{adapter.name} returned no pr_url; report_outcome renders it"


def test_open_pr_is_the_one_method_the_interface_lets_raise():
    """A raising open_pr must reach the caller. Absorbing it is a defect.

    WHY THIS ONE IS DIFFERENT, and it is not an oversight in the interface:
    `github_ops._ensure_offline_repo` REFUSES a git repository offline mode did
    not create, and that refusal exists because the `isdir` version of it was
    measured rewriting a victim's `user.email` and committing onto their
    checked-out branch. A wrapper that turned that into a placeholder DevResult
    would proceed as though a branch existed.
    """
    host = MemoryHost()
    host.failures["open_pr"] = RuntimeError(
        "OFFLINE_REPO is a git repository offline mode did not create"
    )
    with pytest.raises(RuntimeError, match="did not create"):
        host.open_pr(_state())


def test_a_comment_after_open_pr_routes_to_the_pull_request(adapter):
    """Before open_pr: the issue. After: the pull request. Derived, never passed.

    Asserted through `github_ops._destination` on the SAME state the adapter just
    wrote, because that is the predicate every adapter shares -- a second copy of
    the routing rule in a test would be a second answer with nothing recording
    which one an adapter used.
    """
    state = _state()
    assert github_ops._destination(state) == github_ops.ON_ISSUE, (
        "with state.dev unset, a comment can only reach the issue"
    )
    state.dev = _dev()
    adapter.open_pr(state)
    assert github_ops._destination(state) == github_ops.ON_PULL_REQUEST, (
        f"after {adapter.name}.open_pr the state must route comments to the PR"
    )


# ── the refusals every adapter shares, from the same predicate ─────────────────


def test_no_adapter_merges_a_run_that_has_not_earned_it(adapter):
    """A run with no security verdict must not merge, on any adapter.

    A MISSING RESULT IS A REFUSAL, NOT A PASS: `state.security is None` means
    nothing evaluated the block rule, and that must not read the same as
    `verdict == "pass"`. This is the last write in the pipeline and the only
    irreversible one.
    """
    state = _state()
    state.dev = _dev()
    ref = adapter.merge_pr(state)
    assert ref.startswith("merge://refused/"), (
        f"{adapter.name} answered {ref!r} for a run with no verdicts and no gate "
        f"decisions; every adapter must refuse it"
    )


def test_a_blocked_run_is_refused_by_every_adapter(adapter):
    """The poisoned run's ending. `block` must never reach a merge."""
    state = _promotable(_state())
    state.dev = _dev()
    state.security = SecurityResult(
        verdict="block",
        findings=[Finding(tool="gitleaks", severity="critical",
                          rule="aws-access-key-id", file="app/auth.py", line=3,
                          description="key")],
        blocking=[Finding(tool="gitleaks", severity="critical",
                          rule="aws-access-key-id", file="app/auth.py", line=3,
                          description="key")],
        explanation="a committed credential",
    )
    ref = adapter.merge_pr(state)
    assert ref == "merge://refused/security-verdict-block", (
        f"{adapter.name} answered {ref!r} for a BLOCKED run. This is the claim the "
        f"whole project rests on and it must hold on every adapter"
    )


def test_a_ticket_that_is_not_an_issue_number_is_not_guessed_at(adapter):
    """`CLEAN-1` must not become issue #1 on somebody's repository.

    `_ISSUE_REF` is anchored and uses `[0-9]` rather than `\\d` -- both deliberate,
    both recorded in `github_ops`. THE NEXT THING AN ADAPTER DOES WITH THE ANSWER
    IS WRITE, so a lenient parse is a comment on a real issue nobody named. Every
    ticket id this repo uses -- `POISON-1`, `CLEAN-1`, `DEMO-1` -- would become #1.
    """
    assert github_ops._issue_number("CLEAN-1") is None, (
        "the shared predicate itself is wrong; this test would pin nothing"
    )
    ref = adapter.post_comment(_state("CLEAN-1"), "plan")
    assert isinstance(ref, str) and ref, (
        f"{adapter.name} must still return a ref for a non-issue ticket"
    )
    assert scheme_of(ref) in DELIVERY_SCHEMES


# ── the adapters' own identity, which host() reads ────────────────────────────


def test_every_adapter_puts_the_change_on_the_SAME_branch(adapter):
    """One branch shape across all three adapters, from `base.branch_for`.

    THIS IS THE SECOND DECLARATION `base.branch_for` WARNS ABOUT, and the test is
    the instrument that keeps the two copies honest. `github_ops.open_pr` builds
    `agent-org/<ticket>-<_short_sha(diff)>` itself; `base.branch_for` restates that
    shape for the adapters that are not delegating to it. Two copies of a rule keep
    agreeing while one moves, so the agreement is asserted rather than assumed.

    Compared against `github_ops._short_sha`, the function the shipped path
    actually calls, so this cannot pass by both sides using the same wrong hash.
    """
    state = _state()
    state.dev = _dev()
    expected = f"agent-org/7-{github_ops._short_sha(state.dev.diff)}"

    dev = adapter.open_pr(state)

    assert dev.branch == expected, (
        f"{adapter.name} put the change on {dev.branch!r}, not {expected!r}. Every "
        f"adapter must agree on the branch shape: `_destination` routes comments by "
        f"`dev.branch`, and `merge_pr` looks a pull request up by it."
    )


def test_only_the_github_adapter_is_shipped():
    """`shipped` decides what may serve a real run, and only one may.

    Named adapters rather than a loop over `ADAPTERS`, because a loop would derive
    its expectation from the thing under test -- the eleventh instance of this
    repo's pattern, where a property test reads the table it is checking.
    """
    assert GitHubHost.shipped is True, "the shipped adapter must be shippable"
    assert MemoryHost.shipped is False, (
        "a double that could serve a run would report a merged PR that does not exist"
    )
    assert GitHost.shipped is False, (
        "the second adapter merges into main with no protected branch and no "
        "review; passing the conformance suite is not permission to serve a run"
    )
