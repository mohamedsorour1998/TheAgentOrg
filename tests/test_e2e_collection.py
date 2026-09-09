"""The four Selenium tests are COLLECTED, and reverting that fails by name.

WHY THIS FILE EXISTS. Lane G wrote `target_repo/tests/e2e/test_login_browser.py` with
a skip that is loud in both directions, and `pyproject.toml`'s `testpaths = ["tests"]`
meant pytest never saw it. Correct machinery, unreachable -- CLAUDE.md's second named
pattern (*a feature complete, tested, and reached by nothing*) arriving in the test
suite's own configuration, which is the one place none of the four gates looks.

Widening `testpaths` fixes it and **nothing would notice it being reverted**. The
collected total would drop 1974 -> 1969 and every test would still pass, which is
exactly Lane L's twelfth instance: a RED step that deletes a test rather than failing
one, where the count still reads healthy. So the widening needs a test that fails BY
NAME, and that is what this file is.

THE `grep -ci selenium` METRIC IN CLAUDE.md ITEM 5 CANNOT EXPRESS THE FIXED CASE, and
that is worth more than the fix. It reads:

    pytest --collect-only | grep -ci selenium    ->  0

Measured 2026-09-09, before AND after the widening: **0 both times**. Collection emits
node ids, and the string "selenium" appears in no node id here -- the file is
`test_login_browser.py` and every test name is about a browser or a login. So the
number offered as proof the tests are missing is a number that reads 0 when they are
present. This repository's fifteen-times pattern, arriving in the *evidence for an open
item* rather than in a test. The honest metric is the one below: the four node ids by
name.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

E2E_TESTPATH = "target_repo/tests/e2e"

# The four browser tests plus the reporter, as LITERALS. Deliberately not derived from
# the file under test: Lane H's H7 suite parametrised over the set it was checking, and
# dropping a member took it from `32 passed` to `31 passed` with nothing failing. A
# literal is the only form a mutation cannot follow.
BROWSER_TESTS = (
    "test_the_login_form_renders_in_a_real_browser",
    "test_valid_credentials_signed_in_through_the_browser",
    "test_invalid_credentials_are_refused_through_the_browser",
    "test_an_empty_submission_is_refused_through_the_browser",
)
SKIP_REPORTER = "test_the_skip_is_visible_and_not_silent"


def _ini_options() -> dict:
    """`pyproject.toml`'s pytest table, PARSED -- never grepped.

    A substring check would be satisfied by this repository's own commentary: the
    `[tool.pytest.ini_options]` block explains the widening at length and names
    `target_repo/tests/e2e` several times in prose. CLAUDE.md records that failure
    twice (a test satisfied by the comment explaining the thing it checks), and once
    where the prose was the author's own in the same commit. tomllib reads the value.
    """
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    return data["tool"]["pytest"]["ini_options"]


def test_testpaths_carries_the_browser_directory_as_a_parsed_value():
    """The widening itself, read as TOML data rather than as text."""
    options = _ini_options()
    testpaths = options.get("testpaths")

    assert testpaths, "testpaths is absent or empty; this test would pin nothing"
    assert "tests" in testpaths, (
        f"the main suite's directory left testpaths: {testpaths!r}. This test is about "
        f"ADDING the browser directory, not about replacing the suite with it."
    )
    assert E2E_TESTPATH in testpaths, (
        f"{E2E_TESTPATH!r} is not in testpaths ({testpaths!r}), so Lane G's four "
        f"Selenium tests are not collected by `pytest` from the repository root. The "
        f"skip they were written to make loud cannot fire in a run nobody points at "
        f"them."
    )


def test_the_four_browser_tests_are_ACTUALLY_collected_by_a_bare_run():
    """Drive real collection with NO path argument, so `testpaths` is what decides.

    Passing the directory explicitly would pass whatever `testpaths` said, which is the
    whole thing under test. The subprocess therefore takes no positional argument.

    `PYTHONPATH=REPO_ROOT` is the `cf5cb83` fix: `sys.path[0]` for a subprocess is the
    script's directory, and the editable install's finder then resolves `agentorg` to
    the SHARED checkout rather than to this worktree.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT), "HOME": str(Path.home())},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    out = proc.stdout

    # Anti-vacuity, and it is not decoration: a collection error prints a short output
    # in which every `assert name in out` below fails for the WRONG reason, and an
    # empty run would satisfy a test written only as `not in`.
    assert proc.returncode == 0, f"collection itself failed:\n{proc.stdout}\n{proc.stderr}"
    assert out.count("::") > 1000, (
        f"only {out.count('::')} node ids collected; the suite did not run, so this "
        f"test would pin nothing"
    )

    for name in (*BROWSER_TESTS, SKIP_REPORTER):
        assert f"{E2E_TESTPATH}/test_login_browser.py::{name}" in out, (
            f"{name} was not collected. `pytest` from the repository root does not "
            f"reach Lane G's browser tests, whatever `pyproject.toml` says."
        )
