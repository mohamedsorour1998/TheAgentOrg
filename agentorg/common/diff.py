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

_HEADER = "+++ b/"


def added_files(diff: str | None) -> dict[str, str]:
    """Rebuild the files `diff` proposes: `{path: added lines, joined}`.

    Only `+` lines count, and only after a `+++ b/<path>` header has said which
    file they belong to -- added lines before the first header belong to no file
    and are dropped, which is the second way "somewhere in the diff text" and
    "in this change" disagree. A `+++ b//dev/null` target names a deleted file
    and materialises nothing. Paths repeated across headers keep the last body.
    """
    files: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []

    for line in (diff or "").splitlines():
        if line.startswith(_HEADER):
            if current is not None:
                files[current] = "\n".join(body)
            relative = line[len(_HEADER) :].strip()
            current = None if relative == "/dev/null" else relative
            body = []
            continue

        # `+++` is the header marker, never content -- see the docstring.
        if line.startswith("+") and not line.startswith("+++"):
            body.append(line[1:])

    if current is not None:
        files[current] = "\n".join(body)
    return files


def write_added_files(diff: str | None, dest_dir: str) -> None:
    """Materialize `added_files(diff)` under `dest_dir`, ready to be scanned.

    The trailing newline matters: a scanner reads a file, not a Python string,
    and gitleaks reports the line NUMBER it found a secret on. scripts/scan_gate
    pins those numbers (app/auth.py:3 and :4 on the poisoned fixture), so this
    has to stay one file line per added diff line, in order, with the file
    ending in a newline like any other text file.

    Every target must land INSIDE `dest_dir`, and one that does not raises.
    The path in a `+++ b/` header is written by the model, and `Path(dest_dir)
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
