"""A read-only snapshot of the target repository, shared by every agent.

OWNER: Sorour.

WHY THIS EXISTS
===============
Before it, each agent reasoned about the target repository from its NAME alone.
`developer._prompt` passed target file paths and nothing else, so the agent wrote a
diff against a file it had never seen: it invented the original, which is why its
`@@` hunk headers and context lines were guesses that `git apply` would refuse.

MEASURED on the deployed pipeline, and the failures were not subtle:

  * the developer proposed `sync.RWMutex` and `NewRateLimiter` -- GO -- for a Python
    Flask application, four revisions running, until the cap expired with the
    scanners reporting PASS
  * the planner named `app/controllers/password_resets_controller.rb`,
    `config/initializers/rate_limit_config.rb` and `spec/requests/...`, which is a
    RAILS layout. Nothing in the repository resembles it
  * the reviewer listed "Missing import for the authenticate function" as a blocking
    issue, for a file that defines `authenticate` twenty lines above the hunk

None of those is a model being careless. Each is an agent asked to reason about
bytes it was never shown.

A CLONE, NOT A HANDFUL OF API READS
===================================
One `git clone --depth 1`, briefly cached, read by all five agents. That is what
a human developer has, and it is simpler than five agents each fetching different
subsets through different calls -- which was the first version of this and produced
exactly the divergence you would expect: a reviewer judging a diff against different
information than the developer wrote it from, so its objections were unactionable.

ANONYMOUS, WITH NO CREDENTIAL ANYWHERE
======================================
The clone is unauthenticated, and that is a deliberate constraint rather than an
oversight. The five AgentCore runtimes carry exactly one environment variable --
`AGENT_ROLE` (`deploy.yml`) -- plus `SCANNERS_REQUIRED` on the security image. They
have no GitHub token, and shipping one into five containers so they could read a
PUBLIC repository would be a real credential in five more places for no capability.

Verified before relying on it: `git clone --depth 1 https://github.com/<target>`
succeeds with no authentication, because the target repository is public.

The consequence is stated rather than hidden: **a private target repository gets no
snapshot.** `snapshot()` returns an empty mapping, every agent falls back to the
names-only prompt it used before this module existed, and the pipeline still
completes. Degraded, not broken -- and the alternative is a token in five runtimes.

`git` IS IN THE IMAGE ALREADY, for an unrelated reason: `github_ops.open_pr` shells
out to real git on the offline path, so the Dockerfile installs it. This module adds
no dependency.

WHAT IT DELIBERATELY DOES NOT DO
================================
It does not write. It does not fetch, pull or check out a branch. `--depth 1` on the
default branch, into a temporary directory, read and discarded.

IT IS DYNAMIC, NOT A BUILD-TIME SNAPSHOT. The clone happens when an agent asks, so a
run that follows a merge sees the merged file -- which matters because `promote`
merges, and the demo runs two tickets against one repository. See CACHE_TTL_SECONDS
for why the window is short rather than absent. An agent that could
mutate the target repository through this seam would be a much larger change than
the one this module makes.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path

from .common import config
from .common.diff import added_files

# Files an agent would learn nothing from, and which dominate a listing. Matched on
# path prefix, because `.git` and `node_modules` are what turn a forty-file
# repository into a forty-thousand-file one.
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", ".pytest_cache",
    ".ruff_cache", "dist", "build", ".mypy_cache", ".tox",
})

# Binary and generated files. An agent cannot patch these usefully and their bytes
# would crowd out the source it can.
_SKIP_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".zip", ".gz", ".tar", ".whl",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf", ".lock",
})

# THE PROMPT BUDGET, and every one of these is a ceiling rather than a target.
#
# The prompt also carries the ticket, the plan, the previous diff and the reviewer's
# must_fix list. A snapshot that crowded those out would trade one failure for a
# worse one: a model that stops following instructions it can no longer see.
#
# Sized against the actual target -- `app/auth.py` is ~1.4 KB and the whole
# repository is under 20 KB of source -- so these bind on a hypothetical monorepo,
# not on this demo.
MAX_FILES = 40
MAX_FILE_BYTES = 20_000
MAX_TOTAL_BYTES = 120_000

# Cached only for a SHORT WINDOW, not for the life of the process, and the
# difference matters because the repository changes DURING a run.
#
# `promote` merges the pull request. So a second run against the same repository must
# see the first run's merge, or its planner is reasoning about a file that no longer
# looks like that -- and the demo runs two tickets back to back against one repo.
#
# A process-lifetime cache also breaks a single run: an AgentCore container serves
# many invocations over hours, so the planner of run 2 could be answered from a
# clone taken before run 1 merged.
#
# The window exists at all because ONE run's five agents should agree with each
# other. A developer patching bytes the reviewer no longer sees is the divergence
# this module was written to remove, and a run completes well inside the TTL.
#
# 120s: longer than a pipeline stage, far shorter than the gap between runs, which
# includes at least one human clicking a gate.
CACHE_TTL_SECONDS = 120

_CACHE: dict[str, str] | None = None
_CACHE_AT: float = 0.0


def reset_cache() -> None:
    """Forget the snapshot. For tests, and to force a re-read after a known merge."""
    global _CACHE, _CACHE_AT
    _CACHE = None
    _CACHE_AT = 0.0


def _clone_url() -> str:
    """The public HTTPS URL for the target repo. No credential, by design."""
    return f"https://github.com/{config.GITHUB_REPO}.git"


def _is_interesting(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in _SKIP_DIRS for part in relative.parts):
        return False
    return path.suffix.lower() not in _SKIP_SUFFIXES


def _read_tree(root: Path) -> dict[str, str]:
    """Every interesting file under `root`, as {relative path: text}."""
    out: dict[str, str] = {}
    total = 0

    # Sorted, so the snapshot is deterministic: two runs against the same commit
    # produce byte-identical prompts, which is what makes a model's answer
    # reproducible enough to debug.
    for path in sorted(root.rglob("*")):
        if len(out) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            break
        if not path.is_file() or not _is_interesting(path, root):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > MAX_FILE_BYTES:
            text = text[:MAX_FILE_BYTES] + "\n… truncated …\n"
        out[str(path.relative_to(root))] = text
        total += len(text)

    return out


def snapshot() -> dict[str, str]:
    """The target repository as {path: contents}. NEVER raises; may be empty.

    Re-clones once the TTL expires, so a run that follows a merge sees the merged
    file rather than the one its predecessor started from.

    Empty means one of: no target configured, a private repository, git absent, or a
    network failure. Every caller treats an empty snapshot as "carry on without it",
    because a degraded prompt is better than a failed pipeline stage -- and the agents
    behaved that way for the whole project before this module existed.
    """
    global _CACHE, _CACHE_AT
    if _CACHE is not None and (time.monotonic() - _CACHE_AT) < CACHE_TTL_SECONDS:
        return _CACHE

    if not config.GITHUB_REPO:
        _CACHE, _CACHE_AT = {}, time.monotonic()
        return _CACHE

    log = logging.getLogger(__name__)
    try:
        with tempfile.TemporaryDirectory(prefix="agentorg-snapshot-") as workdir:
            # --depth 1: one commit. --no-tags and a single branch keep it to the
            # bytes an agent will actually read.
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--no-tags", "--single-branch",
                 _clone_url(), workdir],
                capture_output=True, text=True, check=False, timeout=60,
            )
            if result.returncode != 0:
                # A private repository fails here, and so does a network outage. Both
                # get the same answer because the caller's next move is the same.
                log.info(
                    "no repository snapshot: git clone exited %s (%s)",
                    result.returncode, (result.stderr or "").strip()[:200],
                )
                _CACHE, _CACHE_AT = {}, time.monotonic()
                return _CACHE

            _CACHE, _CACHE_AT = _read_tree(Path(workdir)), time.monotonic()
            log.info("repository snapshot: %d files", len(_CACHE))
            return _CACHE
    except Exception:
        log.debug("repository snapshot failed", exc_info=True)
        _CACHE, _CACHE_AT = {}, time.monotonic()
        return _CACHE


def apply_diff(files: dict[str, str], diff: str | None) -> dict[str, str]:
    """`files` as the diff would leave them. Best effort; never raises.

    WHY THE REVIEWER NEEDS THIS AND THE DEVELOPER DOES NOT. The developer is about to
    write a diff, so it wants the file as it stands. The reviewer is judging a diff
    that has already been written, so what matters is the file AS THE CHANGE WOULD
    LEAVE IT -- otherwise it is handed an original plus a patch and asked to apply the
    patch in its head, which is exactly the work that produces objections like
    "missing import for the authenticate function" about an import three lines above
    the hunk.

    DELETIONS ARE APPLIED, and the first version of this did not apply them. That
    version appended `+` lines and ignored `-` lines entirely, so a removal-only diff
    produced an after-view byte-identical to the before-view -- under a heading
    promising the reader it was what the change would leave behind. MEASURED:

        before:  AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
        diff:    -AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
        after:   AWS_KEY = "AKIAIOSFODNN7EXAMPLE"   <- still there

    That is the poisoned run's exact revision shape: the reviewer correctly asks for
    the credential to be removed, the developer complies, and the reviewer is then
    shown a file that still contains it -- so it objects again to a problem already
    fixed, and the revision cap expires on a change that was correct. The block
    verdict is unaffected either way, because the scanners read the diff rather than
    this view, so the cost is a `failed` run rather than a false pass.

    Removed lines are matched by CONTENT, not by line number. A model-written diff's
    `@@` offsets are unreliable -- `git apply` refuses such diffs outright, and
    refusing is the wrong answer here -- but a `-` line quotes the text it removes
    verbatim, so the content is trustworthy where the position is not.
    """
    if not diff:
        return files

    try:
        added = added_files(diff)
    except Exception:
        # `added_files` raises on a diff with no recognised header. The reviewer is
        # better served by the unpatched files than by nothing at all.
        logging.getLogger(__name__).debug("apply_diff: unparseable", exc_info=True)
        return files

    removed = _removed_lines(diff)
    out = dict(files)

    for path in set(added) | set(removed):
        if path not in out:
            # A new file: its added lines ARE its contents, and there is nothing to
            # remove from.
            out[path] = added.get(path, "")
            continue

        kept = [
            line for line in out[path].splitlines()
            if line.strip() not in removed.get(path, frozenset())
        ]
        new_lines = added.get(path, "")
        if new_lines:
            # Appended with a marker rather than spliced at a line number, for the
            # reason above: the position is a guess, the content is not. Labelling it
            # honestly beats placing it wrongly.
            kept.append(f"# ---- lines this diff ADDS to {path} ----")
            kept.extend(new_lines.splitlines())
        out[path] = "\n".join(kept) + "\n"

    return out


def _removed_lines(diff: str) -> dict[str, frozenset[str]]:
    """`{path: stripped text of each line the diff removes}`.

    Keyed on stripped content so a line's indentation changing between the clone and
    the diff cannot hide a removal. The cost is that a duplicated line removes both
    copies from the view -- acceptable, because the alternative is showing the
    reviewer a credential the change deletes.

    Mirrors `added_files`'s header handling rather than reusing it, because that
    function returns bodies and this needs a membership test. `-` lines before the
    first header belong to no file and are dropped, exactly as its `+` lines are.
    """
    out: dict[str, set[str]] = {}
    current: str | None = None

    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].split("\t", 1)[0].strip()
            for prefix in ("a/", "b/", "i/", "w/", "c/", "o/", "old/", "new/"):
                if path.startswith(prefix):
                    path = path[len(prefix):]
                    break
            current = None if path in ("/dev/null", "") else path
            continue
        if line.startswith(("---", "+++")):
            continue
        if current is not None and line.startswith("-") and not line.startswith("---"):
            out.setdefault(current, set()).add(line[1:].strip())

    return {path: frozenset(lines) for path, lines in out.items()}


def render(paths: list[str] | None = None, diff: str | None = None) -> str:
    """The snapshot as prompt text, or "" when there is nothing to show.

    `paths` narrows the rendering to files an agent especially cares about while
    still listing everything else, so the developer sees the file it must patch in
    full without losing the shape of the project around it.

    `diff`, when given, renders the files AS THAT DIFF WOULD LEAVE THEM. The reviewer
    passes it; the developer does not, because the developer is the one writing it.

    Returns "" rather than a heading with nothing under it: an empty
    "REPOSITORY CONTENTS" section reads as "this repository is empty", which is a
    different and worse claim than saying nothing at all.
    """
    files = snapshot()
    if not files:
        return ""

    heading = "THE TARGET REPOSITORY, as it is right now:"
    if diff:
        files = apply_diff(files, diff)
        heading = (
            "THE TARGET REPOSITORY, WITH THE DIFF UNDER REVIEW APPLIED — this is "
            "what the change would leave behind:"
        )

    wanted = [p for p in (paths or []) if p in files]
    rest = [p for p in files if p not in wanted]

    sections = [heading]
    sections.append("FILES:\n" + "\n".join(f"- {p}" for p in sorted(files)))

    for path in wanted + rest:
        sections.append(f"--- {path} ---\n{files[path]}")

    return "\n\n".join(sections)
