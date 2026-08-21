"""Every agent's output reaches the target repo, one labelled comment per stage.

OWNER: Sorour.

WHY THIS FILE EXISTS. The PR is the timeline a judge reads. A stage that ran and
said nothing is indistinguishable, on that surface, from a stage that never ran
-- which is this project's signature defect, sitting in the one place the
audience is actually looking.

=========================================================================
WHY THE DISCRIMINATOR IS NOT A SUBSTRING SEARCH
=========================================================================

The obvious test is the one the plan sketched:

    assert any(stage in body.lower() for body in posted), f"{stage} posted nothing"

It passes for the wrong reason in at least three ways, and all three are live in
this pipeline rather than hypothetical:

  * "review" is a substring of "reviewer", and the word "reviewer" appears in
    the DEVELOP comment's summary and in `reviewer.NO_DETAIL_MUST_FIX`. So the
    review stage can post nothing at all and still be reported as present.
  * "security" appears inside prose written by other stages -- the planner's
    acceptance criteria mention credentials, the SRE agent's notes mention
    checks -- so a substring hit is not attribution.
  * ONE comment mentioning several stage names satisfies SEVERAL of those
    assertions at once. Five stages can go silent behind a single posted string.

So every comment carries a label line -- `### Agent Org · <stage>` -- and this
file parses that line and compares the stage by EQUALITY. Three consequences,
each one of them a mutation this file catches that the sketch does not:

  * bodies that are all identical bucket into ONE stage, so the other seven
    count zero;
  * "reviewer" cannot satisfy "review";
  * counts are asserted as an exact dict, so an extra comment fails as loudly
    as a missing one.

`_HEADER` below deliberately RESTATES graph.COMMENT_HEADER as a literal instead
of importing it. Importing it would make a change to the label format invisible
here -- both sides would move together and the tests would keep passing while
pinning a format nobody posts any more. That is rule 2, and this repo has paid
for it three times this week.

`_by_stage` keeps an `unlabelled` bucket and every test asserts it is empty, so
a matcher that stops matching fails instead of quietly measuring nothing.

=========================================================================
WHAT THE FOUR AUTOUSE GUARDS IN conftest.py MEAN FOR THIS FILE
=========================================================================

They force `config.OFFLINE = True`, so `run_pipeline` here takes github_ops'
LOCAL branch: comments are appended to `config.OFFLINE_NOTES` rather than
posted, and the model is off so every agent returns its fixture. Two things
follow, and both are stated because a test that pins nothing on the path it
actually runs is worse than no test.

  1. WHAT IS STILL MEANINGFUL OFFLINE: which stages posted, how many times
     each, in what order, whether the reviewer loop APPENDED, whether each
     comment carries its own stage's output, and -- because github_ops writes
     the intended destination into the NOTES header -- which target each
     comment was for. That last one is what makes the issue/PR split testable
     on the offline path at all.

  2. WHAT IS NOT: offline there is no issue and no PR, so both targets collapse
     onto one file. The claim that plan/gate1 reach the ISSUE and everything
     from develop onward reaches the PR is therefore pinned by
     `test_plan_and_gate1_land_on_the_issue_and_the_rest_on_the_pr`, which opts
     into the ONLINE path with all four lines conftest.py demands and gives the
     issue and the PR DIFFERENT numbers so a comment cannot be attributed to
     the wrong one.

Every test that drives the pipeline requests the `provenance` fixture and pins
`none_installed()`, following tests/test_timeline.py: without it the security
stage shells out to whatever scanners this machine happens to have, which makes
the run slower and its verdict machine-dependent for no gain -- nothing here is
a claim about scanners.
"""

import pathlib
import re
from types import SimpleNamespace

import pytest

from agentorg import github_ops
from agentorg.agents import developer, reviewer
from agentorg.common import config
from agentorg.graph import run_pipeline
from agentorg.state import DevResult, ReviewResult, RunState

TICKET_TEXT = "Add a per-IP login rate limit."

# RESTATED, not imported from agentorg.graph -- see the module docstring.
_HEADER = "### Agent Org · "

# Every stage that comments on a run that goes all the way through, and how many
# comments each one owes. An exact dict rather than a set of `any` checks: a
# stage that posted twice is as wrong as one that posted nothing, and only a
# count catches the first.
_PROMOTED_RUN_COMMENTS = {
    "plan": 1,
    "gate1": 1,
    "develop": 1,
    "review": 1,
    "security": 1,
    "gate2": 1,
    "sre": 1,
    "gate3": 1,
}

# The five AGENTS, which is what the plan's Task 4 is named for. Asserted
# separately from the dict above so the requirement stays visible even if the
# gate comments are ever dropped.
_AGENT_STAGES = ("plan", "develop", "review", "security", "sre")


def _stage_of(body: str) -> str:
    """The stage this comment is labelled with, or "" if it carries no label.

    Reads the FIRST LINE only and requires the exact prefix, so a stage name
    mentioned anywhere in the prose cannot be mistaken for the label.
    """
    first = body.splitlines()[0] if body else ""
    if not first.startswith(_HEADER):
        return ""
    return first[len(_HEADER):].strip()


def _by_stage(posted: list[str]) -> dict[str, list[str]]:
    """Group posted bodies by their label. Unlabelled bodies get their own key.

    The `unlabelled` bucket is the point: a body this parser cannot attribute
    must show up somewhere a test can assert on, rather than being dropped and
    leaving the caller to assert over an empty collection.
    """
    grouped: dict[str, list[str]] = {}
    for body in posted:
        grouped.setdefault(_stage_of(body) or "unlabelled", []).append(body)
    return grouped


def _capture(monkeypatch) -> list[str]:
    """Record every body graph.py posts, in order, and hand back a delivered ref.

    Patched as a module ATTRIBUTE on github_ops because that is how graph.py
    reaches it -- `github_ops.post_comment(...)`, resolved at call time. The
    signature matches the real one so a call shape graph.py cannot actually make
    would fail here rather than silently working.
    """
    posted: list[str] = []

    def _record(state, body, finding=None):
        posted.append(body)
        return f"local://captured/{len(posted)}"

    monkeypatch.setattr(github_ops, "post_comment", _record)
    return posted


_ATTEMPT = re.compile(r"attempt (\d+)")


def _attempts(bodies: list[str]) -> list[int]:
    """The revision number each of these comments carries, in posting order.

    Asserts every body actually carried one instead of returning a short list:
    a matcher that can match nothing must say so. Without this, a loop that
    replaced its comments would yield `[]` and any `==`/`<=` check on the result
    would be comparing two things that both mean "nothing was measured".
    """
    found = []
    for body in bodies:
        match = _ATTEMPT.search(body)
        assert match, f"comment carries no attempt number: {body[:160]!r}"
        found.append(int(match.group(1)))
    return found


def _attempts_or_none(bodies: list[str]) -> list[int | None]:
    """The attempt number on each body in order, or None where there is none.

    Positional counterpart to `_attempts`: that one asserts every body carried a
    number, which is right for a list already filtered to one stage. This one is
    applied to the WHOLE posted list, where plan/gate1/security legitimately
    carry none, so it must be able to say "none here" rather than fail.
    """
    found = []
    for body in bodies:
        match = _ATTEMPT.search(body)
        found.append(int(match.group(1)) if match else None)
    return found


# =========================================================================
# STEP 1's test, by the name the plan gives it. The RED step deletes the
# planner's post and this is the test that must go red naming `plan`.
# =========================================================================

def test_every_agent_stage_posts_its_output(monkeypatch, provenance):
    """The PR is the timeline. A stage that runs silently is invisible to a judge."""
    provenance.none_installed()
    posted = _capture(monkeypatch)

    state = run_pipeline("T-1", TICKET_TEXT)

    assert state.status == "promoted", "this test is about a run that finishes"
    grouped = _by_stage(posted)

    # Named before counted, so the failure says WHICH stage went silent rather
    # than printing a dict diff and leaving the reader to spot the gap.
    missing = [stage for stage in _PROMOTED_RUN_COMMENTS if stage not in grouped]
    assert not missing, f"these stages posted nothing: {missing}"

    assert "unlabelled" not in grouped, (
        "these comments carry no stage label, so nothing can attribute them: "
        f"{[b[:80] for b in grouped.get('unlabelled', [])]}"
    )
    counts = {stage: len(bodies) for stage, bodies in grouped.items()}
    assert counts == _PROMOTED_RUN_COMMENTS, counts

    # The five agents, restated -- see _AGENT_STAGES.
    assert all(stage in grouped for stage in _AGENT_STAGES)

    # A LABEL IS NOT OUTPUT. Every comment has to carry the thing its own stage
    # produced, or the whole set could be eight headers with nothing under them.
    # Each value below is read off the RunState the run returned, so none of
    # these strings is one this test made up -- `.upper()` included, since the
    # comments render verdicts in caps and the VALUE is still the state's.
    assert state.plan.tasks[0] in grouped["plan"][0]
    assert state.dev.summary in grouped["develop"][0]
    assert state.review.verdict in grouped["review"][0]
    assert state.security.explanation in grouped["security"][0]
    assert state.sre.verdict.upper() in grouped["sre"][0]
    assert state.decisions[0].decision.upper() in grouped["gate1"][0]

    # And they are eight DIFFERENT comments. Identical bodies would already fail
    # the count above; this says so directly, because "post the same string
    # eight times" is the cheapest way to satisfy a presence check.
    assert len(set(posted)) == len(posted)


def test_the_plan_comment_is_posted_before_the_develop_comment(monkeypatch,
                                                               provenance):
    """Order is part of the artifact: the timeline has to read as one.

    Separate from the test above because that one is about presence and this one
    is about sequence, and a shuffled set satisfies presence perfectly. The
    develop and review comments are QUEUED during the revision loop and flushed
    once `open_pr` exists to receive them, so this also pins that the flush
    happens after the plan/gate1 posts rather than before.
    """
    provenance.none_installed()
    posted = _capture(monkeypatch)

    run_pipeline("T-1", TICKET_TEXT)

    order = [_stage_of(body) for body in posted]
    assert "" not in order, f"an unlabelled comment breaks this ordering: {order}"
    assert order == ["plan", "gate1", "develop", "review",
                     "security", "gate2", "sre", "gate3"], order


# =========================================================================
# THE REVISION LOOP APPENDS. Three revisions is part of the story a judge
# reads, so a loop that overwrote its own comment would leave one.
# =========================================================================

def _never_approves(state: RunState) -> ReviewResult:
    """A reviewer that always asks for changes, so the cap is what stops the loop.

    Each verdict names its own pass, so the review comments are distinguishable
    by their CONTENT and not only by the attempt number graph.py stamps on them.
    """
    attempt = state.revision_count + 1
    return ReviewResult(
        verdict="changes_requested",
        must_fix=[f"pass {attempt}: the 429 branch is still missing"],
    )


# One diff per pass, each carrying a marker no other pass has. THIS IS WHAT
# CATCHES "APPEND IN SHAPE, REPLACE IN SUBSTANCE": the fixture developer returns
# a byte-identical diff on every pass, so with it, three comments rendering the
# LAST pass's diff are indistinguishable from three rendering their own. MEASURED
# -- a mutation that re-read `state.dev` at flush time instead of using the
# captured per-pass result survived the whole file until these markers existed.
_PASS_MARKER = "revision-marker-pass-"


def _developer_per_pass(state: RunState, poisoned: bool | None = None) -> DevResult:
    """A developer whose diff and summary say which pass produced them."""
    attempt = state.revision_count + 1
    return DevResult(
        branch="",
        diff=f"--- a/app/auth.py\n+++ b/app/auth.py\n+# {_PASS_MARKER}{attempt}\n",
        summary=f"attempt-summary-{attempt}: adds the limiter",
        files_changed=["app/auth.py"],
    )


def test_the_reviewer_loop_appends_a_comment_per_revision(monkeypatch, provenance):
    """Every revision survives, carrying ITS OWN output. A REPLACE leaves one.

    `reviewer.run` and `developer.run` are replaced rather than the model
    scripted, because the claim is about graph.py's loop and not about how a
    verdict or a diff is produced. agents/server.AGENTS holds the MODULE and
    `AGENTS[role].run` is resolved at call time, so patching the attribute reaches
    the call graph.py makes through common/agent_client.call_agent.

    The developer is replaced for a second, sharper reason: the fixture returns
    the SAME diff every pass, so a loop that appended the right NUMBER of comments
    while rendering the last pass's content into all of them would pass every
    count and every attempt-number check. See _PASS_MARKER.
    """
    provenance.none_installed()
    monkeypatch.setattr(reviewer, "run", _never_approves)
    monkeypatch.setattr(developer, "run", _developer_per_pass)
    posted = _capture(monkeypatch)

    state = run_pipeline("T-1", TICKET_TEXT)

    # The precondition. Without it every count below could be 1 because the
    # reviewer approved on the first pass and the loop never ran -- which is
    # exactly what a mis-patched seam produces, and it passes an `<=` check.
    assert state.revision_count == config.MAX_REVISION_LOOPS
    assert state.status == "failed", "the cap exit is the path under test"

    passes = config.MAX_REVISION_LOOPS + 1
    grouped = _by_stage(posted)
    assert "unlabelled" not in grouped

    assert len(grouped["develop"]) == passes, grouped["develop"]
    assert len(grouped["review"]) == passes, grouped["review"]

    # ASCENDING AND COMPLETE. A loop that kept only the last revision leaves
    # `[4]`; one that kept only the first leaves `[1]`; one that appended but
    # mislabelled leaves the wrong sequence. All three fail here.
    assert _attempts(grouped["develop"]) == list(range(1, passes + 1))
    assert _attempts(grouped["review"]) == list(range(1, passes + 1))

    # AND EACH COMMENT CARRIES ITS OWN PASS'S CONTENT, not the last one's. This
    # is the assertion the attempt numbers above cannot make: graph.py stamps
    # those, so they are ascending even when the body under them is wrong.
    for n, body in enumerate(grouped["develop"], start=1):
        assert f"{_PASS_MARKER}{n}\n" in body, f"develop comment {n} carries another pass's diff"
        assert f"attempt-summary-{n}:" in body, f"develop comment {n} carries another pass's summary"
        others = [m for m in range(1, passes + 1) if m != n]
        assert not [m for m in others if f"{_PASS_MARKER}{m}\n" in body], (
            f"develop comment {n} also carries another pass's diff"
        )
    for n, body in enumerate(grouped["review"], start=1):
        assert f"pass {n}: the 429 branch" in body, (
            f"review comment {n} carries another pass's must_fix"
        )

    assert len(set(grouped["review"])) == passes, "revisions must be distinguishable"
    assert len(set(grouped["develop"])) == passes, "revisions must be distinguishable"

    # CROSS-STAGE ORDER, which `grouped` cannot see. Grouping by stage discards
    # the sequence BETWEEN stages, so a flush that posted all four diffs and then
    # all four reviews satisfies every assertion above -- and renders attempt 1's
    # review three comments below attempt 4's diff, so the PR reads as four
    # unexplained diffs followed by four unattached verdicts. The shipped order
    # pairs them: develop 1, review 1, develop 2, review 2, ...
    #
    # This is the cross-stage twin of the "append in shape, replace in substance"
    # gap that mutation E exposed: the per-stage view was blind to it, and the
    # single-pass ordering test above cannot see it either, because with one pass
    # `[develop, review]` is the same list under both orders.
    order = [_stage_of(body) for body in posted]
    loop_order = [stage for stage in order if stage in ("develop", "review")]
    assert loop_order == ["develop", "review"] * passes, loop_order

    # And the pairing is by ATTEMPT, not merely alternating: an alternating flush
    # that emitted develop 1, review 2, develop 2, review 1 satisfies the line
    # above. Read the attempt number off each body in posting order instead.
    paired = [(_stage_of(b), n) for b, n in zip(posted, _attempts_or_none(posted), strict=True)
              if _stage_of(b) in ("develop", "review")]
    assert paired == [(stage, n) for n in range(1, passes + 1)
                      for stage in ("develop", "review")], paired

    # The stages a capped run never reaches must not have commented, or the
    # comment set is telling a judge about work that did not happen.
    assert "gate2" not in grouped
    assert "sre" not in grouped
    assert "gate3" not in grouped
    # ...and the ones before the loop still did.
    assert len(grouped["plan"]) == 1
    assert len(grouped["security"]) == 1


# =========================================================================
# A COMMENT THAT CANNOT BE DELIVERED MUST NOT TAKE THE RUN DOWN -- AND MUST
# NOT GO QUIET EITHER. post_comment cannot raise, so the only thing left to
# check is that graph.py still asked, and that the body still reached a human.
# =========================================================================

def test_an_undeliverable_comment_neither_stops_the_run_nor_goes_silent(
        tmp_path, monkeypatch, capsys, provenance):
    """Every stage still posts, the run still promotes, the bodies still surface.

    The failure is injected in the real github_ops rather than by patching
    post_comment out, so the delivery path itself is what fails: OFFLINE_NOTES
    is pointed at a DIRECTORY, which is the offline branch's measured failure
    (`IsADirectoryError`, tests/test_offline_mode.py). The witness is stdout,
    which is an independent channel from the one the other tests use -- if
    graph.py swallowed a failed post, or skipped a stage when the previous one
    failed, the labels would be missing from it.
    """
    provenance.none_installed()
    notes = tmp_path / "unwritable" / "NOTES.md"
    notes.mkdir(parents=True)
    monkeypatch.setattr(config, "OFFLINE_NOTES", str(notes))

    state = run_pipeline("T-1", TICKET_TEXT)

    assert state.status == "promoted", "an undelivered comment is not a verdict"

    out = capsys.readouterr().out
    labelled = re.findall(rf"{re.escape(_HEADER)}(\S+)", out)
    assert labelled, "no comment body reached stdout; the failure went silent"
    counts: dict[str, int] = {}
    for stage in labelled:
        counts[stage] = counts.get(stage, 0) + 1
    assert counts == _PROMOTED_RUN_COMMENTS, counts


# =========================================================================
# WHERE each comment goes. The offline half first, because that is the path
# the demo and this suite actually run on.
# =========================================================================

def _notes_targets() -> list[str]:
    """The intended destination of each comment, read out of the NOTES headers.

    Every `##` header github_ops writes ends in ` → <target>`. The marker is
    asserted per header rather than parsed with a lenient split: `rsplit` on a
    missing separator returns the whole line, so a header that stopped carrying
    a target would yield plausible-looking garbage instead of failing.
    """
    text = pathlib.Path(config.OFFLINE_NOTES).read_text()
    headers = [line for line in text.splitlines() if line.startswith("## ")]
    assert headers, f"no comment headers in {config.OFFLINE_NOTES}"
    targets = []
    for header in headers:
        _, marker, target = header.partition(" → ")
        assert marker, f"notes header names no target: {header!r}"
        targets.append(target)
    return targets


def test_the_offline_notes_record_which_target_each_comment_was_for(provenance):
    """Offline both targets collapse onto one file, so the file has to say which.

    Without this the issue/PR split would be unobservable on the only path this
    suite runs, and the online test below would be its sole witness.
    """
    provenance.none_installed()

    run_pipeline("T-1", TICKET_TEXT)

    assert _notes_targets() == ["issue", "issue"] + ["pull request"] * 6


# The real open_pr, captured at import BEFORE any test patches it. Read at call
# time this would wrap the previous test's wrapper. Same reasoning, and the same
# comment, as tests/test_offline_mode.py's `_REAL_OPEN_PR`.
_REAL_OPEN_PR = github_ops.open_pr

ISSUE_TICKET = "7"
ISSUE_NUMBER = 7
PR_NUMBER = 41


def _open_pr_on_local_git(state: RunState) -> DevResult:
    """Branch and commit on local git while the COMMENTS stay online.

    open_pr is pinned to the local path deliberately: this test is about where a
    comment lands, and a PR that also had to be faked would mean the fake repo
    was answering two different questions.
    """
    config.OFFLINE = True
    try:
        return _REAL_OPEN_PR(state)
    finally:
        config.OFFLINE = False


class _Pulls:
    def __init__(self, total: int):
        self.totalCount = total

    def __getitem__(self, index):
        return SimpleNamespace(number=PR_NUMBER)


class _Commentable:
    def __init__(self, repo, number: int):
        self._repo = repo
        self._number = number

    def create_comment(self, body: str):
        self._repo.comments.append((self._number, body))
        return SimpleNamespace(
            html_url=f"https://github.com/someone/auth-service/"
                     f"issues/{self._number}#issuecomment-{len(self._repo.comments)}"
        )


class _RecordingRepo:
    """Stand-in for the PyGithub repo handle that records WHICH number was written.

    The two targets are told apart by NUMBER, not by call order: the issue is 7
    (the ticket id) and the PR is 41, and only `get_pulls` can hand back 41. So
    a comment cannot be attributed to the wrong target by a bookkeeping mistake
    in this fake -- which matters, because attribution is the whole claim.
    """

    def __init__(self):
        self.comments: list[tuple[int, str]] = []
        self.heads: list[str] = []
        self.owner = SimpleNamespace(login="someone")

    def get_pulls(self, **kwargs):
        self.heads.append(kwargs.get("head"))
        return _Pulls(1)

    def get_issue(self, number):
        return _Commentable(self, number)


def _online(monkeypatch, repo) -> None:
    """Opt into the online path -- all four lines, per tests/conftest.py."""
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")
    monkeypatch.setattr(github_ops, "_repo", lambda: repo)


def test_plan_and_gate1_land_on_the_issue_and_the_rest_on_the_pr(monkeypatch,
                                                                 provenance):
    """The split the plan requires, pinned where it is actually observable.

    The PR does not exist until `open_pr`, so plan and gate1 have nowhere else
    to go than the issue -- and everything after it belongs on the PR, where the
    diff is. Offline these two are one file; here they are numbers 7 and 41.
    """
    provenance.none_installed()
    repo = _RecordingRepo()
    _online(monkeypatch, repo)
    monkeypatch.setattr(github_ops, "open_pr", _open_pr_on_local_git)

    state = run_pipeline(ISSUE_TICKET, TICKET_TEXT)

    assert state.status == "promoted"
    assert repo.comments, "nothing was posted at all"

    by_target: dict[int, list[str]] = {}
    for number, body in repo.comments:
        by_target.setdefault(number, []).append(_stage_of(body))

    assert set(by_target) == {ISSUE_NUMBER, PR_NUMBER}, sorted(by_target)
    assert by_target[ISSUE_NUMBER] == ["plan", "gate1"], by_target[ISSUE_NUMBER]
    assert by_target[PR_NUMBER] == ["develop", "review", "security",
                                    "gate2", "sre", "gate3"], by_target[PR_NUMBER]

    # The PR was found by the branch this run opened, not by an empty filter --
    # `head="someone:"` is a query GitHub can answer with somebody else's PR.
    assert repo.heads, "the PR-targeted comments never asked for a PR"
    assert set(repo.heads) == {f"someone:{state.dev.branch}"}, repo.heads
    assert len(repo.heads) == len(by_target[PR_NUMBER])


# =========================================================================
# The issue number is READ OFF THE TICKET ID, so the parse is the guard.
# =========================================================================

def _state(ticket_id: str) -> RunState:
    """A run that has not reached `open_pr`, so the issue is its only target."""
    return RunState(ticket_id=ticket_id, ticket_text=TICKET_TEXT)


# Ticket ids that are NOT issue references, and the reason each one is listed.
# The first four are the ids this repo actually uses. The last four are the ones
# that separate the shipped anchored pattern from a merely-`.match`ed one:
# MEASURED, `re.compile(r"#?([0-9]+)").match` yields issue 7, 7, 7 and 1 for
# them, so each is a comment written on a real issue nobody named. Those four are
# the coverage that a survivor mutation on `_ISSUE_REF`'s anchors slipped
# through, and the report that called that mutation benign was wrong.
_NOT_AN_ISSUE = [
    "POISON-1",     # the poisoned demo ticket
    "CLEAN-1",      # the clean demo ticket
    "T-1",          # this file's own ticket
    "DEMO-POISON",  # `python -m agentorg.graph --poisoned`
    "7-extra",      # anchors: trailing text after a real number
    "7 7",          # anchors: two numbers, neither one named
    "#7x",          # anchors: the GitHub form with a suffix
    "1-2",          # anchors: leading number of a range
]


@pytest.mark.parametrize("ticket_id", _NOT_AN_ISSUE)
def test_a_ticket_id_that_merely_contains_a_digit_is_not_an_issue_number(
        monkeypatch, capsys, ticket_id):
    """`POISON-1` must not become issue #1 on the target repo.

    This is the same failure `post_comment` already refuses for branches: a
    filter built from a value we did not really resolve is not a filter that
    selects nothing, and the next thing this function does is WRITE. A lenient
    parse -- the first digits in the string, or `int(re.sub(r"\\D", "", tid))` --
    turns every ticket this repo actually uses (`POISON-1`, `CLEAN-1`, `T-1`,
    `DEMO-POISON`) into a real issue number on somebody else's repository. So
    the witness is not that it returned a ref: it is that GitHub was never
    asked. `_repo` is left as conftest's raiser, which fails by name if it is.

    PARAMETRISED over `_NOT_AN_ISSUE` rather than asserting on one id, because
    one id cannot distinguish the shipped pattern from a partly-anchored one.
    `POISON-1` alone is refused by BOTH, so it proves the guard exists without
    proving it is tight; `7-extra` is refused only by the shipped one.
    """
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")

    state = _state(ticket_id)
    ref = github_ops.post_comment(state, "the plan, with nowhere to go")

    assert ref == f"comment://{state.run_id}"
    # The reason still reaches a human rather than evaporating.
    assert "the plan, with nowhere to go" in capsys.readouterr().out


def test_a_numeric_ticket_id_is_the_issue_it_names(monkeypatch):
    """The other half: the guard must not refuse everything.

    Paired with the test above deliberately -- a `_issue_number` that always
    returned None would pass that one, and "never posts on an issue" is not the
    claim. Both the bare number and the `#7` form GitHub itself writes resolve.
    """
    for ticket_id in ("7", "#7", " 7 "):
        repo = _RecordingRepo()
        _online(monkeypatch, repo)

        state = _state(ticket_id)
        ref = github_ops.post_comment(state, f"plan for {ticket_id}")

        assert repo.comments == [(ISSUE_NUMBER, f"plan for {ticket_id}")], ticket_id
        assert ref.startswith("https://"), ref
        # It went STRAIGHT to the issue: a PR lookup here would mean the run's
        # own branch was consulted for a run that has no branch.
        assert repo.heads == [], ticket_id


# =========================================================================
# THE ISSUE PATH DEGRADES TOO. This is the mirror of
# test_post_comment_survives_a_github_failure in tests/test_offline_mode.py,
# which covers the PR path only.
#
# WHY THIS IS THE MOST EXPENSIVE GAP IN THIS FILE. The plan and gate1 comments
# are the FIRST TWO things a judge sees, and they are the only two that take the
# issue path. `_comment_on_issue` had no test at all -- measured, `grep -rn
# "_comment_on_issue" tests/` matched nothing -- so replacing its `except
# Exception` with a bare raise passed all 735 tests while turning a routine 403
# ("Resource not accessible by integration", which is what a GitHub App without
# Issues:write returns) into a traceback at the plan stage, before anything
# renders at all.
#
# post_comment's contract is that it CANNOT raise, because graph.py calls it on
# the line after `status = "blocked"`. That contract was tested on one of its two
# online branches.
# =========================================================================

ISSUE_BOOM = "403 Resource not accessible by integration"


class _RefusingRepo:
    """A repo handle whose issue lookup or comment write fails.

    `failing` names the ONE call that raises, so each step of the issue path can
    be broken on its own -- a guard around only the write would look right and
    still crash on the lookup. Recording matters as much as raising: the claim
    includes that nothing was written when the lookup failed.
    """

    def __init__(self, failing: str):
        self.failing = failing
        self.calls: list[str] = []
        self.owner = SimpleNamespace(login="someone")

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self.failing == name:
            raise RuntimeError(ISSUE_BOOM)

    def get_issue(self, number):
        self._record("get_issue")
        repo = self

        class _Issue:
            def create_comment(self, body):
                repo._record("create_comment")
                return SimpleNamespace(html_url=f"https://github.com/o/r/issues/{number}#c1")

        return _Issue()


@pytest.mark.parametrize("failing", ["_repo", "get_issue", "create_comment"])
def test_post_comment_survives_a_github_failure_on_the_issue_path(monkeypatch,
                                                                 capsys, failing):
    """A 403 on the issue must not crash the plan stage -- the demo's first beat.

    Every step of the issue path is broken on its own, `_repo()` included, so
    this cannot pass by hardening only the first call. The witness is not just
    "did not raise": the real exception's own text has to reach stdout, because a
    canned "something went wrong" string would satisfy a handler that never
    looked at what it caught. And the ref must be the honest `comment://` one --
    a `https://` ref here would be the run's log row claiming a delivery that
    did not happen.
    """
    repo = _RefusingRepo(failing)

    def _explode():
        raise RuntimeError(ISSUE_BOOM)

    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")
    monkeypatch.setattr(github_ops, "_repo",
                        _explode if failing == "_repo" else (lambda: repo))

    state = _state(ISSUE_TICKET)
    ref = github_ops.post_comment(state, "the plan nobody could deliver")

    assert ref == f"comment://{state.run_id}", "the ref must not claim a delivery"

    out = capsys.readouterr().out
    assert "the plan nobody could deliver" in out, "the body still has to reach a human"
    assert ISSUE_BOOM in out, "the caught exception's own text, not a canned string"
    # It is the failure ending naming the ISSUE, not the PR path's wording -- a
    # handler that reported every problem as a PR failure would send a reader
    # looking for a pull request that was never involved.
    assert f"could not comment on issue #{ISSUE_NUMBER}" in out, out
    assert "no PR for" not in out


def test_the_whole_pipeline_survives_an_issue_that_refuses_every_comment(
        monkeypatch, provenance):
    """End to end: a repo that 403s every issue comment still promotes the run.

    The unit test above pins post_comment; this pins that graph.py does not
    convert its degraded return into a stopped run. Driven through run_pipeline
    with the ISSUE lookup failing and the PR path working, so plan and gate1
    both fail to deliver while the six later comments succeed -- and the verdict
    is unchanged.
    """
    provenance.none_installed()

    class _IssueRefusing(_RecordingRepo):
        def get_issue(self, number):
            if number == ISSUE_NUMBER:      # the issue, not the PR
                raise RuntimeError(ISSUE_BOOM)
            return super().get_issue(number)

    repo = _IssueRefusing()
    _online(monkeypatch, repo)
    monkeypatch.setattr(github_ops, "open_pr", _open_pr_on_local_git)

    state = run_pipeline(ISSUE_TICKET, TICKET_TEXT)

    assert state.status == "promoted", "an undeliverable issue comment is not a verdict"
    # The six PR comments still landed, so the failure was contained to the two
    # that had nowhere to go rather than poisoning the rest of the run.
    assert [_stage_of(b) for _, b in repo.comments] == [
        "develop", "review", "security", "gate2", "sre", "gate3",
    ], repo.comments
