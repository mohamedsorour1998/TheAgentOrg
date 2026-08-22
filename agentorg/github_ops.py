"""GitHub operations — branch, open PR, post comments.

OWNER: Mariam.  INTEGRATION SEAM with Sorour's graph.

The graph calls these three functions and nothing else. Their signatures are
frozen (they take/return the shapes in state.py), so the bodies could go from
stub to real without graph.py changing a line — which is exactly what happened.

Two modes:
    online  — real GitHub API via PyGithub; opens real PRs and posts real comments.
              Needs GITHUB_TOKEN + DEMO_REPO (see agentorg/common/config.py).
    local   — no network. Used when OFFLINE=true, and automatically whenever
              those credentials are absent, so every other lane (and CI) can run
              the pipeline without a token. Not a stub: it does the same work
              against a plain git repo at config.OFFLINE_REPO — real branch,
              real commit of the diff — and records what would have been a PR
              comment in config.OFFLINE_NOTES. Returns local:// refs.

The local path is the demo's insurance policy: `OFFLINE=true LLM_DISABLED=true`
walks the whole pipeline, poisoned ticket included, with the network unplugged.

deploy_note() is the third mode, and it is read-only: it asks the AgentCore
control plane whether the five theagentorg_* runtimes are actually READY and
says so. Like the two above it degrades rather than raising -- no credentials,
no network and a half-finished deploy each get an honest sentence -- because it
is rendered beside the pipeline status and a fabricated deploy claim is worse
than an admitted unknown.

`post_comment` has TWO destinations, and it chooses between them by reading the
state rather than by taking a parameter -- see `_target` for why. Plan and gate1
output has to reach the ISSUE, because the PR does not exist until `open_pr`
runs; everything from the developer onward belongs on the PR, where the diff is.
"""

import hashlib
import logging
import os
import re
import subprocess

from github import Auth, Github, GithubException

from . import fixtures_loader
from .agents.security import _one_line
from .common import config
from .state import DevResult, Finding, RunState


def _short_sha(text: str) -> str:
    """First 7 hex chars of sha1(text) — stable branch suffix per diff."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:7]


def _use_local() -> bool:
    """True when we must not touch the GitHub API.

    Either OFFLINE was requested, or no credentials are configured. The second
    case matters: PyGithub raises on an empty token, so without this guard every
    other lane's `python -m agentorg.graph` — and CI, which has no secrets —
    dies inside the PR node. Nobody needs a token to run the pipeline.
    """
    return config.OFFLINE or not (config.GITHUB_TOKEN and config.GITHUB_REPO)


def _repo():
    """Authenticated handle on the target demo repo."""
    return Github(auth=Auth.Token(config.GITHUB_TOKEN)).get_repo(config.GITHUB_REPO)


def _git(*args: str, cwd: str) -> None:
    """Run a git command in cwd, raising on failure."""
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


# Written into .git/ when we create the offline repo, and required to be there
# before we will reuse one. Inside .git deliberately: open_pr switches branches
# on every run, so a marker in the working tree could be checked out from under
# us, and one that was committed would turn up in the demo's own diffs.
_OFFLINE_MARKER = "agent-org-offline"


def _ensure_offline_repo() -> str:
    """Create the offline demo repo with a main branch if it doesn't exist.

    user.email/user.name are set LOCALLY here rather than inherited from the
    machine, because a CI container has no global identity. Git only *usually*
    covers for that: with none configured it guesses from username+hostname
    (measured on a laptop: `Mohamed Sorour <sorour@192.168.1.9>`, commit exit 0,
    with a warning). It refuses when it cannot build a plausible address, and
    refuses outright under user.useConfigOnly. Deleting these two lines and
    running the suite with that set fails 29 tests; with them, 95 pass.

    -b main is likewise explicit rather than trusting init.defaultBranch, since
    `open_pr` checks out "main" by name on every subsequent run.

    Refuses a repository it did not create. Without that check, an OFFLINE_REPO
    that already holds a .git skips init, and open_pr goes on to run
    `git checkout -B agent-org/<ticket>` THERE -- creating or resetting a branch
    and switching that checkout onto it, as a side effect of opening a PR. With
    OFFLINE_REPO=. that is this repository and the user's own working tree. The
    test is a marker file init writes, not a guess from branch names or remotes:
    the question is "did we make this?", and only a marker answers it.

    `os.path.exists`, NOT `os.path.isdir`, and that is load-bearing rather than
    sloppy. `git init` writes `.git/` as a directory, but a linked WORKTREE --
    and a submodule -- gets a one-line `gitdir:` FILE instead. `isdir` answers
    False for those, so the guard never ran and `init` went to work on a live
    checkout. Measured on the isdir version, in a throwaway worktree: it
    rewrote the victim's local user.email, committed a README.md onto the
    branch they had checked out, then died with NotADirectoryError writing the
    marker inside a `.git` that is a file. `exists` covers both shapes, and the
    marker check below still refuses correctly because
    `os.path.exists("<a file>/agent-org-offline")` is False. Note that this
    repository is itself a linked worktree, so OFFLINE_REPO=. above is the
    gitfile case, not the directory one. Pinned by
    test_open_pr_refuses_a_worktree_it_did_not_create.
    """
    path = config.OFFLINE_REPO
    os.makedirs(path, exist_ok=True)
    marker = os.path.join(path, ".git", _OFFLINE_MARKER)
    if not os.path.exists(os.path.join(path, ".git")):
        _git("init", "-b", "main", cwd=path)
        _git("config", "user.email", "agentorg@example.com", cwd=path)
        _git("config", "user.name", "Agent Org", cwd=path)
        with open(os.path.join(path, "README.md"), "w") as fh:
            fh.write("# offline demo\n")
        _git("add", "README.md", cwd=path)
        _git("commit", "-m", "init offline demo repo", cwd=path)
        with open(marker, "w") as fh:
            fh.write("Created by agentorg.github_ops offline mode.\n")
    elif not os.path.exists(marker):
        raise RuntimeError(
            f"OFFLINE_REPO is a git repository that offline mode did not "
            f"create: {os.path.abspath(path)}\n"
            f"Refusing to touch it. open_pr would run `git checkout -B "
            f"agent-org/<ticket>` there, creating or resetting a branch and "
            f"switching that checkout onto it, as a side effect of opening a "
            f"pull request. Point OFFLINE_REPO at a new or empty directory "
            f"(the default is runs/offline-demo), or delete that one if it is "
            f"a stale offline workspace from before this check existed."
        )
    return path


def open_pr(state: RunState) -> DevResult:
    """Create a branch + PR for the developer's diff. Returns DevResult with pr_url set."""
    dev = state.dev or fixtures_loader.dev()
    branch = f"agent-org/{state.ticket_id}-{_short_sha(dev.diff)}"
    dev.branch = branch

    if _use_local():
        # No network (or no credentials): do the same work against a local repo.
        path = _ensure_offline_repo()
        _git("checkout", "main", cwd=path)
        # -B resets the branch if a prior run created it, so re-runs are safe.
        _git("checkout", "-B", branch, cwd=path)
        os.makedirs(os.path.join(path, "changes"), exist_ok=True)
        diff_file = os.path.join("changes", f"{state.ticket_id}.diff")
        with open(os.path.join(path, diff_file), "w") as fh:
            fh.write(dev.diff)
        _git("add", diff_file, cwd=path)
        _git("commit", "-m", f"{state.ticket_id}: {dev.summary}", cwd=path)
        dev.pr_url = f"local://{branch}"
        return dev

    repo = _repo()

    # 1. Branch off main at its current tip.
    base = repo.get_branch("main")
    ref = f"refs/heads/{branch}"
    try:
        repo.create_git_ref(ref=ref, sha=base.commit.sha)
    except GithubException as e:
        if e.status != 422:
            raise

    # 2. Commit the diff. Write the raw unified diff so the PR carries the
    # change the scanners will read. One deterministic path per run.
    path = f"changes/{state.ticket_id}.diff"
    message = f"{state.ticket_id}: {dev.summary}"

    try:
        existing = repo.get_contents(path, ref=branch)
        repo.update_file(
            path,
            message,
            dev.diff,
            existing.sha,
            branch=branch,
        )
    except GithubException as e:
        if e.status == 404:
            repo.create_file(
                path,
                message,
                dev.diff,
                branch=branch,
            )
        else:
            raise

    # 3. Open the PR (reuse the open one if this branch already has it).
    try:
        # THE BODY CARRIES A CLOSING KEYWORD, which is what makes GitHub link the
        # pull request to the issue in the issue's own Development sidebar.
        #
        # Without it the two are related only by a number in a title, so an issue
        # gives a reader no way to reach the work it produced -- and the issue is the
        # surface this pipeline is judged on. `Closes #N` is GitHub's own mechanism:
        # it populates the sidebar, and it closes the issue when the PR merges.
        #
        # Only when the ticket id IS an issue number. `_issue_number` refuses
        # anything else, deliberately, because a loose parse would attach this PR to
        # whichever issue happened to match -- see its own comment.
        issue_number = _issue_number(state.ticket_id)
        body = dev.summary
        if issue_number is not None:
            body = f"{dev.summary}\n\nCloses #{issue_number}"

        pr = repo.create_pull(
            title=message,
            body=body,
            head=branch,
            base="main",
        )
    except GithubException as e:
        if e.status == 422:
            pr = repo.get_pulls(
                state="open",
                head=f"{repo.owner.login}:{branch}",
            )[0]
        else:
            raise

    dev.pr_url = pr.html_url
    return dev


def _undelivered(what: str, exc: Exception, body: str, ref: str) -> str:
    """Report a comment we could not deliver, and hand back the honest ref.

    Both of post_comment's paths degrade through here, so the function carries
    one pattern rather than two. The shape is security.run's scanner fallback:
    one bounded line at WARNING naming the cause, the body itself on stdout,
    and the traceback left to the DEBUG record its caller emits.

    "comment" rather than "block reason", which is what this said when the block
    explanation was the only thing anyone posted. Every stage's output goes
    through post_comment now, so a failure here can lose the planner's tasks or
    the SRE's verdict just as easily -- and a projector line naming the wrong
    artifact sends the reader looking for a block that never happened.

    Bounded is the load-bearing word. During the demo this prints on the
    projector immediately above `status=blocked`, and a wall of text there
    reads as a crash rather than as the block working. A real GithubException
    carries the whole JSON response body in its message, so `exc` goes through
    _one_line -- imported from security rather than copied, so the two callers'
    200-char bound cannot drift. `what` is bounded too: it names a branch or a
    path, and neither is ours to trust the length of.

    Returns `ref` -- the caller's "not delivered" ref -- so that the log row
    graph.py writes cannot claim a delivery that did not happen.
    """
    detail = f"{type(exc).__name__}: {_one_line(str(exc))}"
    logging.getLogger(__name__).warning(
        "could not %s (%s); comment body to stdout instead",
        _one_line(what, limit=100), detail,
    )
    print(f"[post_comment] could not {what} ({detail}); reason: {body}")
    return ref


# The two surfaces a comment can land on, in the words the offline NOTES header
# records. Named constants rather than bare strings at four call sites, because
# the NOTES header and the online branch must not be able to disagree about
# which one a given comment was for -- that disagreement is unobservable.
ON_ISSUE = "issue"
ON_PULL_REQUEST = "pull request"


def _destination(state: RunState) -> str:
    """Which surface this comment belongs on: the run's issue, or its PR.

    DERIVED FROM THE STATE, not passed in as a parameter, and that is a
    deliberate choice rather than the lazy one. `post_comment(state, body,
    finding=None)` is called from graph.py and from the reviewer lane, and its
    signature is named in the plan's own type-consistency check as unchanged.
    Adding a `target=` argument would mean every existing caller keeps whatever
    the default is -- so the one call that most needs to reach the issue (the
    planner's, which runs before any PR exists) would silently keep going to the
    PR lookup, and the failure would be a comment that quietly went nowhere.

    The discriminator is the branch, because that is the thing that only exists
    once `open_pr` has run: it OVERWRITES `dev.branch` with the branch it
    created (`open_pr` above, `dev.branch = branch`). Before the developer runs
    at all, `state.dev` is None. So plan and gate1 -- the two stages that
    precede the developer -- resolve to the issue, and everything after
    `open_pr` resolves to the PR.

    WHAT THIS CANNOT DO, stated because it is an accepted limit rather than an
    oversight: between the developer returning and `open_pr` running, `state.dev`
    carries the AGENT's branch (the fixture's is `feat/login-rate-limit`), which
    is not a branch this run created and has no PR. A comment posted in that
    window would resolve to ON_PULL_REQUEST and then fail to find one. graph.py
    closes that window by holding the developer's and reviewer's comments until
    `open_pr` has run rather than by asking this function to guess.
    """
    return ON_PULL_REQUEST if (state.dev and state.dev.branch) else ON_ISSUE


# A ticket id that IS an issue reference, and nothing looser. `#7` is the form
# GitHub itself writes; a bare `7` is what the ingress passes through
# (infra/Terraform/modules/ingress/main.tf sends `"ticket_id": "<issue_number>"`).
#
# BOTH ANCHORS ARE LOAD-BEARING, and `.match` is not a substitute for them.
# MEASURED over 15 ticket ids: dropping `\A`/`\Z` while keeping `.match` still
# diverges on `7-extra`, `7 7`, `#7x` and `1-2` -- each yields issue 7 (or 1)
# where this pattern yields None, which is a comment written on a real issue
# nobody named. An earlier version of this file's report called that mutation
# benign; it is not, and the refusal test below now covers it.
#
# `[0-9]` NOT `\d`, deliberately -- do not "tidy" this. `\d` is Unicode-aware, so
# `\d+` matches the Arabic-Indic `\u0667` and `int()` accepts it, returning 7. A
# ticket id in another numeral system would therefore resolve to an issue number
# no reader of the id would predict. Measured:
#     re.match(r"\A#?\d+\Z", "\u0667") -> matches, int("\u0667") == 7
#     re.match(r"\A#?[0-9]+\Z", "\u0667") -> None
_ISSUE_REF = re.compile(r"\A#?([0-9]+)\Z")


def _issue_number(ticket_id: str) -> int | None:
    """The issue this run came from, or None when the ticket id is not one.

    ANCHORED, and that is the whole point of the function. The lenient readings
    -- the first digits in the string, or `int(re.sub(r"\\D", "", ticket_id))` --
    turn every ticket id this repo actually uses into a real issue number on
    somebody else's repository: `POISON-1` and `CLEAN-1` become #1, `T-1`
    becomes #1, `DEMO-1` becomes #1. The next thing post_comment does with the
    answer is WRITE, so this is the same refusal the branch check below makes
    for the same reason: a lookup built from a value we did not really resolve
    is not a lookup that finds nothing.

    Returning None is not a failure to handle -- it is the ordinary case for
    every locally-driven run, and post_comment answers it the way it answers a
    missing PR: the body to stdout and a `comment://` ref back to the caller.
    """
    match = _ISSUE_REF.match((ticket_id or "").strip())
    return int(match.group(1)) if match else None


def _delivered_ref(comment, ref: str) -> str:
    """The posted comment's URL, or the undelivered ref if it has none.

    post_comment is annotated `-> str` and the timeline splits its return value
    on "://" to classify delivery, so a None here would be a TypeError inside the
    renderer -- on the artifact a judge is reading -- rather than at the point of
    the fault. PyGithub types `html_url` as a plain attribute off the API
    response, so an unexpected payload shape (a proxy error body, a future API
    change) can leave it None while `create_comment` itself succeeded.

    `ref` rather than "" for the fallback, because "" would render as a delivery
    that simply carried no ref, which is indistinguishable from a comment nobody
    tried to post. `comment://<run_id>` says the attempt was made and the proof
    of delivery is missing -- which is exactly what happened.

    Shared by BOTH online paths, the issue one and the PR one, so the annotation
    cannot be honoured in one and broken in the other.
    """
    url = getattr(comment, "html_url", None)
    return url if isinstance(url, str) and url else ref


def _comment_on_issue(state: RunState, body: str, ref: str) -> str:
    """Post on the issue that opened this run. Cannot raise; see post_comment."""
    number = _issue_number(state.ticket_id)
    if number is None:
        # No issue to write on. NOT an error and not silent: this is every run
        # driven from a terminal or a workflow_dispatch, so the body goes to
        # stdout exactly as an unreachable PR's does.
        print(f"[post_comment] ticket {state.ticket_id!r} is not an issue "
              f"number, so there is no issue to comment on; reason: {body}")
        return ref

    try:
        posted = _repo().get_issue(number).create_comment(body)
        return _delivered_ref(posted, ref)
    except Exception as exc:
        # The same two lines, in the same order, as the other two handlers in
        # this function's family -- see the PR branch below for why the traceback
        # is a separate inline DEBUG call rather than folded into _undelivered.
        logging.getLogger(__name__).debug("post_comment failure traceback", exc_info=True)
        return _undelivered(f"comment on issue #{number}", exc, body, ref)


def _note_locally(state: RunState, body: str, destination: str, ref: str) -> str:
    """Append one comment to the offline NOTES file. Cannot raise; see post_comment.

    THE ONE WRITER of that file, and it takes `destination` as an argument rather
    than deriving it, because its two callers disagree about the answer and both are
    right: `post_comment` asks `_destination(state)`, while `report_outcome` always
    means the ISSUE even though a PR exists by then.

    Extracted for that disagreement alone. Left inline, `report_outcome` had to go
    through `post_comment` to reach this file, which stamped its entry `pull request`
    while the online path posted the same comment to the issue -- so the NOTES file,
    whose header marker exists to make the issue/PR split observable offline, would
    have been recording a destination the online path does not use. A marker that can
    disagree with the behaviour it describes is worse than no marker.

    Wrapped, and this is the branch that matters most: the demo command is
    `OFFLINE=true`, so an unwritable NOTES path -- a read-only workspace, a stale
    directory sitting where the file should be, a full disk -- is a traceback on the
    path stage actually takes. Measured before this guard existed:
    `OFFLINE=true python -m agentorg.graph --poisoned` exited 1 with IsADirectoryError,
    on a run that had correctly blocked.
    """
    try:
        # `or "."` because dirname("NOTES.md") is "", and makedirs("") raises.
        os.makedirs(os.path.dirname(config.OFFLINE_NOTES) or ".", exist_ok=True)
        with open(config.OFFLINE_NOTES, "a") as fh:
            fh.write(f"\n## {state.ticket_id} ({state.run_id}) → {destination}"
                     f"\n{body}\n")
    except Exception as exc:
        # Inline and at DEBUG: this is the "demote, don't drop" half, and it is also
        # what satisfies BLE001 -- the rule wants a logging call carrying exc_info in
        # the handler itself, which a call to _undelivered would not provide.
        logging.getLogger(__name__).debug("post_comment failure traceback",
                                          exc_info=True)
        return _undelivered("write the comment to the offline NOTES file",
                            exc, body, ref)
    # Only now -- a local:// ref means the bytes are on disk. Returning it from
    # anywhere above would be the artifact claiming a delivery that did not happen,
    # which is worse than the silence this replaced.
    return f"local://{config.OFFLINE_NOTES}"


def post_comment(state: RunState, body: str, finding: Finding | None = None) -> str:
    """Post a comment on this run's PR, or on its issue. Returns a comment ref.

    WHICH OF THE TWO is decided by `_destination(state)`, not by an argument --
    read that function for why. Plan and gate1 output reaches the ISSUE because
    the PR does not exist until `open_pr`; everything from the developer onward
    reaches the PR, where the diff is.

    Returns a ref string in every case, and does not raise. That is a hard
    requirement rather than politeness, because of WHERE it is called from:
    graph.py sets `status="blocked"` and then, on the very next line, records
    the ref this returns. The block is the product; the comment is only how a
    human learns why. So a comment that cannot be delivered must not be able to
    convert a correctly-blocked run into a traceback -- on stage, in front of
    judges. Since graph.py now posts after EVERY stage, that requirement got
    nine times more load-bearing: any one of nine failures could take the demo
    down, and none of them may.

    BOTH paths degrade, and the offline one matters most: the demo command is
    `OFFLINE=true`, so that is the branch stage actually takes.

      * OFFLINE -- the NOTES file cannot be written: a read-only workspace, a
        stale directory sitting where the file should be, a full disk. The ref
        then says `comment://<run_id>`, NOT `local://<path>`. That distinction
        is the whole point of returning a ref at all: graph.py records it, so a
        `local://` ref on a run whose bytes never reached disk would be the
        artifact claiming a delivery that did not happen.

    Four more ways delivery fails online, all ending the same way (the body on
    stdout, a `comment://<run_id>` ref back to the caller):

      * there is no branch to look a PR up by -- `state.dev` is None, or its
        branch is still "". We do not ASK GitHub in that case. `head="owner:"`
        is not a filter that selects nothing, so a query built from an empty
        branch is a query that can come back with somebody else's PR, and
        this function's next move is to write on whatever came back. That state
        now routes to the issue instead, which has the same refusal of its own:
      * the ticket id is not an issue number, so there is no issue either. See
        `_issue_number` -- a loose parse would write on issue #1 of the target
        repository for every `CLEAN-1` run this suite performs.
      * the branch has no open PR. Ordinary: `open_pr` is skipped or the PR was
        merged or closed between the two calls.
      * the API call itself fails -- rate limit, 502, an expired token, a
        locked conversation. Caught broadly ON PURPOSE: the caller has already
        decided to block, and there is no failure from this API worth losing
        that decision over. One bounded line at WARNING and the traceback
        demoted to DEBUG, for the reason spelled out on the handler itself.

    `_repo()` is inside that try too, and the conftest guard that keeps the
    suite off the live API survives it only because `pytest.fail` raises
    `Failed`, which derives from BaseException rather than Exception. Pinned by
    test_the_blind_except_does_not_swallow_the_conftest_github_guard.
    """
    if finding is not None:
        header = (
            f"**[{finding.tool} · {finding.severity}] {finding.rule}** "
            f"({finding.file}:{finding.line})\n\n"
        )
        body = header + body

    # The "not delivered" ref, shared by every path and computed before any of
    # them runs. graph.py writes whatever comes back into the run's log row,
    # so this is the value that tells a reader the reason never landed anywhere.
    ref = f"comment://{state.run_id}"
    destination = _destination(state)

    if _use_local():
        # No network (or no credentials): append the body to a local NOTES file.
        #
        # THE HEADER NAMES THE DESTINATION. Offline there is no issue and no PR, so
        # both surfaces collapse onto this one file -- and without the word, the
        # issue/PR split would be unobservable on the path the demo and the whole
        # suite actually run, leaving it pinned by the online tests alone.
        return _note_locally(state, body, destination, ref)

    if destination == ON_ISSUE:
        return _comment_on_issue(state, body, ref)

    branch = state.dev.branch if state.dev and state.dev.branch else ""
    no_pr = f"[post_comment] no PR for {branch!r}; reason: {body}"

    try:
        repo = _repo()
        pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
        if pulls.totalCount == 0:
            print(no_pr)
            return ref
        posted = repo.get_issue(pulls[0].number).create_comment(body)
        return _delivered_ref(posted, ref)
    except Exception as exc:
        # Same two lines as the offline branch above, in the same order, for
        # the same reasons. BLE001 can force a logging call carrying exc_info
        # to EXIST; it cannot force its level, its wording or its length, so
        # those are pinned by a caplog test rather than by lint -- see
        # test_a_chatty_github_failure_stays_one_short_warning_line. Nothing
        # here can reach the return value; logging cannot affect control flow.
        logging.getLogger(__name__).debug("post_comment failure traceback", exc_info=True)
        return _undelivered(f"comment on the PR for branch {branch!r}", exc, body, ref)


# CI conclusions that are NOT failures. GitHub's own semantics: a skipped or
# neutral check is not a red build, and a path-filtered workflow reports
# `skipped` on every commit its filter excludes -- calling that a failure would
# block every such change.
#
# An ALLOW-LIST, not a deny-list of the bad ones, and that is the fail-closed
# direction. GitHub's conclusion vocabulary can grow; a value this set has never
# seen means CI said something we do not understand, and the safe reading of that
# is "not green". A deny-list would send every future conclusion to `passing`.
_CI_NOT_A_FAILURE = frozenset({"success", "skipped", "neutral"})


def ci_status(state: RunState) -> str:
    """`"passing"`, `"failing"` or `"unknown"` for this run's head commit.

    THE THIRD VALUE IS THE POINT. GitHub reports a commit status of `pending`
    when NOTHING has run, which is indistinguishable from "still running" if you
    read that field. MEASURED 2026-08-22 on the target repo before it had any
    workflow at all:

        gh api repos/.../contents/.github/workflows -> 404 Not Found
        gh api repos/.../commits/<sha>/status       -> {"state": "pending",
                                                        "total_count": 0}

    So zero checks is `unknown`, never `passing`. A commit nothing has examined
    is not a green commit, and an SRE agent reporting "CI passing" about a
    repository that has never run a test is the fail-open shape the security lane
    exists to prevent, one agent over.

    Reads CHECK RUNS rather than the commit `status` field, deliberately. The
    status API's `state` collapses "nothing ran" and "still running" into one
    word; check runs keep `status` and `conclusion` separate, which is the only
    way to tell those two apart -- and telling them apart is this function's
    entire job.

    NEVER RAISES, and always returns one of the three. Every caller is on the
    pipeline path, and a promoted run must not depend on GitHub being reachable
    at the moment the SRE stage happens to run -- but an unreachable GitHub is
    `unknown`, not `passing`, so an outage cannot read as a green build.

    Works against a target repository with CI and one without. `unknown` is a
    first-class answer, not an error.
    """
    # No branch means no head to look up -- and we do NOT ask. `get_branch("")`
    # is not a query that selects nothing, so a lookup built from an empty branch
    # is a lookup that can come back with something else. Same refusal, and the
    # same reasoning, as post_comment's empty-branch guard above.
    if _use_local() or state.dev is None or not state.dev.branch:
        return "unknown"

    try:
        repo = _repo()
        head = repo.get_branch(state.dev.branch).commit.sha
        runs = repo.get_commit(head).get_check_runs()
        # totalCount and the iteration BOTH inside the try: PyGithub defers the
        # HTTP request until one of them is touched, so a network failure
        # surfaces here rather than at the first read below.
        total = runs.totalCount
        conclusions = [r.conclusion for r in runs]
    except Exception:
        # Broad on purpose, and the reason is the same one post_comment gives:
        # the failure set spans PyGithub, the network, an expired token and the
        # response shape, and the SRE stage must not die because one of them
        # moved. The inline exc_info is what satisfies BLE001 -- narrowing this
        # clause would satisfy it too, with no logging at all, which is the worse
        # option. Note `_repo()` is inside the try, and conftest's guard survives
        # it only because pytest.fail raises Failed, which derives from
        # BaseException rather than Exception.
        logging.getLogger(__name__).debug("ci_status lookup failed", exc_info=True)
        return "unknown"

    if total == 0:
        return "unknown"

    # A check with no conclusion has not finished. In progress is not green:
    # treating it as passing would let a merge land before CI completed. Checked
    # BEFORE the all-success test, because a run of green checks plus one still
    # going is the ordinary mid-CI shape of a real pull request -- and reading
    # only the finished ones is how a merge lands during CI while looking fully
    # informed.
    if any(c is None for c in conclusions):
        return "unknown"
    if all(c in _CI_NOT_A_FAILURE for c in conclusions):
        return "passing"
    return "failing"


# THE PRECONDITIONS FOR AN IRREVERSIBLE WRITE, checked here as well as enforced
# by the job graph. `promote` is only reachable past gate3, so in the workflow
# these are already true -- but a function that merges somebody else's pull
# request must not depend on a caller's control flow for the one thing it must
# never do, and `graph.py` and `scripts/run_stage.py` are two callers with two
# orderings.
_MERGE_REQUIRED_GATES = ("gate1", "gate2", "gate3")

# A human decision that lets a run through. `overridden` counts, and that is not
# leniency: `gates_cli resume --decision overridden` is the documented route a
# human keeps for a known false positive, and it is the one capability a human is
# meant to retain. Treating it as "not approved" would silently revoke it, and the
# run would refuse to merge with three human decisions on file.
_MERGE_PERMITTING_DECISIONS = frozenset({"approved", "overridden"})


def _merge_refusal(state: RunState) -> str | None:
    """Why this run must not be merged, or None if it may be.

    Returns a SHORT reason suitable for a ref and a log summary. Every branch is
    a fact about the RUN, not about GitHub -- GitHub's own refusals are handled at
    the call site, because "we declined" and "GitHub declined" are different
    events and a reader needs to know which. One is a policy the pipeline
    enforced; the other is a conflict somebody has to go and resolve.

    A MISSING result is a refusal, not a pass. `security is None` means the stage
    did not run, and a stage that did not run cleared nothing -- the same
    "did not run" versus "passed" distinction this whole project exists to keep
    from collapsing, at the last write.
    """
    if state.security is None or state.security.verdict != "pass":
        verdict = None if state.security is None else state.security.verdict
        return f"security-verdict-{verdict}"
    if state.sre is None or state.sre.verdict != "go":
        verdict = None if state.sre is None else state.sre.verdict
        return f"sre-verdict-{verdict}"

    # READ the decisions, do not COUNT them. `gates.resume` sets
    # status="rejected" for a refusal and NEVER un-sets it, so approving a run
    # the graph already rejected appends the approval and leaves the rejection on
    # file: a run can carry three -- or four -- decision rows one of which is a
    # refusal. `len(decisions) >= 3` would pass that, and so would "is every gate
    # approved?", because gate2 would appear in both sets.
    #
    # So the rejection is checked FIRST and independently: a refusal on file
    # outranks a later approval for the same gate.
    approved = {
        d.gate for d in state.decisions
        if d.decision in _MERGE_PERMITTING_DECISIONS
    }
    rejected = {d.gate for d in state.decisions if d.decision == "rejected"}
    if rejected:
        # `min`, not `sorted(...)[0]`: ruff's FURB192 fires on the latter and this
        # repo carries no per-file ignores. Either way the choice of WHICH
        # rejected gate to name is deterministic, which matters because the ref
        # goes into a log row two callers write.
        return f"gate-rejected-{min(rejected)}"
    missing = [g for g in _MERGE_REQUIRED_GATES if g not in approved]
    if missing:
        return f"gate-not-approved-{missing[0]}"
    return None


def merge_pr(state: RunState) -> str:
    """Merge the run's pull request. Returns a ref; NEVER raises.

    The same hard requirement as `post_comment`, for the same reason: `promote`
    calls this and then writes the run's ending, so an exception here would lose
    that ending -- and `timeline._outcome` reads its banner off the LAST log row,
    so losing the ending changes what a judge sees. A merge that did not happen
    is a recorded fact.

    Four ref shapes, and the distinction between the last two is the point:

        https://…                     merged
        local://<branch>              offline; no GitHub was reached
        merge://refused/<reason>      WE declined -- a fact about the run
        merge://failed/<type>         GITHUB declined, or was unreachable

    "The run was not allowed to merge" and "the merge was attempted and did not
    land" call for different actions from whoever reads the timeline. Collapsing
    them would make a conflicted pull request look like a policy refusal.

    THE PRECONDITIONS ARE CHECKED BEFORE THE OFFLINE BRANCH, deliberately.
    Otherwise the demo's own fallback path -- `OFFLINE=true`, the one it takes on
    stage if the runtimes misbehave -- would answer `local://` for a run that must
    never merge, and the blocked beat's final line would read as a delivery.

    Not wired into `promote` here: both promote sites belong to the pipeline
    modules, which call this and write the `merged` log row.
    """
    refusal = _merge_refusal(state)
    if refusal is not None:
        logging.getLogger(__name__).warning(
            "refusing to merge run %s: %s", state.run_id, refusal
        )
        return f"merge://refused/{refusal}"

    branch = state.dev.branch if state.dev else ""
    if _use_local():
        # Offline the branch is already committed in OFFLINE_REPO and there is no
        # PR to merge. Reported as delivered-locally rather than refused: nothing
        # about the RUN prevented this merge, and calling it a refusal would make
        # every local run -- and the whole suite -- look like a policy failure.
        return f"local://{branch}"

    if not branch:
        # We do NOT ask. `head="owner:"` is not a filter that selects nothing, so
        # a query built from an empty branch can come back with somebody else's
        # pull request -- and the next thing this function does is MERGE what came
        # back. Same refusal, and the same reasoning, as post_comment's.
        return "merge://failed/no-branch-to-look-up"

    try:
        repo = _repo()
        pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
        if pulls.totalCount == 0:
            return "merge://failed/no-open-pull-request"
        pull = pulls[0]

        # `mergeable` is None while GitHub is still computing it and False on a
        # conflict. Only False is refused here: None means "not known yet", and
        # the merge call itself is the authoritative answer -- GitHub refuses it
        # if the branch really cannot merge, which lands in the
        # `merged=False` check below. Neither case is worth FORCING; a conflict is
        # a fact to report.
        if pull.mergeable is False:
            return "merge://failed/not-mergeable"

        result = pull.merge(
            commit_title=f"{state.ticket_id}: {state.dev.summary}",
            commit_message=(
                f"Merged by The Agent Org after three human approvals.\n\n"
                f"run_id: {state.run_id}\n"
                f"security: {state.security.verdict} "
                f"(provenance: {state.security.scan_provenance or 'unknown'})\n"
                f"ci: {state.sre.ci_status}\n"
            ),
            merge_method="squash",
        )
        # A 200 that did not merge is the reassuring non-answer. Trusting the
        # absence of an exception would report a merge that did not happen --
        # this project's signature failure shape, at the one write that cannot be
        # taken back.
        if not getattr(result, "merged", False):
            return "merge://failed/github-declined"
        return pull.html_url
    except Exception as exc:
        # Broad on purpose, and this is the clause the whole "never raises"
        # contract rests on: the failure set spans PyGithub, the network, an
        # expired token, a protected branch and the response shape. The inline
        # exc_info is what satisfies BLE001 -- narrowing this would satisfy it
        # too, with no logging, which is the worse option. The type name goes in
        # the ref so the log row can say what went wrong.
        logging.getLogger(__name__).debug("merge failed", exc_info=True)
        return f"merge://failed/{type(exc).__name__}"


# How a run's ending reads on the issue that started it. Keyed on RunState.status.
#
# READ WITH `.get` AND AN EXPLICIT UNKNOWN DEFAULT, not `[]`. A KeyError here would
# be raised from a function whose whole contract is that it cannot raise -- the last
# thing a run does, after the status is already decided -- so a status this table has
# never seen would lose the run's ending in order to complain about not recognising
# it. The default names the status verbatim instead, which is strictly more useful
# than a traceback naming a dict lookup. Same reasoning as
# `compute_security_verdict`'s threshold, and the opposite conclusion, because that
# one is a knob validated at import while this one is a value already in hand.
_OUTCOME_HEADLINE = {
    "promoted": ("✅ ACCEPTED", ("the change was reviewed, scanned, approved at "
                                "three gates and merged")),
    "blocked": ("⛔ REJECTED", ("the deterministic security rule blocked this "
                               "change; it was not merged")),
    "rejected": ("✗ REJECTED", "a human declined this change at a gate"),
    "failed": ("✗ NOT MERGED", ("the change did not earn approval, so it was not "
                                "merged")),
    "running": ("… INCOMPLETE", "this run did not reach an ending"),
}

# Which endings close the issue. A run that is still `running` closes nothing, and a
# blocked run DOES close its issue: the pipeline reached a verdict and that verdict
# was no, which is an answer rather than an abandonment. Leaving it open would imply
# the work is still pending.
_CLOSING_STATUSES = frozenset({"promoted", "blocked", "rejected", "failed"})


def report_outcome(state: RunState) -> str:
    """Post the run's ending to the issue and close it. NEVER raises.

    WHY THIS EXISTS. The issue is where a reader starts, and until now a run told it
    only how things BEGAN -- the plan and the gate1 decision. Everything after that
    landed on the pull request, so an issue was left open forever with no statement of
    what happened, whether the change shipped, or why it did not. On a poisoned run
    that is the worst case: the block is the whole point, and the issue that asked for
    the work never learned it had been refused.

    Closed as `completed` for a promoted change and `not_planned` for every other
    ending, which is GitHub's own distinction between "this got done" and "this will
    not be done". A reader scanning a list of issues sees the difference without
    opening one.

    NEVER RAISES, the same contract as `post_comment` and `merge_pr`, and for the same
    reason: this is the last thing a run does, after the status is already decided.
    An exception here would lose the run's ending to report a failure to report it.
    """
    headline, detail = _OUTCOME_HEADLINE.get(
        state.status, ("… UNKNOWN", f"run ended with status {state.status!r}")
    )

    # The literal, not graph.COMMENT_HEADER, because graph imports THIS module and
    # the reverse import would be a cycle. Restated rather than moved, because moving
    # it would touch seven renderers days before the demo; the drift risk is real and
    # a test asserts the two agree.
    lines = ["### Agent Org · outcome", "", f"**{headline}** — {detail}", ""]

    if state.security is not None:
        provenance = state.security.scan_provenance or "unknown"
        lines.append(
            f"- security: `{state.security.verdict}` "
            f"({len(state.security.blocking)} blocking, provenance: {provenance})"
        )
        for finding in state.security.blocking:
            lines.append(
                f"    - `{finding.tool}` **{finding.rule}** at "
                f"`{finding.file}:{finding.line}`"
            )
    if state.sre is not None:
        lines.append(f"- sre: `{state.sre.verdict}` (CI: {state.sre.ci_status})")
    if state.review is not None:
        lines.append(
            f"- review: `{state.review.verdict}` after "
            f"{state.revision_count + 1} pass(es)"
        )
    if state.dev is not None and state.dev.pr_url:
        lines.append(f"- pull request: {state.dev.pr_url}")
    if state.decisions:
        approved = [d.gate for d in state.decisions if d.decision != "rejected"]
        lines.append(f"- human gates passed: {', '.join(approved) or '(none)'}")

    lines.append("")
    lines.append(f"_run `{state.run_id}` · source: {state.model_provenance or 'unknown'}_")

    body = "\n".join(lines)
    number = _issue_number(state.ticket_id)

    # STRAIGHT TO THE ISSUE ON BOTH PATHS, not through `post_comment`.
    #
    # `post_comment` routes by `_destination`, which sends anything after `open_pr` to
    # the PULL REQUEST -- correct for every stage comment, and wrong for exactly this
    # one. The issue is the surface that stays open and that a reader returns to; an
    # outcome posted only to the PR leaves the issue with no ending, which is the gap
    # this function was added to close.
    #
    # So the offline branch calls `_note_locally` with ON_ISSUE explicitly rather than
    # going through `post_comment` and inheriting `pull request`. Offline the two
    # surfaces are one file, but its header names the destination, and a header saying
    # `pull request` for a comment the online path puts on the issue would make the
    # NOTES file disagree with the behaviour it exists to make observable.
    ref = f"comment://{state.run_id}"
    if _use_local():
        ref = _note_locally(state, body, ON_ISSUE, ref)
    elif number is None:
        print(f"[report_outcome] ticket {state.ticket_id!r} is not an issue number; "
              f"outcome to stdout instead:\n{body}")
    else:
        # `_comment_on_issue` is the same helper the plan and gate1 comments use, so
        # this inherits their never-raises behaviour.
        ref = _comment_on_issue(state, body, ref)

    if _use_local() or number is None or state.status not in _CLOSING_STATUSES:
        return ref

    try:
        issue = _repo().get_issue(number)
        issue.edit(
            state="closed",
            state_reason="completed" if state.status == "promoted" else "not_planned",
        )
    except Exception:
        # The comment above is the load-bearing half; an issue left open is untidy,
        # not wrong. Logged with the traceback rather than raised, because the run has
        # already finished and there is nothing left to abort.
        logging.getLogger(__name__).debug(
            "could not close issue #%s", number, exc_info=True
        )

    return ref


# The five AgentCore runtimes this repo deploys, in the order the spec prints# them (docs/plan/mariam/week3.md:118-131). UNDERSCORED on purpose: these are
# AgentCore RUNTIME names, a different namespace from the HYPHENATED ECR
# REPOSITORY names (theagentorg-shared-<agent>-agent) recorded in
# docs/plan/week1-verification-log.md, which Tasks 5/6 push images to. Both are
# real and they name different things, so neither can be derived from the other.
DEPLOYED_AGENTS = ("planner", "developer", "reviewer", "security", "sre")
RUNTIME_NAMES = tuple(f"theagentorg_{a}" for a in DEPLOYED_AGENTS)

# The one runtime status that means "deployed and serving". AgentCore's own
# service model also reports CREATING, CREATE_FAILED, UPDATING, UPDATE_FAILED
# and DELETING -- read out of botocore's bedrock-agentcore-control model rather
# than guessed:
#     status enum: ['CREATING', 'CREATE_FAILED', 'UPDATING', 'UPDATE_FAILED',
#                   'READY', 'DELETING']
# Accepting any of the others would let a runtime that FAILED to create render
# as a live deploy, which is the exact fabricated success this function exists
# to prevent. Existence is not readiness.
RUNTIME_READY = "READY"

# Bounds on the one network call this module makes. botocore's defaults are
# connect_timeout=60, read_timeout=60 and legacy retries -- measured, not
# assumed. deploy_note()'s return value is rendered on the projector during a
# judged demo, so an unreachable control plane behind those defaults stalls the
# demo for minutes before it can print anything honest. A fast, honest "not
# verified" beats a correct answer that arrives after the audience has moved on,
# so this call gets one attempt and gives up in seconds.
AGENTCORE_CONNECT_TIMEOUT = 3
AGENTCORE_READ_TIMEOUT = 5


def _aws_credentials_available() -> bool:
    """True when an AgentCore call is worth attempting. Makes no network call.

    Same shape and same reasoning as llm.available() (common/llm.py:44-61):
    resolving credentials is local, so this separates "nobody configured AWS"
    -- routine on CI and on every teammate's laptop -- from "AWS was configured
    and the call failed", which is an anomaly worth a projector line. The two
    deserve different log levels, and that is the whole reason they are two
    branches rather than one try/except around a real API call.
    """
    try:
        import boto3

        session = boto3.Session(region_name=config.AWS_REGION)
        return session.get_credentials() is not None
    except Exception:
        # Routine on a machine with no AWS setup, so this stays at debug level
        # -- and the exc_info is what satisfies ruff BLE001 for the broad
        # clause. Broad on purpose: the failure set spans boto3, botocore and
        # the credential file itself, and deploy_note() must not raise.
        logging.getLogger(__name__).debug(
            "AWS credential lookup failed; treating the deploy as unverified",
            exc_info=True,
        )
        return False


def _agentcore_client():
    """Bounded bedrock-agentcore-control client. The seam tests replace.

    boto3 is imported lazily, as in llm.available(): a module-level import here
    would make every `import agentorg.github_ops` -- including the graph's, and
    CI's -- pay for botocore whether or not anything asks about the deploy.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore-control",
        region_name=config.AWS_REGION,
        config=Config(
            connect_timeout=AGENTCORE_CONNECT_TIMEOUT,
            read_timeout=AGENTCORE_READ_TIMEOUT,
            retries={"max_attempts": 0},
        ),
    )


def _unverified(reason: str, detail: str = "", *, routine: bool = False) -> str:
    """The one honest answer for every path that could not confirm the deploy.

    Three causes reach this -- no credentials, the call failed, and the
    runtimes are not (all) there -- and they share one function for the reason
    _undelivered does: one shape cannot drift out of step with itself, and
    three bespoke messages can. What varies between them is one thing only,
    the log LEVEL, so that is the only parameter.

    `routine` picks it, and the split is the same one llm.available() makes. No
    credentials is the COMMON path -- CI has no secrets and neither does a
    teammate's laptop -- so warning there would put a line on the projector on
    every single call and train the demo's audience to ignore warnings. That
    goes to DEBUG. Credentials that exist but do not work is an anomaly nobody
    has seen yet, and it is worth one bounded WARNING line.

    Bounded is load-bearing, exactly as in _undelivered: a botocore error
    carries the whole HTTP response, and this string lands on the projector
    beside the pipeline status, where a wall of text reads as a crash. Both
    halves go through _one_line -- imported from security, not copied, so the
    callers' 200-char bound cannot drift.

    The prefix deliberately does NOT reuse the verified message's
    "AgentCore runtimes (us-east-1): " opening. Sharing it would make the two
    outcomes indistinguishable to a test asserting on the prefix -- and to a
    human glancing at the projector, which is worse.
    """
    note = f"AgentCore deploy unverified: {_one_line(reason, limit=100)}"
    if detail:
        note = f"{note} ({_one_line(detail)})"
    log = logging.getLogger(__name__)
    if routine:
        log.debug("%s", note)
    else:
        log.warning("%s", note)
    # No print(). _undelivered prints because its RETURN value is a ref and the
    # reason had nowhere else to go; here the return value IS the message the
    # caller displays, so printing would put it on the projector twice.
    return note


def deploy_note() -> str:
    """Report the AgentCore deploy target for the log/UI. Never raises.

    Takes no arguments and returns a str, so the spec's done-when
    (`python -c "... print(github_ops.deploy_note())"`) still calls it bare.

    The spec (week3.md:118-131) writes this as a hardcoded one-liner. That
    satisfies its done-when while asserting a deploy that may not exist, and
    plan Task 4 forbids exactly that: "no AWS credentials, no network, or no
    deployment yet must produce an honest message, never an exception and never
    a fabricated success." So the spec's string stays REACHABLE -- character
    for character, on the verified path -- but is not the unconditional return
    value. Controller Ruling 10.

    Four outcomes, and every one of them returns a non-empty string:
      * no credentials      -> unverified, DEBUG (the CI and laptop path)
      * the call failed     -> unverified, WARNING, with the cause bounded
      * all five are READY  -> the spec's exact string
      * anything else       -> unverified, naming what is missing or not ready

    The fourth is the `else`, not a fifth branch that forgot to return. An
    unrecognised state rendering as an empty string would be silence -- which
    is indistinguishable from the absent deploy note this function replaced.
    """
    if not _aws_credentials_available():
        return _unverified("no AWS credentials", routine=True)

    try:
        pages = _agentcore_client().get_paginator("list_agent_runtimes").paginate()
        # Paginated, not a bare list_agent_runtimes(): the five runtimes share
        # an account with whatever else is deployed there, so a single page is
        # not a promise of the whole set. Parsing lives inside the try with the
        # call, so a response shaped differently than expected degrades here
        # instead of raising out of a function that must not raise.
        ready = {
            r.get("agentRuntimeName")
            for page in pages
            for r in page.get("agentRuntimes", [])
            if r.get("status") == RUNTIME_READY
        }
    except Exception as exc:
        # The traceback goes to a separate inline DEBUG call, as in
        # post_comment (github_ops.py:314-318, 347): that is what BLE001 wants
        # in the handler itself, and a call to _unverified would not provide
        # it. Broad on purpose -- the failure set spans boto3, botocore, the
        # network and the response shape, and any one of them escaping would
        # turn a status line into a crashed demo.
        logging.getLogger(__name__).debug("deploy_note failure traceback", exc_info=True)
        return _unverified(f"could not list runtimes in {config.AWS_REGION}",
                           f"{type(exc).__name__}: {exc}")

    # Exact set membership, never a substring or prefix test: a runtime called
    # theagentorg_planner_v2 must not be able to satisfy theagentorg_planner.
    missing = [name for name in RUNTIME_NAMES if name not in ready]
    if not missing:
        return f"AgentCore runtimes (us-east-1): {', '.join(RUNTIME_NAMES)}"
    return _unverified(
        f"{len(RUNTIME_NAMES) - len(missing)} of {len(RUNTIME_NAMES)} runtimes ready",
        f"not ready: {', '.join(missing)}",
    )
