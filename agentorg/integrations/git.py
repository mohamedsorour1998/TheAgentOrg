"""The SECOND adapter — plain git, no code-hosting vendor at all. NOT SHIPPED.

OWNER: Lane D.  Plan D6: "a second adapter sketch (GitLab or plain git) to prove
the interface is real. **Not** shipped — a one-adapter interface is an unproven
claim."

WHY PLAIN GIT AND NOT GITLAB. GitLab would have proved less. Its model is
GitHub's: a remote host with issues, merge requests, pipelines and comments, so
every method maps one-to-one and the exercise would confirm nothing except that
two similar APIs are similar. It would also need a vendor SDK, which is the thing
§5 says to reduce.

Plain git has NO issue, NO pull request, NO CI and NO comment surface. It is the
adapter most likely to reveal that a method on this interface was really a GitHub
feature -- and that is what it did, twice.

=========================================================================
WHAT WRITING THIS FOUND. BOTH WERE FIXED IN base.py, NOT WORKED AROUND HERE
=========================================================================

1. **`post_comment` had no destination a bare repository could name.** The first
   draft of the interface said "issue or pull request", which is not a choice a
   git remote offers. A git host CAN record a comment durably -- as a `git notes`
   ref, or as a file on the branch -- but it cannot answer "which surface". So the
   contract had to be stated as the REF, not as the surface: `local://` means the
   bytes landed somewhere durable, whatever that somewhere is. That is why
   `base.DELIVERY_SCHEMES` names schemes and not destinations, and it is why the
   issue/PR split lives in `github_ops._destination` rather than on the interface.

   Had the interface kept a `destination` in its signature, this adapter would
   have had to return a lie for it.

2. **`ci_status` cannot be `passing` here, ever, and the fail-safe had to be a
   FIRST-CLASS answer rather than an error path.** A bare repository has nothing
   that runs tests, so this adapter answers `unknown` unconditionally. The first
   draft treated `unknown` as "the lookup failed", which would have made a
   correct, honest, permanent answer read as a fault on every call. `CI_UNKNOWN`
   is now documented as an answer, and `github_ops.ci_status`'s own three-cause
   distinction -- nothing ran / we could not look / nobody asked -- is what makes
   that honest rather than lazy.

A third finding, which is a LIMIT and not a fix: `merge_pr` here is a real
`git merge --no-ff` into `main`, and there is no review, no approval surface and
no protected branch. The pipeline's three gates still hold, because they are
`gates.py` and the job graph, not the host -- but a customer running this adapter
has no second line of defence at the host. That is recorded here rather than
patched, because patching it would mean inventing an approval model this adapter
does not have.

=========================================================================
NOT SHIPPED, AND `host()` REFUSES IT
=========================================================================

`shipped = False`. It passes the conformance suite; that is not permission to
serve a run. Two honest reasons:

  * it merges into `main` with no protected-branch check, so the only thing
    standing between a diff and `main` is this pipeline's own gates.
  * `report_outcome` writes a file. Nothing reads it, so a run's ending would be
    recorded where no human is looking -- which is the "reported to nobody" state
    `comment://` exists to name.

It is ~120 lines against `github_ops.py`'s 1,132, and the ratio is the point: the
second implementation of a real interface should be small.
"""

from __future__ import annotations

import os
import subprocess

from ..common import config
from ..state import DevResult, Finding, RunState
from .base import (
    CI_UNKNOWN,
    MERGE_FAILED_PREFIX,
    MERGE_REFUSED_PREFIX,
    SCHEME_DELIVERED_LOCAL,
    CodeHost,
)
from .memory import MemoryHost

# Where a comment goes when there is no comment surface. A file on disk beside the
# repository, one line per comment, because the alternative -- `git notes` -- ties
# a comment to a commit and the planner's comment precedes every commit this run
# makes.
NOTES_FILENAME = "AGENT_ORG_NOTES.md"


class GitHost(CodeHost):
    """A code host that is only a git repository. No vendor, no API, no issues.

    Uses `config.OFFLINE_REPO` as its working repository, which means conftest
    guard 3 already redirects it at `tmp_path` -- so this adapter inherits the
    fixture that keeps real git out of the working tree rather than needing its
    own.
    """

    name = "git"
    shipped = False

    # THE REFUSAL PREDICATE IS SHARED WITH THE OTHER TWO ADAPTERS, deliberately.
    # `merge_pr` must refuse a run that has not earned a merge -- the security
    # verdict, the SRE verdict, three gate decisions -- and a second copy of that
    # rule in a second adapter is a second answer with nothing recording which one
    # a merge used. `github_ops._merge_refusal` reads only the RunState; it names
    # no GitHub type, which is exactly why it is reusable here.

    def _run(self, *args: str) -> str:
        """One git command in the repository. Raises on a non-zero exit.

        Raising is correct: `open_pr` is the interface's one raising method, and
        the four wrapped ones convert this into their own fail-safe value.
        """
        done = subprocess.run(
            ["git", *args], cwd=self._repo_path(), check=True,
            capture_output=True, text=True,
        )
        return done.stdout

    def _repo_path(self) -> str:
        """The repository this adapter works in, created on first use."""
        path = config.OFFLINE_REPO
        os.makedirs(path, exist_ok=True)
        if not os.path.exists(os.path.join(path, ".git")):
            # `os.path.exists`, NOT `isdir`, for the reason
            # `github_ops._ensure_offline_repo` records: a linked worktree gets a
            # one-line `gitdir:` FILE, so `isdir` answers False and this would
            # `git init` on top of a live checkout.
            subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True,
                           capture_output=True, text=True)
            for key, value in (("user.email", "agentorg@example.com"),
                               ("user.name", "Agent Org")):
                subprocess.run(["git", "config", key, value], cwd=path,
                               check=True, capture_output=True, text=True)
            with open(os.path.join(path, "README.md"), "w") as fh:
                fh.write("# agent org git host\n")
            subprocess.run(["git", "add", "README.md"], cwd=path, check=True,
                           capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True,
                           capture_output=True, text=True)
        return path

    def open_pr(self, state: RunState) -> DevResult:
        """A branch carrying the diff. There is no pull request to open.

        `pr_url` is a `local://<branch>` ref rather than an empty string, because
        `state.py` types it `str | None` and `report_outcome` renders it -- and a
        blank there reads as "no change was proposed" rather than as "this host
        has no PR concept".
        """
        dev = state.dev or MemoryHost().open_pr(state)
        branch = dev.branch or f"agent-org/{state.ticket_id}"
        path = self._repo_path()
        self._run("checkout", "main")
        self._run("checkout", "-B", branch)
        os.makedirs(os.path.join(path, "changes"), exist_ok=True)
        relative = os.path.join("changes", f"{state.ticket_id}.diff")
        with open(os.path.join(path, relative), "w") as fh:
            fh.write(dev.diff)
        self._run("add", relative)
        self._run("commit", "-m", f"{state.ticket_id}: {dev.summary}")
        dev.branch = branch
        dev.pr_url = f"{SCHEME_DELIVERED_LOCAL}://{branch}"
        return dev

    def _post_comment(self, state: RunState, body: str,
                      finding: Finding | None = None) -> str:
        """Append the comment to a notes file. THE REF IS THE CONTRACT, not a surface.

        `local://` only AFTER the write returns, which is the discipline
        `github_ops._note_locally` records: a ref handed back before the bytes
        land is the artifact claiming a delivery that did not happen.
        """
        if finding is not None:
            body = (f"**[{finding.tool} · {finding.severity}] {finding.rule}** "
                    f"({finding.file}:{finding.line})\n\n{body}")
        target = os.path.join(self._repo_path(), NOTES_FILENAME)
        with open(target, "a") as fh:
            fh.write(f"\n## {state.ticket_id} ({state.run_id})\n{body}\n")
        return f"{SCHEME_DELIVERED_LOCAL}://{target}"

    def _merge_pr(self, state: RunState) -> str:
        """Merge the branch into main, if the RUN earned it.

        NO PROTECTED BRANCH AND NO REVIEW EXIST HERE -- see the module docstring.
        The refusal predicate is the only thing between this and `main`.
        """
        # Imported at call time, not at module scope, so a test substituting the
        # predicate on the module that OWNS it is observed here too -- the same
        # trap-avoidance the config knobs use.
        from .. import github_ops

        refusal = github_ops._merge_refusal(state)
        if refusal is not None:
            return f"{MERGE_REFUSED_PREFIX}{refusal}"
        branch = state.dev.branch if state.dev else ""
        if not branch:
            return f"{MERGE_FAILED_PREFIX}no-branch-to-merge"
        self._run("checkout", "main")
        self._run("merge", "--no-ff", "-m",
                  f"{state.ticket_id}: merged by The Agent Org", branch)
        return f"{SCHEME_DELIVERED_LOCAL}://merged/{branch}"

    def _report_outcome(self, state: RunState) -> str:
        """The run's ending, into the notes file. Nothing reads it -- see the docstring."""
        return self._post_comment(
            state, f"outcome: {state.status}", None,
        )

    def _ci_status(self, state: RunState) -> str:
        """Always `unknown`, and that is an ANSWER rather than a failure.

        A bare repository runs no tests. `unknown` is honest and permanent here;
        it must never become `passing`, because a commit nothing examined is not a
        green commit -- the fail-open shape the security lane exists to prevent,
        one seam over.
        """
        return state.ci_status_measured or CI_UNKNOWN
