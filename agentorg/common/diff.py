"""What a unified diff PROPOSES: its added lines, keyed by file.

OWNER: Sorour, but it is a contract with Habiba's scanner lane, so read the
paragraph below before changing what counts as "in this change".

A unified diff carries three kinds of line and only one of them is part of the
change being proposed:

    `+`   added   -- the new code. This is what a reviewer, a scanner, and the
                     merged branch all end up reading.
    `-`   removed -- code the change DELETES. Present in the diff text,
                     absent from the result.
    ` `   context -- unchanged surroundings, shown for orientation.

Every scanner wrapper in agentorg/security/ rebuilds the changed files from the
`+` lines alone and scans those, so "the change contains X" can only ever mean
"an added line contains X". That used to be re-derived at four call sites --
three wrappers plus the poisoned safety net in agents/developer.py -- and the
safety net's copy asked a different question: it searched the whole diff STRING,
removal lines included. From revision 2 onward the reviewer correctly asks the
developer to take the hardcoded credentials out, the model complies, and the
only AKIA... left in the diff is on a `-` line. The safety net read "the key is
present", declined to substitute the reference diff, and handed the scanners a
change with no secret in it; compute_security_verdict([]) then correctly
returned "pass" and the poisoned ticket promoted. Measured on five live runs:
it blocked on two. Nothing was wrong with the block rule or the scanners --
they were handed the wrong input.

So there is one materialiser now, here, and both sides call it. Anything that
wants to know what a change contains asks this module; if the answer ever needs
to change, it changes once and the safety net and the scanners move together.
"""

from pathlib import Path

# The `+++` line marker. Recognising the marker is separate from reading the
# PATH out of it, deliberately: a `+++` line this parser cannot attribute to a
# file is a header it did not understand, and the whole point of the refusal at
# the bottom of `added_files` is that it can tell that apart from a diff that
# genuinely proposes nothing.
_PLUS_HEADER = "+++ "

# THE PREFIXES GIT ACTUALLY EMITS, PLUS NO PREFIX AT ALL -- not just `b/`.
#
# MEASURED 2026-08-22, when this module matched the literal `"+++ b/"` and
# nothing else:
#
#     b/ prefix    files=['app/auth.py'] key=True
#     no prefix    files=[]              key=False
#     a/ both      files=[]              key=False
#     old/new      files=[]              key=False
#
# Three of the four materialised ZERO files, so the scanners ran over an empty
# tree, returned [], and compute_security_verdict([]) returned ("pass", []) --
# with scan_provenance truthfully recording `scanners`, because they had run.
# They had nothing to read. THE DIFF IS MODEL-WRITTEN, so a non-default prefix
# is a plausible thing to receive and not a hypothetical.
#
# `a` and `b` are git's default. `i` and `w` (index / worktree), `c` and `o`
# (commit / object) come from `diff.mnemonicPrefix`. `1`, `2` and `3` are the
# merge stages in a combined diff. `old` and `new` are the plain-`diff -u`
# convention that a model writing a diff by hand is most likely to reach for.
#
# A WHITELIST, NOT "STRIP ONE COMPONENT", and that distinction is the point.
# `git apply -p1` strips unconditionally, and so does the obvious regex
# `(?:[^/]+/)?(?P<path>.+)` -- MEASURED, it turns `+++ app/auth.py` into
# `auth.py` and `+++ src/pkg/mod.py` into `pkg/mod.py`. The file would still be
# scanned, so no verdict-reading test could catch it; only the path a judge
# reads on the pull request would be wrong, one directory level short. `app/` is
# a directory, not a prefix, and nothing in a `+++` line says which it is except
# knowing what the tools emit.
_PATH_PREFIXES = frozenset({"a", "b", "i", "w", "c", "o", "1", "2", "3",
                            "old", "new"})

# git writes this for the side of the diff where the file does not exist.
_ABSENT = "/dev/null"


def _header_path(line: str) -> str | None:
    """The path a `+++` line names, prefix stripped, or None if unreadable.

    None for `/dev/null` -- a deleted file, which materialises nothing -- and
    None for a `+++` line carrying no path at all, which is a header this parser
    did not understand. `added_files` treats those two differently, so they are
    distinguished by the count of RECOGNISED headers rather than here.
    """
    # A unified-diff header may carry a tab and a timestamp or revision after
    # the path (`+++ b/app/auth.py\t2026-08-22 10:00:00`). The path is what
    # precedes the first tab; `strip()` alone would keep the timestamp and
    # produce a filename no scanner could report.
    target = line[len(_PLUS_HEADER):].split("\t", 1)[0].strip()
    # git quotes a path containing unusual characters. Unquoting is one
    # `strip`, not a full C-escape decode -- the demo's paths are plain ASCII
    # and a half-decode would be worse than none.
    if len(target) >= 2 and target.startswith('"') and target.endswith('"'):
        target = target[1:-1]
    if not target or target == _ABSENT:
        return None
    head, _, rest = target.partition("/")
    if head in _PATH_PREFIXES and rest:
        return rest
    return target


def added_files(diff: str | None) -> dict[str, str]:
    """Rebuild the files `diff` proposes: `{path: added lines, joined}`.

    Only `+` lines count, and only after a `+++ <path>` header has said which
    file they belong to -- added lines before the first header belong to no file
    and are dropped, which is the second way "somewhere in the diff text" and
    "in this change" disagree. A `/dev/null` target names a deleted file and
    materialises nothing. Paths repeated across headers keep the last body.

    Every prefix in `_PATH_PREFIXES` is accepted, and so is no prefix -- see
    that constant for the measurement that made the `b/`-only form dangerous.

    RAISES ValueError when a non-empty diff yields no recognised header. See the
    refusal at the bottom of the function; `added_files(None)` and
    `added_files("")` still return `{}`.
    """
    files: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    # Counted rather than inferred from `files`, because a delete-only diff
    # (`+++ /dev/null` and nothing else) is a header this parser UNDERSTOOD that
    # correctly materialises nothing. Keying the refusal on `not files` would
    # turn that correct parse into a red security stage -- the false alarm that
    # gets a guard deleted.
    recognised_headers = 0

    for line in (diff or "").splitlines():
        if line.startswith(_PLUS_HEADER):
            if current is not None:
                files[current] = "\n".join(body)
            relative = _header_path(line)
            # `/dev/null` and an empty target both give None here, but only the
            # first is a header this parser read. `_header_path` cannot tell the
            # caller which, so the check is repeated on the raw line.
            if relative is not None or _ABSENT in line:
                recognised_headers += 1
            current = relative
            body = []
            continue

        # `+++` is the header marker, never content -- see the docstring.
        if line.startswith("+") and not line.startswith("+++"):
            body.append(line[1:])

    if current is not None:
        files[current] = "\n".join(body)

    # A NON-EMPTY DIFF WITH NO HEADER THIS PARSER READ IS A PARSE FAILURE, NOT A
    # CLEAN CHANGE, and the difference decides whether the pipeline lies.
    #
    # Returning `{}` here sends an empty directory to every scanner. They
    # succeed, find nothing, and the verdict is `pass` -- while scan_provenance
    # says `scanners`, which is true and useless. Raising makes the security
    # stage fail loudly: a red job, which is recoverable, instead of a green one
    # that cleared a change nobody read.
    #
    # `write_added_files`'s docstring already records where this lands: the
    # raise propagates out of the scanner wrapper into `security.run`, which
    # logs one bounded WARNING and falls back to the FIXTURE verdict -- which
    # still blocks a diff carrying an AWS key. So the fail direction is closed
    # on both halves of the demo.
    if not recognised_headers and diff and diff.strip():
        raise ValueError(
            f"parsed no files from a {len(diff)}-character diff: every `+++` "
            f"line was unrecognised, or there was none, so there is nothing to "
            f"scan. Refusing rather than handing an empty tree to the scanners, "
            f"which would report a clean pass over a change nobody read."
        )
    return files


def write_added_files(diff: str | None, dest_dir: str) -> None:
    """Materialize `added_files(diff)` under `dest_dir`, ready to be scanned.

    The trailing newline matters: a scanner reads a file, not a Python string,
    and gitleaks reports the line NUMBER it found a secret on. scripts/scan_gate
    pins those numbers (app/auth.py:3 and :4 on the poisoned fixture), so this
    has to stay one file line per added diff line, in order, with the file
    ending in a newline like any other text file.

    Every target must land INSIDE `dest_dir`, and one that does not raises.
    The path in a `+++` header is written by the model, and `Path(dest_dir)
    / relative` follows an absolute target or a `..` escape straight out of the
    scratch directory: measured before this guard, `+++ b/../escaped.py` and an
    absolute target both wrote model-chosen bytes outside the directory being
    scanned, as whatever user CI runs as, and left the scanned tree EMPTY.

    Loudly, not silently. Dropping the offending file would leave the scanners
    a smaller tree, and an empty tree is a clean scan -- compute_security_verdict
    ([]) returns "pass", which is the one failure mode this lane keeps closing.
    The raise propagates out of the scanner wrapper into security.run, which
    logs one bounded WARNING naming the cause and falls back to the fixture
    verdict; that still blocks a diff carrying an AWS key. A genuinely
    malformed diff is then diagnosable instead of mysteriously scanning nothing.
    """
    root = Path(dest_dir).resolve()
    for relative, body in added_files(diff).items():
        path = (root / relative).resolve()
        # `is_relative_to` is also True for root itself, which an empty target
        # produces -- that is a directory, not a file to write.
        if path == root or not path.is_relative_to(root):
            raise ValueError(
                f"diff header target {relative!r} resolves to {path}, outside "
                f"the directory being scanned ({root}); refusing to write it"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")
