"""Merging is the last write, and the one that cannot be taken back.

`promote` runs only past gate3 -- three human approvals -- so there is nothing
left to decide. But an irreversible write still needs preconditions that are
CHECKED rather than assumed, and a refusal that is RECORDED rather than silent.

WHY THE PRECONDITIONS ARE RE-CHECKED HERE. In the workflow they are already true:
`promote` needs gate3, gate2 needs develop, and a blocked run exits 3 from
develop so gate2 never starts. That is CONTROL FLOW, not a guard -- and
`graph.py` and `scripts/run_stage.py` are two callers with two orderings. A
function that merges somebody else's pull request must not depend on a caller's
job graph for the one thing it must never do.

WHAT THIS FILE DOES NOT COVER: the `merged` log row and its ordering before
`promoted`. Both promote sites belong to another lane, which calls this function
and writes that row; its tests pin the ordering. Everything here stops at
`merge_pr` itself.
"""

from agentorg import github_ops
from agentorg.state import (
    DevResult,
    HumanDecision,
    RunState,
    SecurityResult,
    SREResult,
)


class _FakePR:
    def __init__(self, mergeable=True):
        self.html_url = "https://github.com/o/r/pull/7"
        self.number = 7
        self.mergeable = mergeable
        self.merged = False
        self.merge_calls: list[dict] = []

    def merge(self, **kwargs):
        self.merge_calls.append(kwargs)
        self.merged = True
        return type("R", (), {"merged": True, "sha": "cafe1234"})()


class _FakePaginated(list):
    """PyGithub's PaginatedList, in the two ways merge_pr uses one.

    `totalCount` is PyGithub's own spelling. A plain list would answer `len()`
    and NOT this, so a double without it could not express the real object.
    """

    @property
    def totalCount(self):
        return len(self)


class _FakeRepo:
    def __init__(self, pr=None):
        self._pr = pr
        self.owner = type("O", (), {"login": "o"})()
        self.queries: list[dict] = []

    def get_pulls(self, state=None, head=None):
        self.queries.append({"state": state, "head": head})
        return _FakePaginated([self._pr] if self._pr else [])


def _online(monkeypatch, pr):
    """Put the test on the online path. Returns (repo, pr)."""
    repo = _FakeRepo(pr)
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: repo)
    return repo


def _promotable() -> RunState:
    """A state in exactly the shape promote sees: past gate3, everything clear."""
    state = RunState(ticket_id="7", ticket_text="Add a per-IP login rate limit.")
    state.dev = DevResult(branch="agent-org/7-abc1234", diff="", summary="s",
                          files_changed=["app/auth.py"],
                          pr_url="https://github.com/o/r/pull/7")
    state.security = SecurityResult(verdict="pass", scan_provenance="scanners")
    state.sre = SREResult(verdict="go", ci_status="passing")
    state.decisions = [
        HumanDecision(gate=g, decision="approved", by="reviewer")
        for g in ("gate1", "gate2", "gate3")
    ]
    return state


# --------------------------------------------------------------------------
# The happy path -- without which every refusal below could be unconditional
# --------------------------------------------------------------------------

def test_a_promotable_run_is_merged(monkeypatch):
    """THE anti-vacuity test for this whole file.

    A merge_pr that refused everything would satisfy every other assertion here.
    """
    pr = _FakePR()
    _online(monkeypatch, pr)
    ref = github_ops.merge_pr(_promotable())
    assert pr.merged, "the pull request was not merged"
    assert ref.startswith("https://"), f"ref {ref!r} does not name a delivered merge"


def test_an_overridden_gate_still_permits_the_merge(monkeypatch):
    """`overridden` is a human decision, documented and deliberate.

    `gates_cli resume --decision overridden` is the one capability a human is
    meant to keep, so treating it as "not approved" would silently revoke it --
    and the run would refuse to merge with three human decisions on file.
    """
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    state.decisions[1] = HumanDecision(gate="gate2", decision="overridden",
                                       by="reviewer", reason="known false positive")
    ref = github_ops.merge_pr(state)
    assert pr.merged, (
        f"a run whose gate2 was OVERRIDDEN by a human was refused: {ref!r}. "
        f"Override is a documented human capability, not a missing approval."
    )


# --------------------------------------------------------------------------
# What the run itself forbids
# --------------------------------------------------------------------------

def test_a_blocked_run_is_never_merged(monkeypatch):
    """Defence in depth.

    `promote` is unreachable on a blocked run because gate2 needs develop -- but a
    function that performs an irreversible write must not rely on a caller's
    control flow for the one thing it must never do.
    """
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    state.security = SecurityResult(verdict="block", scan_provenance="scanners")

    ref = github_ops.merge_pr(state)
    assert not pr.merged, (
        "A BLOCKED RUN WAS MERGED. The security verdict was `block` and the "
        "merge proceeded anyway."
    )
    assert "refused" in ref, f"the refusal was not recorded in the ref: {ref!r}"


def test_a_run_with_no_security_result_is_never_merged(monkeypatch):
    """No verdict is not a pass. A stage that did not run cleared nothing.

    This is the "did not run" versus "passed" distinction the whole project
    exists to prevent collapsing, at the last write.
    """
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    state.security = None
    ref = github_ops.merge_pr(state)
    assert not pr.merged, "a run whose security stage never ran was merged"
    assert "refused" in ref


def test_an_sre_no_go_blocks_the_merge(monkeypatch):
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    state.sre = SREResult(verdict="no_go", ci_status="failing")
    ref = github_ops.merge_pr(state)
    assert not pr.merged, "a run the SRE agent refused was merged"
    assert "refused" in ref


def test_a_run_with_no_sre_result_is_never_merged(monkeypatch):
    """Same reasoning as the security one: absent is not approved."""
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    state.sre = None
    ref = github_ops.merge_pr(state)
    assert not pr.merged, "a run whose SRE stage never ran was merged"
    assert "refused" in ref


def test_a_run_missing_a_gate_approval_is_never_merged(monkeypatch):
    """Three approvals, checked here as well as enforced by the job graph."""
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    state.decisions = state.decisions[:2]   # gate3 never approved

    ref = github_ops.merge_pr(state)
    assert not pr.merged, "a run with only two approvals was merged"
    assert "refused" in ref


def test_a_rejected_decision_blocks_the_merge_even_with_three_rows(monkeypatch):
    """A rejection among the approvals is not an approval.

    COUNTING rows rather than READING them would let a rejected gate satisfy the
    check -- and `gates.resume` never un-sets a rejection, so a run genuinely can
    carry three decision rows one of which is a refusal. That is not a
    hypothetical shape; it is documented behaviour of the gates module.
    """
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    state.decisions[1] = HumanDecision(gate="gate2", decision="rejected", by="r")

    ref = github_ops.merge_pr(state)
    assert not pr.merged, "a run carrying a REJECTED gate decision was merged"
    assert "refused" in ref


def test_a_gate_both_rejected_and_later_approved_still_refuses(monkeypatch):
    """The exact shape `gates.resume` produces, with FOUR rows.

    Approving a run the graph already rejected appends the approval and never
    un-sets the rejection, so all three gates appear approved AND a refusal is on
    file. A check that asked only "is every gate approved?" would pass this.
    """
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    state.decisions.insert(
        1, HumanDecision(gate="gate2", decision="rejected", by="r",
                         reason="changed their mind later"))
    assert len(state.decisions) == 4, "this test must present four rows"

    ref = github_ops.merge_pr(state)
    assert not pr.merged, (
        "a run carrying BOTH a rejection and a later approval for gate2 was "
        "merged. gates.resume never un-sets a rejection, so this state is "
        "reachable, and a refusal on file outranks a later approval."
    )
    assert "refused" in ref


def test_the_refusal_reason_names_what_stopped_it(monkeypatch):
    """A ref that says only "refused" sends the reader to the logs.

    Each refusal's ref must distinguish itself: the reason travels into a log row
    and onto the timeline, and "we declined" with no cause is the silence this
    project keeps replacing.
    """
    scenarios = {}

    blocked = _promotable()
    blocked.security = SecurityResult(verdict="block", scan_provenance="scanners")
    scenarios["security"] = blocked

    no_go = _promotable()
    no_go.sre = SREResult(verdict="no_go", ci_status="failing")
    scenarios["sre"] = no_go

    rejected = _promotable()
    rejected.decisions[1] = HumanDecision(gate="gate2", decision="rejected", by="r")
    scenarios["gate"] = rejected

    refs = {}
    for label, state in scenarios.items():
        pr = _FakePR()
        _online(monkeypatch, pr)
        refs[label] = github_ops.merge_pr(state)
        assert not pr.merged, f"the {label} scenario merged"

    assert len(set(refs.values())) == len(refs), (
        f"two different refusals produced the same ref: {refs}. The reason is "
        f"what a reader needs; an undifferentiated 'refused' is silence with a "
        f"label."
    )
    for label, ref in refs.items():
        assert label in ref, (
            f"the {label} refusal's ref is {ref!r} and does not name its cause"
        )


# --------------------------------------------------------------------------
# What GitHub forbids, and the difference between the two
# --------------------------------------------------------------------------

def test_we_declined_and_github_declined_are_different_refs(monkeypatch):
    """`refused` is ours, `failed` is GitHub's, and a reader needs to know which.

    "The run was not allowed to merge" and "the merge was attempted and did not
    land" call for different actions from whoever is reading. Collapsing them
    would make a conflicted PR look like a policy refusal.
    """
    ours = _promotable()
    ours.security = SecurityResult(verdict="block", scan_provenance="scanners")
    _online(monkeypatch, _FakePR())
    our_ref = github_ops.merge_pr(ours)

    _online(monkeypatch, _FakePR(mergeable=False))
    their_ref = github_ops.merge_pr(_promotable())

    assert "refused" in our_ref and "failed" not in our_ref, (
        f"our own refusal reads {our_ref!r}"
    )
    assert "failed" in their_ref and "refused" not in their_ref, (
        f"GitHub's refusal reads {their_ref!r}; it must be distinguishable from "
        f"a precondition we enforced ourselves"
    )


def test_an_unmergeable_pr_is_refused_not_forced(monkeypatch):
    """A conflicting PR is a fact to report, not an obstacle to route around."""
    pr = _FakePR(mergeable=False)
    _online(monkeypatch, pr)
    ref = github_ops.merge_pr(_promotable())
    assert not pr.merged, "an unmergeable pull request was merged"
    assert "merge://failed/" in ref, f"unexpected ref {ref!r}"


def test_a_missing_pr_is_reported_not_merged(monkeypatch):
    _online(monkeypatch, None)
    ref = github_ops.merge_pr(_promotable())
    assert "merge://failed/" in ref, f"unexpected ref {ref!r}"


def test_github_answering_not_merged_is_not_reported_as_success(monkeypatch):
    """A 200 that did not merge is the reassuring non-answer.

    PyGithub's merge() returns a result object carrying `merged`. Trusting the
    absence of an exception would report a merge that did not happen -- this
    project's signature failure shape.
    """
    class _DeclinedPR(_FakePR):
        def merge(self, **kwargs):
            self.merge_calls.append(kwargs)
            return type("R", (), {"merged": False})()

    pr = _DeclinedPR()
    _online(monkeypatch, pr)
    ref = github_ops.merge_pr(_promotable())
    assert pr.merge_calls, "merge was never attempted; this test pins nothing"
    assert "merge://failed/" in ref, (
        f"GitHub answered merged=False and merge_pr returned {ref!r}, which "
        f"reads as a delivered merge"
    )


def test_a_github_failure_is_recorded_and_does_not_raise(monkeypatch):
    """`promote` must finish. A failed merge is a recorded fact, not a crash.

    Same requirement as post_comment: this is called immediately before the run's
    ending is written, and an exception here would lose that ending.
    """
    def _boom():
        raise RuntimeError("api down")

    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", _boom)
    ref = github_ops.merge_pr(_promotable())
    assert "merge://failed/" in ref, f"unexpected ref {ref!r}"
    assert "RuntimeError" in ref, (
        f"the ref {ref!r} does not name the failure type, so the log row cannot "
        f"say what went wrong"
    )


def test_merge_pr_never_raises_for_any_scenario_in_this_file(monkeypatch):
    """The requirement stated once, over every shape, rather than per test.

    A single `raise` reintroduced anywhere in this function loses the run's
    ending, and the ending is what the timeline reads its banner from.
    """
    def _boom():
        raise RuntimeError("api down")

    blocked = _promotable()
    blocked.security = SecurityResult(verdict="block", scan_provenance="scanners")
    no_dev = _promotable()
    no_dev.dev = None

    cases = {
        "clean": (lambda: _FakeRepo(_FakePR()), _promotable()),
        "blocked": (lambda: _FakeRepo(_FakePR()), blocked),
        "unmergeable": (lambda: _FakeRepo(_FakePR(mergeable=False)), _promotable()),
        "no pr": (lambda: _FakeRepo(None), _promotable()),
        "api down": (_boom, _promotable()),
        "no dev result": (lambda: _FakeRepo(_FakePR()), no_dev),
    }
    for label, (repo_factory, state) in cases.items():
        monkeypatch.setattr(github_ops, "_use_local", lambda: False)
        monkeypatch.setattr(github_ops, "_repo", repo_factory)
        try:
            ref = github_ops.merge_pr(state)
        except Exception as exc:
            raise AssertionError(
                f"merge_pr raised {type(exc).__name__} on the {label!r} case. It "
                f"is called immediately before the run's ending is written, so an "
                f"exception here loses that ending."
            ) from exc
        assert isinstance(ref, str) and ref, f"the {label!r} case returned {ref!r}"


# --------------------------------------------------------------------------
# The offline path -- the whole suite, and every local run
# --------------------------------------------------------------------------

def test_the_offline_path_does_not_reach_github(monkeypatch):
    """Must not raise, must not write, must not be called a refusal.

    This test deliberately does NOT stub `_repo`: conftest's raiser stays in
    place, so an offline path that reached the seam fails by name instead of
    silently going online.
    """
    monkeypatch.setattr(github_ops, "_use_local", lambda: True)
    ref = github_ops.merge_pr(_promotable())
    assert ref.startswith("local://"), f"offline merge returned {ref!r}"
    assert "refused" not in ref, (
        f"the offline ref {ref!r} reads as a refusal. Nothing about the RUN "
        f"prevented this merge -- there is simply no PR offline -- and calling it "
        f"a refusal would make every local run look like a policy failure."
    )


def test_the_offline_path_still_honours_the_preconditions(monkeypatch):
    """A blocked run offline is refused, not reported as locally delivered.

    Ordering: the refusal is checked BEFORE the offline branch. Otherwise the
    demo's own fallback path -- `OFFLINE=true`, the one it takes on stage --
    would report `local://` for a run that must never merge, and the blocked
    beat's final line would read as a delivery.
    """
    monkeypatch.setattr(github_ops, "_use_local", lambda: True)
    state = _promotable()
    state.security = SecurityResult(verdict="block", scan_provenance="scanners")
    ref = github_ops.merge_pr(state)
    assert "refused" in ref, (
        f"a BLOCKED run on the offline path returned {ref!r}. The preconditions "
        f"must be checked before the offline branch, or the demo's own fallback "
        f"reports a delivery for a run that must never merge."
    )


# --------------------------------------------------------------------------
# What it sends, and to which pull request
# --------------------------------------------------------------------------

def test_it_looks_up_the_runs_own_branch(monkeypatch):
    """The PR it merges must be THIS run's.

    A query built from an empty or wrong branch is not a query that finds
    nothing -- `head="owner:"` can come back with somebody else's pull request,
    and the next thing this function does is MERGE what came back.
    """
    repo = _online(monkeypatch, _FakePR())
    github_ops.merge_pr(_promotable())
    assert repo.queries == [{"state": "open", "head": "o:agent-org/7-abc1234"}], (
        f"merge_pr queried {repo.queries}; it must ask for this run's own branch, "
        f"open PRs only"
    )


def test_no_dev_branch_is_refused_without_asking_github(monkeypatch):
    """With no branch there is nothing to look up, and we must not ask."""
    repo = _online(monkeypatch, _FakePR())
    state = _promotable()
    state.dev = None
    ref = github_ops.merge_pr(state)
    assert repo.queries == [], (
        f"merge_pr queried GitHub with no dev result on the state: {repo.queries}"
    )
    assert isinstance(ref, str) and ref, f"returned {ref!r}"


def test_the_merge_commit_records_the_evidence(monkeypatch):
    """The merge commit is the durable record, and it outlives the run log.

    A judge reading `git log` on the target repo months later should find why
    this landed: which run, what the scanners said, and whether they really ran.
    """
    pr = _FakePR()
    _online(monkeypatch, pr)
    state = _promotable()
    github_ops.merge_pr(state)

    assert len(pr.merge_calls) == 1, f"merge called {len(pr.merge_calls)} times"
    sent = pr.merge_calls[0]
    body = f"{sent.get('commit_title', '')}\n{sent.get('commit_message', '')}"
    assert state.run_id in body, f"the run_id is not in the merge commit: {body!r}"
    assert "scanners" in body, (
        f"the scan provenance is not in the merge commit: {body!r}. 'pass' means "
        f"two different things depending on whether the scanners really ran."
    )
    assert "passing" in body, f"the CI status is not in the merge commit: {body!r}"
