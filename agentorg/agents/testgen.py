"""Test-generation agent — writes tests from the PLAN, never from the diff.

OWNER: Lane G. Spec §9, judge requirement 7.

SEPARATION OF AUTHORITY IS THE WHOLE POINT OF THIS FILE, and it is one layer out
from the argument the rest of the repository rests on. `compute_security_verdict`
is five lines of Python with no model in it, because a model that can be
persuaded must not be the thing standing between a credential and `main`. The
same reasoning applied to tests: **if the agent that wrote the change also writes
the test that clears it, the test is a restatement of the change's own
assumptions.** It will pass. It proves nothing.

So this agent is given `state.plan.acceptance_criteria` and the repository as it
stands, and is NOT given `state.dev`. That is not a preference expressed in a
prompt — `_prompt` never touches `state.dev`, and `repo_snapshot.render` is
called WITHOUT `diff=`, which is the switch between the before-view and the
after-view. `tests/test_testgen_authority.py` asserts both over the AST, because
a comment saying "we do not read the diff" would satisfy a grep while a call to
`state.dev.diff` sat beside it — this repository has been bitten by exactly that
twice (CLAUDE.md, "a test satisfied by the comment explaining the thing it was
checking").

The reviewer, by contrast, DOES pass `diff=` and should: its job is to judge the
change. This agent's job is to be an independent second opinion, and an
independent opinion that has read the answer is not independent.

WHY IT READS THE REPOSITORY AT ALL — G1, and a measured incident. CLAUDE.md,
"THE DEVELOPER WAS WRITING GO FOR A FLASK APP": the clean run failed twice at the
revision cap while the scanners reported PASS, because `developer._prompt` names
target FILES but never their contents and `target_repo/` is excluded from the
image. The agent guessed the language and every revision inherited the guess. An
agent asked to test a file it cannot see invents imports, which is the same
defect with a different symptom.

WHAT THIS AGENT IS NOT ALLOWED TO SAY. `GeneratedTests` carries `passed`,
`failed` and `binding`, and none of the three is the model's to set — they are
MEASURED by running the tests. So the model is asked for `TestPlan`, which
cannot express them. That is the `SREAdvice` lesson applied before it can bite:
`sre.run` used to validate the model's reply against `SREResult`, whose required
`verdict` its own prompt forbade the model to fill, so every obedient reply was
rejected and the fixture was served silently with all 16 tests green. A narrow
model the reply cannot overfill is stronger than dropping fields afterwards, and
a model reporting its own `passed` count is fabricated evidence rather than a
schema mismatch.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from .. import repo_snapshot
from ..common import llm
from ..state import GeneratedTests, RunState

# `GeneratedTests.source` admits three values. This agent may produce exactly two
# of them: a real generation, or a fixture stand-in. "diff" is in the contract's
# vocabulary and is the one value this file must never write -- it would mean the
# generator read the change it is meant to be independent of. Named here so the
# refusal is a constant a test can assert against rather than an absence.
SOURCE_ACCEPTANCE = "acceptance_criteria"
SOURCE_FIXTURE = "fixture"
SOURCE_FORBIDDEN = "diff"

# One retry, deliberately, and the reasoning is in `_execute`. More retries buy a
# green run at the cost of the signal; zero retries make an infrastructure hiccup
# indistinguishable from a real failure.
FLAKE_RETRIES = 1

SYSTEM_PROMPT = """You are the Test Generator in a CI/CD pipeline. You are given a
ticket's ACCEPTANCE CRITERIA and the target repository as it stands right now.

You are deliberately NOT given the change under review. Another agent wrote that
change; if you read it you would test what it does instead of what was asked for,
and the test would pass whether or not the change is correct. Write the test the
acceptance criteria demand, against the interface the repository already exposes.

THE TARGET IS A PYTHON 3.12 FLASK APPLICATION tested with pytest. The app factory
is `create_app()` and tests drive it through `app.test_client()`.

Respond with ONE JSON object and nothing else. Shape:
{
  "files": [{"path": "tests/test_<name>.py", "content": "<the full file>"}],
  "notes": "<what you covered, and what you could not>"
}

RULES:
  * Every path must be relative and under `tests/`. No absolute paths, no `..`.
  * One assertion per acceptance criterion, named after the criterion.
  * If a criterion is not checkable from the outside -- "no credentials are
    committed" is not something a pytest can see -- say so in `notes` and write
    no test for it. A test that cannot fail is worse than no test.
  * Do NOT report how many tests pass. That is measured by running them."""


class GeneratedFile(BaseModel):
    """One test file the model proposes. Contents, not a diff."""

    path: str
    content: str


class TestPlan(BaseModel):
    """WHAT THE MODEL IS ASKED FOR, and it is narrower than `GeneratedTests`.

    Deliberately cannot express `passed`, `failed` or `binding`. See the module
    docstring: those are measurements, and a model that can report its own pass
    count can report a green run it never had.
    """

    files: list[GeneratedFile] = Field(default_factory=list)
    notes: str = ""


def _prompt(state: RunState) -> str:
    """The generator's inputs: the criteria, and the repository BEFORE the change.

    READ THE TWO OMISSIONS HERE AS THE FEATURE. There is no `state.dev` and no
    `diff=` on the render call, and `tests/test_testgen_authority.py` fails by
    name if either appears.

    `render(paths)` with no `diff=` is the before-view -- the same call the
    developer makes, for a different reason. The developer omits the diff because
    it is the one writing it; this agent omits it because seeing it would make the
    generated test a restatement of the change.
    """
    criteria = state.plan.acceptance_criteria if state.plan else []
    targets = state.plan.target_files if state.plan else []

    parts = [
        "TICKET:\n" + (state.ticket_text or "(no ticket text)"),
        "ACCEPTANCE CRITERIA:\n- " + "\n- ".join(criteria or ["(none given)"]),
    ]
    context = repo_snapshot.render(targets)
    if context:
        parts.append(context)
    return "\n\n".join(parts)


def _safe_paths(files: list[GeneratedFile], workdir: Path) -> list[GeneratedFile]:
    """Drop any file whose path escapes `workdir`. Model output is untrusted input.

    A model that answers `{"path": "../../agentorg/state.py"}` would otherwise
    overwrite the frozen contract from inside a pipeline stage, and the run would
    look successful. `Path.resolve` then `is_relative_to` is the check; string
    prefix matching is not, because `workdir/../workdir-evil` starts with the same
    characters.

    Refusals are logged rather than raised: one bad path in a reply should cost
    that file, not the whole generation. The count reaches `notes`, so a dropped
    file is reported and not silently absent -- same requirement as a quarantined
    test.
    """
    root = workdir.resolve()
    kept: list[GeneratedFile] = []
    for item in files:
        candidate = (root / item.path).resolve()
        if candidate.is_relative_to(root) and item.path.strip():
            kept.append(item)
        else:
            logging.getLogger(__name__).warning(
                "testgen refused a generated path that escapes the work directory: %r",
                item.path,
            )
    return kept


def _write(files: list[GeneratedFile], workdir: Path) -> list[str]:
    """Materialise the accepted files and return their relative paths."""
    written: list[str] = []
    for item in files:
        target = (workdir / item.path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.content)
        written.append(item.path)
    return written


def _pytest_runner(workdir: Path, paths: list[str]) -> subprocess.CompletedProcess:
    """THE EXECUTION SEAM the suite substitutes. Real pytest, in a subprocess.

    `python -m pytest`, never the bare console script: measured in this repository
    against `target_repo/`, `python -m` prepends cwd to `sys.path` and the bare
    form dies with `ModuleNotFoundError: No module named 'app'` during collection.
    The two are not interchangeable and must not be harmonised.

    A separate function rather than an inline call because the suite must be able
    to express a failing run, a flaky run and a crashing run. A double that can
    only express success is this repository's most-repeated defect shape.
    """
    import sys

    # Fixed argv, no shell, and every path went through `_safe_paths` first.
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *paths],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        check=False,
    )


# ── G6: the flake policy ──────────────────────────────────────────────────────
#
# THE FAILURE MODE HERE IS SOCIAL, NOT TECHNICAL. A flaky blocking test gets
# disabled by the first person it inconveniences, and after that the gate is
# theatre: it reports green because nothing is left to run. That is this
# repository's signature defect -- a check that cannot distinguish "did not run"
# from "passed" -- arriving through a human decision rather than a code path.
#
# So quarantine is REPRESENTED, not implied. The two states are kept apart for
# exactly the reason `scan_provenance` keeps `fixture-fallback` (a fault) apart
# from `fixture-stub` (a choice):
#
#   QUARANTINE_FLAKY   a test passed on retry after failing -- a FAULT in the test
#   QUARANTINE_CHOSEN  a human named it in QUARANTINED -- a CHOICE
#
# Collapsing them would hide a genuinely broken generated test behind a decision
# somebody made last month. And neither may be silent: `_quarantine_note` puts
# every quarantined name into `GeneratedTests.notes`, which is rendered, so the
# absence of a test is a line a human reads rather than a gap they must notice.
QUARANTINE_FLAKY = "flaky"
QUARANTINE_CHOSEN = "chosen"

# Tests a human has deliberately taken out of the binding set. EMPTY TODAY, and
# that is the honest state -- nothing has earned a place here yet. It exists as a
# named, greppable list rather than as a `-k 'not ...'` string buried in a
# workflow, because a quarantine nobody can enumerate cannot be reported.
QUARANTINED: dict[str, str] = {}


def _quarantine_note(flaky: list[str]) -> str:
    """Every quarantined test named, or an explicit statement that none is.

    "no tests are quarantined" is written out rather than left as an empty string,
    because an absent line and a line saying nothing was excluded are different
    facts and only one of them is checkable. A reader who sees nothing cannot tell
    whether the policy ran.
    """
    rows: list[str] = []
    for name in sorted(QUARANTINED):
        rows.append(f"QUARANTINED ({QUARANTINE_CHOSEN}): {name} -- {QUARANTINED[name]}")
    for name in sorted(set(flaky)):
        rows.append(
            f"QUARANTINED ({QUARANTINE_FLAKY}): {name} -- failed then passed on "
            f"retry, so its result is not binding this run"
        )
    if not rows:
        return "no tests are quarantined; every generated test counted toward the verdict"
    return "\n".join(rows)


def _execute(
    workdir: Path,
    paths: list[str],
    runner=_pytest_runner,
) -> tuple[int, int, list[str]]:
    """Run the generated tests. Returns (passed, failed, flaky-quarantined).

    THE RETRY IS WHAT MAKES THE FLAKE POLICY REAL, and it is exactly one. A first
    run that fails is re-run once; if the second run passes, the failure was not
    reproducible and the tests are recorded as flaky-quarantined rather than as a
    block. If it fails twice, it is a fact.

    ONE retry and not three, deliberately. Each additional retry raises the chance
    of turning a genuine intermittent failure green, and an intermittent failure in
    a security-adjacent pipeline is information. One retry distinguishes "the
    environment hiccuped" from "this does not work" and stops there.

    A flaky result is NOT counted as passed either. Both would be dishonest: as a
    failure it blocks on evidence that did not reproduce, and as a pass it claims a
    check succeeded when the run disagreed with itself. It is reported as its own
    third thing, and the caller's `binding` stays False.
    """
    first = runner(workdir, paths)
    if first.returncode == 0:
        return _counts(first.stdout, failed=False)

    second = runner(workdir, paths)
    if second.returncode == 0:
        # Disagreed with itself. Quarantine by FILE, because a subprocess pytest
        # summary does not reliably name each test and inventing node ids from
        # stdout would put a name in an audit artifact that nothing verified.
        passed, _failed, _ = _counts(second.stdout, failed=False)
        return passed, 0, list(paths)

    return _counts(second.stdout, failed=True)


def _counts(stdout: str, failed: bool) -> tuple[int, int, list[str]]:
    """Parse pytest's summary line. Unparseable output is NOT read as zero.

    An unreadable summary means the count is unknown, and `0 failed` is the one
    answer that must never be inferred from silence -- it is the shape of every
    silent-pass defect in this repository (`compute_security_verdict([])` passes,
    which is why a scanner failure must never become an empty list).

    So when the run FAILED and the summary cannot be read, this reports one
    failure: the run said so with its exit code, and the exit code is the fact.
    """
    passed = _first_int_before(stdout, "passed")
    parsed_failed = _first_int_before(stdout, "failed")
    if not failed:
        return passed, 0, []
    return passed, parsed_failed or 1, []


def _first_int_before(stdout: str, word: str) -> int:
    """`N passed` / `N failed` from pytest's terminal summary, else 0."""
    for line in reversed(stdout.splitlines()):
        tokens = line.replace(",", " ").split()
        for index, token in enumerate(tokens):
            if token.startswith(word) and index and tokens[index - 1].isdigit():
                return int(tokens[index - 1])
    return 0


# ── G5 + G7: what a result is allowed to mean ─────────────────────────────────
#
# THESE ARE ONE IDEA AND `GeneratedTests` ALREADY ENCODES IT. A generated test that
# FAILS is a fact: something ran, and it disagreed with the acceptance criteria. A
# generated test that PASSES proves the generator produced something that executes
# -- nothing about correctness. A generated test that is MISSING is advisory,
# because the reason may be the generator's, not the change's.
#
# So `binding` is true only when `failed > 0`. It is not `not passed`, and it is
# not `failed or missing`, and both wrong spellings read as reasonable:
#
#   binding = failed > 0        <- correct: a failure is a fact
#   binding = not passed        <- would block a run where NOTHING was generated
#   binding = failed >= 0       <- always true; blocks every run including green
#
# The middle one is the dangerous one, because it looks stricter and is therefore
# easy to defend in review. It makes the generator's own failure to produce
# anything into a block on the change, which punishes the developer for the test
# agent's bad day and gives the whole feature a reputation for false alarms -- and
# a feature with that reputation gets switched off, which is G6's failure mode
# arriving through G5.
GREEN_PROVES = (
    "a passing generated test proves the generator produced something that RAN. It "
    "is not evidence the change is correct -- these tests were written from the "
    "ticket by a model, not derived from a specification. Only the FAILING case is "
    "a fact."
)


def _binding(failed: int) -> bool:
    """True only when a failure was OBSERVED. See the block comment above.

    A separate function purely so a test can drive it across the boundary without
    running pytest, and so the one comparison this decision rests on exists in
    exactly one place.
    """
    return failed > 0


def run(
    state: RunState,
    workdir: Path | None = None,
    runner=_pytest_runner,
) -> GeneratedTests:
    """Generate tests from the acceptance criteria, run them, and report honestly.

    `workdir` is where generated files land and where pytest runs. `None` means
    "generate but do not execute" -- a real state, not a degenerate one: the plan
    stage has criteria long before there is a checkout to run against, and a
    generation with nothing executed must not report `passed=0, failed=0` as
    though a green run had happened. It reports it in `notes` instead.

    NO try/except AROUND `llm.structured`. It already absorbs unavailable, raised,
    chatty and unparseable and returns None, which is the one signal this function
    acts on. Wrapping it again would swallow caller bugs and serve fixture data
    while the run looked live -- the rule every other agent in this package follows.

    There is no fixture file for this agent, so the fallback is an EXPLICITLY EMPTY
    result carrying `source="fixture"`, not a fabricated green one. `GeneratedTests`
    has no fixture in `fixtures/`, and inventing one would be worse than this: a
    plausible sample result is indistinguishable from a real generation, which is
    the exact confusion `scan_provenance` exists to prevent.
    """
    plan = llm.structured(TestPlan, SYSTEM_PROMPT, _prompt(state))
    if plan is None:
        llm.record_fixture_fallback()
        return GeneratedTests(
            files=[],
            passed=0,
            failed=0,
            binding=False,
            source=SOURCE_FIXTURE,
            notes=(
                "NO TESTS WERE GENERATED -- no model answered, and this agent has no "
                "fixture to stand in. A missing generated test is ADVISORY: it says "
                "nothing about the change. " + _quarantine_note([])
            ),
        )

    if workdir is None:
        return GeneratedTests(
            files=[f.path for f in plan.files],
            passed=0,
            failed=0,
            binding=False,
            source=SOURCE_ACCEPTANCE,
            notes=(
                f"{len(plan.files)} test file(s) generated from the acceptance "
                f"criteria and NOT EXECUTED (no work directory was given), so the "
                f"counts below are not measurements. {plan.notes} "
                + _quarantine_note([])
            ),
        )

    accepted = _safe_paths(plan.files, workdir)
    refused = len(plan.files) - len(accepted)
    written = _write(accepted, workdir)
    if not written:
        return GeneratedTests(
            files=[],
            passed=0,
            failed=0,
            binding=False,
            source=SOURCE_ACCEPTANCE,
            notes=(
                f"the model answered but produced no usable test file "
                f"({refused} refused for an unsafe path). A missing generated test "
                f"is ADVISORY. {plan.notes} " + _quarantine_note([])
            ),
        )

    passed, failed, flaky = _execute(workdir, written, runner=runner)
    notes = [
        (
            f"generated from the ticket's acceptance criteria; {passed} passed, "
            f"{failed} failed."
        )
    ]
    if refused:
        notes.append(f"{refused} generated file(s) refused for an unsafe path.")
    if failed:
        notes.append(
            "A FAILING generated test is BINDING: something ran and disagreed with "
            "the acceptance criteria."
        )
    else:
        notes.append(GREEN_PROVES)
    notes.append(_quarantine_note(flaky))
    if plan.notes:
        notes.append(f"generator notes: {plan.notes}")

    return GeneratedTests(
        files=written,
        passed=passed,
        failed=failed,
        binding=_binding(failed),
        source=SOURCE_ACCEPTANCE,
        notes=" ".join(notes),
    )
