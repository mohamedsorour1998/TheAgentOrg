"""A copy of the subject app with the agent's change written into it, so the
generated tests have something real to run against.

WHY THIS EXISTS
===============
`testgen.run(state)` was called with `workdir=None` on BOTH pipeline paths, and
`None` means "generate but do not execute". Every run produced test files that
were never run and `binding` was structurally always False, so the honest answer
to "does the pipeline run the tests it writes?" was no.

That was the right call when it was made -- the comment at both call sites says
"the pipeline has no checkout to run against here" -- and reporting NOT EXECUTED
beats reporting `passed=0 failed=0`, which is the tuple a green zero-test run
produces. This module is the checkout those comments were waiting for.

IT WRITES FILES. IT DOES NOT APPLY A DIFF, AND THAT IS A MEASUREMENT
====================================================================
The first version of this module applied `dev.diff` with `git apply`. Measured
2026-09-22 against both fixtures and a real clean diff from ticket 61:

    dev_result_clean                 error: corrupt patch at line 24
    dev_result_poisoned              error: patch failed: app/auth.py:1
    ticket 61, model-written         error: corrupt patch at line 28

A model's unified diff does not reliably apply: the hunks are malformed and the
context lines do not match the file. Applying with fuzz would be worse -- it lets
a diff that does NOT describe this code apply anyway, and a test running against
code the agent did not write is worse than no test.

So `DevResult.applied` carries the complete file content and this writes it
verbatim. `agents/developer.py` asks for it and keeps it consistent with the diff.

THE SEPARATION OF AUTHORITY IS PRESERVED, AND SHARPENED
=======================================================
`testgen` still never sees the change -- `tests/test_testgen_authority.py` fails by
name if `state.dev` or `diff=` reaches its prompt. The change is not an INPUT to
the generator; it is what the generated test is RUN AGAINST:

    the generator  reads the acceptance criteria   ->  writes the test
    the testbed    writes the developer's files    ->  the test runs against them

A test written from the change passes whether or not the change is correct. A test
written from the criteria and run against the change can fail, which is the only
reason to run one.

EVERY REFUSAL IS NAMED, NEVER SILENT
====================================
`prepare` returns `(None, reason)` rather than just `None`. A bed that could not be
built and a bed that was not attempted are different facts, and `testgen` puts the
reason into notes that are rendered onto the pull request.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from ..state import RunState

# The subject app, resolved from the REPO ROOT the way `fixtures_loader` resolves
# `fixtures/` -- `pip install .` does not ship `target_repo/`, and a path relative
# to this module would point inside site-packages there.
REPO_ROOT = Path(__file__).resolve().parents[2]
SUBJECT_APP = REPO_ROOT / "target_repo"

# What a generated test may reach for. Written into the bed so a generated file
# asks for a fixture rather than knowing how the harness is assembled.
#
# `live_server` AND `browser` ARE SEPARATE ON PURPOSE. A criterion about an HTTP
# status needs a server and no browser; only a criterion about what a person sees
# needs both. Bundling them would start Chrome for every test and make a headless
# browser failure look like a failing assertion about the change.
_CONFTEST = '''"""Fixtures a generated test may use. Written by agentorg/agents/testbed.py."""

import shutil as _shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests" / "e2e"))


@pytest.fixture()
def client():
    """Flask's own test client against the CHANGED app. No browser, no socket."""
    from app.auth import create_app

    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture()
def live_server():
    """A real HTTP server on a real port, serving the CHANGED app.

    `app.test_client()` is an in-process WSGI shim a browser cannot reach, which
    is why a browser test needs this and an API test does not.
    """
    from app_web import LiveServer

    with LiveServer() as server:
        yield server


@pytest.fixture()
def browser():
    """Headless Chrome, or a SKIP naming what is absent.

    A missing browser must not read as a failing assertion about the change --
    those are different facts, and `binding` acts on one of them.
    """
    if not _shutil.which("chromedriver"):
        pytest.skip("no chromedriver on PATH; this generated browser test did not run")

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    for flag in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
                 "--disable-gpu", "--window-size=1280,900"):
        options.add_argument(flag)
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    try:
        yield driver
    finally:
        driver.quit()
'''


def _safe_target(path: str, bed: Path) -> Path | None:
    """The absolute destination for `path`, or None when it escapes the bed.

    MODEL OUTPUT IS UNTRUSTED INPUT and this writes to disk. `../../etc/x` and an
    absolute path are both refused, the same rule `testgen._safe_paths` applies to
    the files it writes -- one spelling of the check would be better, but that one
    is about test paths under `tests/` and this one is about arbitrary source.
    """
    candidate = (bed / path).resolve()
    try:
        candidate.relative_to(bed.resolve())
    except ValueError:
        return None
    return candidate


def prepare(state: RunState, root: Path) -> tuple[Path | None, str]:
    """A directory holding the subject app with `state.dev.applied` written in.

    Returns `(path, "")` on success and `(None, reason)` otherwise, where the
    reason is a sentence for a human -- it reaches `GeneratedTests.notes`, which
    is rendered onto the pull request.

    NOTHING HERE RAISES. This runs inside the `develop` stage beside the one
    binding verdict in the pipeline; a bed that cannot be built must degrade to
    "the tests were not executed" and never to a failed run. Same ruling
    `run_index.record_run` carries, for the same reason.
    """
    dev = state.dev
    if dev is None:
        return None, "the developer stage has not run, so there was nothing to test"
    if not dev.applied:
        # THE COMMON CASE ON AN OLDER RUN, and on every poisoned run: the safety
        # net clears `applied` when it substitutes the reference diff, because the
        # two views of the change would otherwise disagree.
        return None, (
            "the developer returned no complete file contents, so the generated "
            "tests were not executed"
        )
    if not SUBJECT_APP.is_dir():
        return None, f"the subject app is not on disk at {SUBJECT_APP.name}/"

    bed = root / "testbed"
    try:
        shutil.copytree(
            SUBJECT_APP, bed,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
        )
        (bed / "conftest.py").write_text(_CONFTEST, encoding="utf-8")

        written, refused = [], []
        for path, content in dev.applied.items():
            target = _safe_target(path, bed)
            if target is None:
                refused.append(path)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(path)

        if not written:
            return None, "no changed file could be written safely into the testbed"

        # ── THE SMOKE CHECK, AND IT IS WHAT MAKES THIS SAFE TO RUN AT ALL ──────
        #
        # `testgen._counts` reports ONE failure when a run exits non-zero and its
        # summary cannot be parsed -- correct, because the exit code is the fact
        # and inferring `0 failed` from silence is this repository's signature
        # defect. But it means a bed where pytest CANNOT RUN (no pytest, no flask,
        # a syntax error in the model's file) produces `failed=1`, and
        # `binding = failed > 0` then BLOCKS A CORRECT CHANGE because the
        # environment was inadequate.
        #
        # That is the false-alarm direction `testgen`'s own G5 note warns about --
        # "a feature with that reputation gets switched off". So the bed must
        # prove it can COLLECT the app's own existing tests before it is allowed
        # to judge a generated one. Collection is cheap and already proves the
        # three things that matter: pytest exists, `flask` imports, and the
        # model's `app/auth.py` is importable Python.
        #
        # It is the ABSENT-versus-BROKEN split the scanners already draw: an
        # environment that can run nothing is ABSENT, so report it and do not
        # block; a test that runs and fails is a FACT.
        smoke = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--collect-only", "tests/test_auth.py"],
            cwd=str(bed), capture_output=True, text=True, check=False,
        )
        if smoke.returncode != 0:
            tail = (smoke.stdout or smoke.stderr or "").strip().splitlines()
            why = tail[-1] if tail else "pytest gave no reason"
            return None, (
                "the generated tests were NOT executed: the changed code could not "
                f"be collected by pytest ({why})"
            )

        note = f"ran against {len(written)} changed file(s)"
        if refused:
            # NAMED. A path that escaped the bed is a fact about the model's output
            # and belongs in the record, not in a log nobody reads.
            note += f"; refused {len(refused)} unsafe path(s)"
        return bed, note
    except Exception:
        # BROAD ON PURPOSE, logger fetched INLINE -- ruff's BLE001 is satisfied only
        # by a logging call it can statically resolve, carrying the traceback,
        # inside the handler. Narrowing the except satisfies the rule with NO
        # logging at all, which is the worse option.
        logging.getLogger(__name__).warning(
            "could not prepare a testbed for run %s", state.run_id, exc_info=True
        )
        return None, "the testbed could not be prepared; the generated tests were not run"
