"""Every legal `+++` spelling must materialise the file, or the scan is empty.

MEASURED 2026-08-22 against `_HEADER = "+++ b/"`:

    b/ prefix    files=['app/auth.py'] key=True
    no prefix    files=[]              key=False
    a/ both      files=[]              key=False
    old/new      files=[]              key=False
    /dev/null    files=['app/auth.py'] key=True
    garbage ->   {}

Zero files means the scanners run over an empty tree, return [], and
compute_security_verdict([]) returns ("pass", []) -- while scan_provenance
truthfully records `scanners`, because they did run. They had nothing to read.
The diff is MODEL-WRITTEN, so `git diff --no-prefix` output is a plausible thing
to receive rather than a hypothetical.

The poisoned half of the demo survives this only by accident:
`developer._key_is_in_the_change` uses the same parser, so a no-prefix poisoned
diff reads as "key absent" and the safety net substitutes the reference diff.
**The clean half has no safety net**, so a clean run reports `pass` from an empty
scan -- indistinguishable from a real pass in every field the PR carries.
"""

import pytest

from agentorg.common.diff import added_files

_KEY = "AKIAIOSFODNN7EXAMPLE"


def _diff(minus: str, plus: str) -> str:
    return (
        f"--- {minus}\n"
        f"+++ {plus}\n"
        "@@ -1,2 +1,3 @@\n"
        " from flask import request\n"
        f'+SECRET = "{_KEY}"\n'
    )


@pytest.mark.parametrize(
    ("minus", "plus", "label"),
    [
        ("a/app/auth.py", "b/app/auth.py", "git default"),
        ("app/auth.py", "app/auth.py", "--no-prefix"),
        ("a/app/auth.py", "a/app/auth.py", "a/ on both sides"),
        ("old/app/auth.py", "new/app/auth.py", "old/ new/ prefixes"),
        ("/dev/null", "b/app/auth.py", "a new file"),
        ("i/app/auth.py", "w/app/auth.py", "git diff.mnemonicPrefix"),
    ],
)
def test_every_legal_header_spelling_materialises_the_file(minus, plus, label):
    """The parametrisation IS the test: one spelling passing proves nothing."""
    files = added_files(_diff(minus, plus))
    assert files, (
        f"the {label} spelling (+++ {plus}) materialised NO files. The scanners "
        f"would then run over an empty tree, find nothing, and the verdict would "
        f"be `pass` with scan_provenance truthfully reading `scanners`."
    )
    assert any(_KEY in body for body in files.values()), (
        f"the {label} spelling materialised {list(files)} but the added "
        f"credential is not in the body, so a scanner would not see it"
    )


@pytest.mark.parametrize(
    ("minus", "plus"),
    [
        ("a/app/auth.py", "b/app/auth.py"),
        ("app/auth.py", "app/auth.py"),
        ("a/app/auth.py", "a/app/auth.py"),
        ("old/app/auth.py", "new/app/auth.py"),
        ("i/app/auth.py", "w/app/auth.py"),
        ("/dev/null", "b/app/auth.py"),
    ],
)
def test_the_filename_is_the_same_whatever_the_prefix(minus, plus):
    """A finding must read `app/auth.py`, not `b/app/auth.py` or `auth.py`.

    The demo's central claim quotes a file and a line number. A prefix leaking
    into the path changes what a judge reads on the pull request -- and so does
    over-stripping: `git apply -p1` would turn the `--no-prefix` spelling into
    `auth.py`, silently dropping a real directory level from every finding.
    Both directions are wrong here, so both are pinned.
    """
    assert list(added_files(_diff(minus, plus))) == ["app/auth.py"], (
        f"+++ {plus} produced {list(added_files(_diff(minus, plus)))}, "
        f"expected exactly ['app/auth.py']"
    )


def test_a_path_whose_first_component_is_not_a_prefix_keeps_every_component():
    """The whitelist's converse, and the reason it is a whitelist.

    `app/` is not a diff prefix, it is a directory. A rule that stripped one
    leading component unconditionally -- which is what `git apply -p1` does, and
    what the obvious regex does -- would report `auth.py` for a `--no-prefix`
    diff. The file would still be scanned, so no test looking at the verdict
    could catch it; only the path a judge reads would be wrong.
    """
    files = added_files(_diff("src/pkg/mod.py", "src/pkg/mod.py"))
    assert list(files) == ["src/pkg/mod.py"], (
        f"a non-prefix first component was stripped: got {list(files)}, which "
        f"loses a directory level from every finding this parser reports"
    )


def test_a_diff_that_yields_no_files_is_refused_not_scanned_empty():
    """The guard that makes the whole class of failure loud instead of silent.

    A non-empty diff with no recognised `+++` header is not a clean change -- it
    is a diff this parser did not understand. Returning {} sends an empty tree
    to the scanners and the run reports `pass`. Raising makes the security stage
    fail, which is a red job rather than a false green.
    """
    with pytest.raises(ValueError, match="no files"):
        added_files("this is not a diff at all, but it is not empty either\n")


def test_an_empty_diff_is_still_an_empty_dict():
    """The complement, so the guard above cannot be over-eager.

    An empty or None diff genuinely proposes nothing -- that is not a parse
    failure, and it must not raise, because `added_files(None)` is a real call.
    """
    assert added_files("") == {}
    assert added_files(None) == {}
    assert added_files("   \n\n  \n") == {}


def test_a_delete_only_diff_is_understood_rather_than_refused():
    """`+++ /dev/null` parsed fine and proposed nothing. That is not a failure.

    The refusal must key on "no `+++` header was recognised", not on "the result
    is empty". A change that only DELETES a file legitimately materialises zero
    files, and raising there would turn a correct parse into a red security
    stage -- the false alarm that gets a guard deleted.
    """
    delete_only = (
        "--- a/app/legacy.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-import os\n"
        "-KEY = os.environ['K']\n"
    )
    assert added_files(delete_only) == {}
