"""Which scanner-provenance mode is this test running in? Owner: Aya.

THE PROBLEM THIS SOLVES, AND IT IS THE MOST CONFUSING THING IN THIS REPOSITORY.
The three scanner binaries are not on the default PATH. Without them every
wrapper raises FileNotFoundError, agents/security.py catches it, and the verdict
comes from fixtures/security_result_block.json -- `compute_security_verdict` is
never called. With them, the fan-out runs for real and the rule decides.

BOTH MODES PRODUCE THE SAME HEADLINE: status="blocked", verdict="block",
len(blocking) == 2, rules {aws-access-key-id, aws-secret-access-key}, severity
"critical". So "the poisoned ticket blocked 10 out of 10" is a claim about the
BLOCK RULE in one mode and a claim about JSON DESERIALISATION in the other, and
nothing in the suite used to say which.

THE DISCRIMINATOR IS THE LINE NUMBER, and it is the only field that differs.
MEASURED:

    fixture (security_result_block.json)   access key line 4, secret line 5
    real gitleaks 8.21.2 (scan_gate.py)    access key line 3, secret line 4

Both report tool="gitleaks", the same two rule names, the same file
"app/auth.py", and severity "critical". The line numbers differ because the
fixture was written against a slightly different rendering of the poisoned diff
than the one common/diff.py materialises. That divergence is load-bearing here
and must not be "fixed" by aligning the fixture: aligning them would remove the
only signal that tells a reader which path answered.

NOTE THAT THE TWO SETS OVERLAP AT LINE 4. No single-line observation can tell
the modes apart -- only the whole set can. That is why the checks below compare
sets and never individual findings.

FRAGILITY, STATED HONESTLY: this discriminator breaks if the fixture is
regenerated to lines 3/4, or if gitleaks' reported lines move. Both are
possible. So `answered_from_fixture` cross-checks against
`binaries_installed()` and raises rather than guessing when the two disagree --
a wrong answer here would silently relabel every metric in the DORA table.

IMPORT PATH CONSTRAINT: `tests/` has no `__init__.py`; `pyproject.toml` sets
`pythonpath = ["."]`, which makes `import tests.provenance` work under pytest
and under `python -m` from the repository root, but NOT from any other cwd.
"""

import pathlib
import shutil

from agentorg.state import RunState

# Mirrors run_all_scanners' fan-out order in agentorg/security/__init__.py.
# semgrep is FIRST, which matters: the knob-off ABSENT path signals absence by
# raising, and one raise ends the loop -- see _run.py's accepted-limit section.
SCANNER_TOOLS = ("semgrep", "gitleaks", "trivy")

# fixtures/security_result_block.json, measured.
FIXTURE_LINES = frozenset({4, 5})

# scripts/scan_gate.py EXPECTED_BLOCKING, measured on gitleaks 8.21.2.
REAL_SCANNER_LINES = frozenset({3, 4})

_AWS_RULES = frozenset({"aws-access-key-id", "aws-secret-access-key"})


def binaries_installed() -> list[str]:
    """Which of the three scanners `shutil.which` can find, in fan-out order."""
    return [tool for tool in SCANNER_TOOLS if shutil.which(tool) is not None]


def describe_mode() -> str:
    """One line naming the ambient mode, for a test failure message or a report."""
    installed = binaries_installed()
    if not installed:
        return "FIXTURE-FALLBACK mode: no scanner binaries on PATH"
    if len(installed) == len(SCANNER_TOOLS):
        return "REAL-SCANNER mode: all three binaries on PATH"
    return (
        f"HALF-PROVISIONED: only {installed} on PATH -- the absent one raises and "
        f"ends the fan-out unless SCANNERS_REQUIRED is set"
    )


def _aws_lines(state: RunState) -> frozenset[int]:
    """Line numbers of the two AWS-credential findings, or an empty set."""
    if state.security is None:
        return frozenset()
    return frozenset(
        f.line for f in state.security.blocking if f.rule in _AWS_RULES
    )


def answered_from_fixture(state: RunState) -> bool:
    """Did the FIXTURE produce this verdict, rather than the real scanners?

    Raises RuntimeError when the line numbers and the installed binaries
    disagree, instead of returning a plausible guess. A silent wrong answer here
    would mislabel the provenance column of every DORA row, and a mislabelled
    metric is worse than a missing one -- it reads as evidence.
    """
    lines = _aws_lines(state)
    installed = binaries_installed()

    if not lines:
        # No AWS findings at all: either a clean run or a fault-reported run.
        # Provenance is not answerable from the findings, so fall back to PATH.
        return not installed

    if lines == FIXTURE_LINES:
        if len(installed) == len(SCANNER_TOOLS):
            raise RuntimeError(
                f"findings carry the FIXTURE line numbers {sorted(lines)} but all "
                f"three binaries are installed. Either the real scanners now "
                f"report these lines, or the fan-out silently fell back. Do not "
                f"guess: re-measure scripts/scan_gate.py's EXPECTED_BLOCKING and "
                f"update REAL_SCANNER_LINES."
            )
        return True

    if lines == REAL_SCANNER_LINES:
        if not installed:
            raise RuntimeError(
                f"findings carry the REAL-SCANNER line numbers {sorted(lines)} but "
                f"no binaries are on PATH. The fixture has probably been "
                f"regenerated onto lines 3/4, which destroys this discriminator. "
                f"Re-measure FIXTURE_LINES."
            )
        return False

    raise RuntimeError(
        f"AWS findings on lines {sorted(lines)}, which match neither the fixture "
        f"{sorted(FIXTURE_LINES)} nor the real scanners "
        f"{sorted(REAL_SCANNER_LINES)}. Provenance is unknown; re-measure both "
        f"before trusting any metric built on this run."
    )


def answered_from_real_scanners(state: RunState) -> bool:
    """The complement of answered_from_fixture, with the same raising behaviour."""
    return not answered_from_fixture(state)


class Provenance:
    """Puts a test into a chosen provenance mode. Yielded by the `provenance` fixture.

    WHY PATH IS PREPENDED AND NEVER REPLACED -- MEASURED, AND IT IS A REAL TRAP.
    tests/test_scanner_resilience.py's own `_fake_scanner` helper REPLACES
    os.environ["PATH"] with its fake directory, which is correct for its
    inside-out tests: they call gitleaks_tool.scan directly and never touch git.

    A black-box test cannot do that. github_ops.open_pr runs real `git init` /
    `checkout -B` / `add` / `commit` in the offline path that conftest.py forces
    on every test. With PATH replaced, `git` is unresolvable and run_pipeline
    dies with:

        FileNotFoundError: [Errno 2] No such file or directory: 'git'
        at agentorg/github_ops.py:114, in _ensure_offline_repo

    -- before the security stage is ever reached, so a test written that way
    fails for a reason that has nothing to do with scanners. So this class
    PREPENDS its directory and leaves the rest of PATH intact.
    """

    def __init__(self, bin_dir: pathlib.Path, monkeypatch) -> None:
        self._bin = bin_dir
        self._monkeypatch = monkeypatch
        self._bin.mkdir(parents=True, exist_ok=True)

    def _activate(self) -> None:
        """Prepend the fake directory, keeping the real PATH (and git) behind it."""
        self._monkeypatch.setenv("PATH", str(self._bin), prepend=":")

    def fake_scanner(self, tool: str, script: str) -> pathlib.Path:
        """Create an executable fake for one tool and put it first on PATH."""
        if tool not in SCANNER_TOOLS:
            raise ValueError(f"{tool!r} is not one of {SCANNER_TOOLS}")
        path = self._bin / tool
        path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
        path.chmod(0o755)
        self._activate()
        return path

    def none_installed(self) -> None:
        """ABSENT for all three: CI's mode, and every laptop on this team.

        Prepends an EMPTY directory and also asserts the real ones are not
        reachable, because a machine that happens to have them installed would
        otherwise run a different test than the one that was written.
        """
        self._activate()
        found = binaries_installed()
        if found:
            raise RuntimeError(
                f"none_installed() cannot make {found} disappear: they are on "
                f"PATH behind the fake directory. Prepending cannot hide a real "
                f"binary. Run this test in a shell without them, or use "
                f"all_broken(), which shadows them instead."
            )

    def all_broken(self) -> None:
        """FAULT for all three: present, and exiting non-zero with stderr."""
        for tool in SCANNER_TOOLS:
            self.fake_scanner(tool, 'echo "internal error" >&2\nexit 2')

    def some_absent_others_broken(self, absent: str = "semgrep") -> None:
        """The HALF-PROVISIONED LAPTOP that _run.py's accepted limit describes.

        One scanner absent, the others present and broken. Defaults to semgrep
        because it runs FIRST in the fan-out, which is what makes its raise abort
        the loop before the other two faults are reported.
        """
        if absent not in SCANNER_TOOLS:
            raise ValueError(f"{absent!r} is not one of {SCANNER_TOOLS}")
        for tool in SCANNER_TOOLS:
            if tool != absent:
                self.fake_scanner(tool, 'echo "internal error" >&2\nexit 2')
        self._activate()
        if (self._bin / absent).exists():
            raise RuntimeError(f"{absent} must be the absent one, but a fake exists")

    @staticmethod
    def answered_from_fixture(state: RunState) -> bool:
        return answered_from_fixture(state)
