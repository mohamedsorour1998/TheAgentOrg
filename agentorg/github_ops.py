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
"""

import hashlib
import logging
import os
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
        pr = repo.create_pull(
            title=message,
            body=dev.summary,
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
    """Report a block reason we could not deliver, and hand back the honest ref.

    Both of post_comment's paths degrade through here, so the function carries
    one pattern rather than two. The shape is security.run's scanner fallback:
    one bounded line at WARNING naming the cause, the reason itself on stdout,
    and the traceback left to the DEBUG record its caller emits.

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
        "could not %s (%s); block reason to stdout instead",
        _one_line(what, limit=100), detail,
    )
    print(f"[post_comment] could not {what} ({detail}); reason: {body}")
    return ref


def post_comment(state: RunState, body: str, finding: Finding | None = None) -> str:
    """Post a comment on the PR (reviewer + security lanes). Returns a comment ref.

    Returns a ref string in every case, and does not raise. That is a hard
    requirement rather than politeness, because of WHERE it is called from:
    graph.py sets `status="blocked"` and then, on the very next line, calls
    `post_comment(state, state.security.explanation)`. The block is the
    product; the comment is only how a human learns why. So a comment that
    cannot be delivered must not be able to convert a correctly-blocked run
    into a traceback -- on stage, in front of judges.

    BOTH paths degrade, and the offline one matters most: the demo command is
    `OFFLINE=true`, so that is the branch stage actually takes.

      * OFFLINE -- the NOTES file cannot be written: a read-only workspace, a
        stale directory sitting where the file should be, a full disk. The ref
        then says `comment://<run_id>`, NOT `local://<path>`. That distinction
        is the whole point of returning a ref at all: graph.py records it, so a
        `local://` ref on a run whose bytes never reached disk would be the
        artifact claiming a delivery that did not happen.

    Three more ways delivery fails online, all ending the same way (the reason
    on stdout, a `comment://<run_id>` ref back to the caller):

      * there is no branch to look a PR up by -- `state.dev` is None, or its
        branch is still "". We do not ASK GitHub in that case. `head="owner:"`
        is not a filter that selects nothing, so a query built from an empty
        branch is a query that can come back with somebody else's PR, and
        this function's next move is to write on whatever came back.
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

    # The "not delivered" ref, shared by both paths and computed before either
    # of them runs. graph.py writes whatever comes back into the run's log row,
    # so this is the value that tells a reader the reason never landed anywhere.
    ref = f"comment://{state.run_id}"

    if _use_local():
        # No network (or no credentials): append the reason to a local NOTES file.
        # `or "."` because dirname("NOTES.md") is "", and makedirs("") raises.
        #
        # Wrapped for the same reason the online branch is, and this is the
        # branch that matters more: the demo command is `OFFLINE=true`, so an
        # unwritable NOTES path -- a read-only workspace, a stale directory
        # sitting where the file should be, a full disk -- is a traceback on
        # the path stage actually takes. Measured before this guard existed:
        # `OFFLINE=true python -m agentorg.graph --poisoned` exited 1 with
        # IsADirectoryError, on a run that had correctly blocked.
        try:
            os.makedirs(os.path.dirname(config.OFFLINE_NOTES) or ".", exist_ok=True)
            with open(config.OFFLINE_NOTES, "a") as fh:
                fh.write(f"\n## {state.ticket_id} ({state.run_id})\n{body}\n")
        except Exception as exc:
            # Inline and at DEBUG: this is the "demote, don't drop" half, and
            # it is also what satisfies BLE001 -- the rule wants a logging call
            # carrying exc_info in the handler itself, which a call to
            # _undelivered would not provide.
            logging.getLogger(__name__).debug("post_comment failure traceback",
                                              exc_info=True)
            return _undelivered("write the block reason to the offline NOTES file",
                                exc, body, ref)
        # Only now -- a local:// ref means the bytes are on disk. Returning it
        # from anywhere above would be the artifact claiming a delivery that
        # did not happen, which is worse than the silence this replaced.
        return f"local://{config.OFFLINE_NOTES}"

    branch = state.dev.branch if state.dev and state.dev.branch else ""
    no_pr = f"[post_comment] no PR for {branch!r}; reason: {body}"

    if not branch:
        print(no_pr)
        return ref

    try:
        repo = _repo()
        pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
        if pulls.totalCount == 0:
            print(no_pr)
            return ref
        return repo.get_issue(pulls[0].number).create_comment(body).html_url
    except Exception as exc:
        # Same two lines as the offline branch above, in the same order, for
        # the same reasons. BLE001 can force a logging call carrying exc_info
        # to EXIST; it cannot force its level, its wording or its length, so
        # those are pinned by a caplog test rather than by lint -- see
        # test_a_chatty_github_failure_stays_one_short_warning_line. Nothing
        # here can reach the return value; logging cannot affect control flow.
        logging.getLogger(__name__).debug("post_comment failure traceback", exc_info=True)
        return _undelivered(f"comment on the PR for branch {branch!r}", exc, body, ref)


# The five AgentCore runtimes this repo deploys, in the order the spec prints
# them (docs/plan/mariam/week3.md:118-131). UNDERSCORED on purpose: these are
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
