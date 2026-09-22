"""THE GENERATED TESTS NOW RUN AGAINST THE CHANGE. These are the guards on that.

`testgen.run(state)` was called with `workdir=None` on both pipeline paths, which
means "generate but do not execute" -- so every run wrote tests nothing ran and
`binding` was structurally always False. `agents/testbed.py` is the checkout that
was missing.

**THE DANGEROUS DIRECTION IS A FALSE BLOCK, NOT A MISSED ONE.** `testgen._counts`
reports one failure when a run exits non-zero and its summary cannot be parsed --
correct, because the exit code is the fact and inferring `0 failed` from silence is
this repository's signature defect. But it means a bed where pytest CANNOT RUN
produces `failed=1`, and `binding = failed > 0` would then block a correct change
because the environment was inadequate. `testgen`'s own G5 note names where that
ends: "a feature with that reputation gets switched off."

So most of this file is about the cases that must NOT produce a verdict.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from agentorg.agents import testbed
from agentorg.state import DevResult, RunState

SUBJECT = pathlib.Path(__file__).resolve().parents[1] / "target_repo" / "app" / "auth.py"


def _state(**applied: str) -> RunState:
    return RunState(
        ticket_id="T-1",
        ticket_text="add a per-IP rate limit",
        dev=DevResult(
            branch="agent-org/T-1",
            diff="--- a/app/auth.py\n+++ b/app/auth.py\n",
            summary="s",
            files_changed=list(applied) or ["app/auth.py"],
            applied=dict(applied),
        ),
    )


@pytest.fixture()
def root():
    with tempfile.TemporaryDirectory() as tmp:
        yield pathlib.Path(tmp)


class TestWhenThereIsNothingToRunAgainst:
    """Every one of these must answer with a REASON, never with a verdict."""

    def test_no_developer_stage(self, root):
        bed, why = testbed.prepare(RunState(ticket_id="T", ticket_text="t"), root)
        assert bed is None
        assert "developer stage has not run" in why

    def test_a_diff_with_no_applied_files(self, root):
        """THE POISONED PATH, and the common case on any older run.

        `developer.run`'s safety net clears `applied` when it substitutes the
        reference diff, because the two views of one change would otherwise
        disagree -- the scanners reading a poisoned diff while the generated tests
        ran clean code.
        """
        state = RunState(
            ticket_id="T", ticket_text="t",
            dev=DevResult(branch="b", diff="d", summary="s", files_changed=["app/auth.py"]),
        )
        bed, why = testbed.prepare(state, root)
        assert bed is None
        assert "no complete file contents" in why

    def test_the_reason_is_never_empty(self, root):
        """An absence with no reason is the thing this module exists to avoid.

        `NOT EXECUTED` alone cannot distinguish an older run from a broken one from
        a poisoned run whose files were cleared on purpose -- three different facts
        rendered identically.
        """
        for state in (
            RunState(ticket_id="T", ticket_text="t"),
            RunState(ticket_id="T", ticket_text="t",
                     dev=DevResult(branch="b", diff="d", summary="s", files_changed=[])),
        ):
            bed, why = testbed.prepare(state, root)
            assert bed is None
            assert why.strip(), "a refusal with no reason"


class TestTheSafetyProperty:
    def test_code_that_does_not_import_is_REFUSED_not_reported_as_a_failure(self, root):
        """**THE ONE THAT KEEPS THIS FEATURE ALIVE.**

        A model can return a complete file that does not parse. Without the smoke
        check the bed is built, pytest exits non-zero, `_counts` reports one
        failure and `binding` blocks a change whose only crime was that the
        generator produced broken source.

        An environment that can run nothing is ABSENT -- report it. A test that
        runs and fails is a FACT. The scanners draw the same line.
        """
        bed, why = testbed.prepare(_state(**{"app/auth.py": "def broken(:\n"}), root)
        assert bed is None, "a bed was handed over for code that cannot be collected"
        assert "could not be collected" in why

    def test_a_path_that_escapes_the_bed_is_refused(self, root):
        """Model output is untrusted input and this writes to disk."""
        bed, why = testbed.prepare(_state(**{"../../pwned.py": "x = 1"}), root)
        assert bed is None
        assert "safely" in why

    def test_an_escaping_path_writes_nothing_outside_the_bed(self, root):
        testbed.prepare(_state(**{"../../pwned.py": "x = 1"}), root)
        assert not (root.parent / "pwned.py").exists()
        assert not (root / "pwned.py").exists()


class TestWhenThereIsSomethingToRunAgainst:
    def test_a_complete_file_produces_a_usable_bed(self, root):
        bed, why = testbed.prepare(_state(**{"app/auth.py": SUBJECT.read_text()}), root)
        assert bed is not None, why
        assert (bed / "app" / "auth.py").exists()
        assert "1 changed file" in why

    def test_the_developer_s_content_is_what_lands_on_disk(self, root):
        """Written VERBATIM. A bed holding anything else is testing the wrong code."""
        marker = SUBJECT.read_text() + "\n# written by the testbed\n"
        bed, _ = testbed.prepare(_state(**{"app/auth.py": marker}), root)
        assert bed is not None
        assert (bed / "app" / "auth.py").read_text() == marker

    def test_the_fixtures_a_generated_test_may_use_are_present(self, root):
        """`client`, `live_server` and `browser` -- the browser half of note 7.

        Asserted by NAME rather than by counting: a conftest that defined two of
        the three would leave every generated browser test erroring on a missing
        fixture, which `binding` would read as a failing assertion about the change.
        """
        bed, _ = testbed.prepare(_state(**{"app/auth.py": SUBJECT.read_text()}), root)
        assert bed is not None
        conftest = (bed / "conftest.py").read_text()
        for fixture in ("def client(", "def live_server(", "def browser("):
            assert fixture in conftest, f"{fixture} missing from the generated conftest"

    def test_a_missing_browser_SKIPS_rather_than_failing(self, root):
        """A headless-Chrome absence must not read as an assertion about the change.

        Those are different facts and `binding` acts on one of them. Asserted over
        the conftest's source because running it needs a browser, which is the
        condition under test.
        """
        bed, _ = testbed.prepare(_state(**{"app/auth.py": SUBJECT.read_text()}), root)
        assert bed is not None
        conftest = (bed / "conftest.py").read_text()
        assert "pytest.skip" in conftest
        assert "chromedriver" in conftest


def test_the_subject_app_is_where_this_module_thinks_it_is():
    """ANTI-VACUITY for the whole file.

    Every test above would pass trivially -- always refusing, always reporting a
    reason -- if `target_repo/` were absent, because `prepare` returns early. This
    is the check that the happy path is reachable at all.
    """
    assert testbed.SUBJECT_APP.is_dir(), (
        f"{testbed.SUBJECT_APP} is missing; every test in this file would pass "
        f"against the early return and pin nothing"
    )
    assert SUBJECT.is_file()
