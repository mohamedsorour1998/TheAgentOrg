"""The scan gate: real scanners, real fan-out, over both demo fixtures.

OWNER: Mariam (CI). The scanners themselves are Habiba's lane.

Run by the `scan` job in .github/workflows/ci.yml, and runnable by hand:

    gitleaks version && trivy --version && semgrep --version   # all three needed
    python scripts/scan_gate.py

WHY THIS IS A FILE AND NOT A HEREDOC IN THE WORKFLOW
    Because then the bytes CI runs are the bytes you can run on your laptop.
    A heredoc inside `run: |` cannot be executed locally without re-typing it,
    and YAML indentation silently rewrites Python. This file is the gate; the
    workflow only installs the binaries and calls it.

WHAT IT PROVES
    1. All three binaries actually executed. run_all_scanners -- the exact
       function agents/security.py calls -- fans out to (semgrep, gitleaks,
       trivy), and each wrapper shells out via subprocess.run. That call is
       SPIED ON here and every argv[0] recorded, so "the fan-out ran all three"
       is asserted rather than assumed.

       An earlier version of this docstring claimed that merely reaching the
       end of the script proved it, on the grounds that a missing binary raises
       FileNotFoundError. That was wrong in the direction that matters: a
       missing binary is caught, but a scanner DROPPED FROM THE FAN-OUT is not,
       because nothing then tries to execute it. Deleting `_trivy` from the
       tuple in agentorg/security/__init__.py left this gate green. Trivy is
       also the one scanner with no findings of its own to assert on, so it had
       no other cover. Nothing here is wrapped in try/except, for the separate
       reason that a missing scanner must take the job down loudly.
    2. The poisoned diff yields exactly the two critical gitleaks findings the
       demo blocks on, at the exact rules and lines, with the exact
       multiplicity -- compared as a Counter, not a set. A set silently
       collapses duplicates, so a wrapper that emitted every finding twice
       reported blocking=4 and still passed. Counter equality is
       order-insensitive (gitleaks' finding order is not stable across runs)
       but multiplicity-sensitive, which is the combination this needs.
    3. Semgrep produced at least one mapped finding, so a semgrep that runs but
       reports nothing usable cannot pass as healthy.
    4. The clean diff does NOT block. Without this negative control, a scanner
       that flagged everything would sail through checks 2 and 3.

WHAT IT DELIBERATELY DOES NOT DO
    It never substitutes an empty findings list for a broken scanner.
    compute_security_verdict([]) returns ("pass", []), so a swallowed scanner
    error would report the poisoned diff as clean while this gate stayed green
    -- the one failure that survives CI and takes the demo down.

Trivy contributes zero findings to both fixtures, measured: the wrappers
materialize only added lines from the diff, and neither fixture adds a
dependency manifest for trivy to find CVEs in. It is here because the security
agent calls it, so CI must exercise the same fan-out the agent does.
"""

import subprocess
import sys
from collections import Counter
from pathlib import Path

import agentorg
from agentorg.common import config
from agentorg.security import run_all_scanners
from agentorg.state import DevResult, Finding, compute_security_verdict

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every binary the fan-out is supposed to execute. Kept here rather than read
# back out of agentorg.security, because a gate that derives its expectation
# from the code under test agrees with that code by construction.
SCANNER_BINARIES = ("semgrep", "gitleaks", "trivy")

# Measured on gitleaks 8.21.2 with agentorg/security/gitleaks.toml. Two rules,
# two findings, one per poisoned line. If a scanner or rule change moves these,
# this gate goes red ON PURPOSE: gitleaks.toml says in its own comments that the
# demo asserts exactly two blocking findings, and a third overlapping rule has
# already broken that once. Update the pin deliberately, do not loosen it.
# Compared as a Counter -- see the docstring on why not a set.
EXPECTED_BLOCKING = (
    ("gitleaks", "aws-access-key-id", "app/auth.py", 3),
    ("gitleaks", "aws-secret-access-key", "app/auth.py", 4),
)


def _key(finding: Finding) -> tuple[str, str, str, int]:
    """The identity of a finding, for comparison against EXPECTED_BLOCKING."""
    return (finding.tool, finding.rule, finding.file, finding.line)


def _scan_recording_binaries(dev: DevResult, executed: set[str]) -> list[Finding]:
    """Run the real fan-out, recording every binary it actually executes.

    The wrappers call `subprocess.run` through the module object, so replacing
    the attribute is visible to all three without touching Habiba's code. The
    wrapper delegates unchanged, so this observes the run without altering it,
    and `finally` puts the real function back even if a scanner raises.
    """
    real_run = subprocess.run

    def spy(args, *rest, **kwargs):
        if isinstance(args, list | tuple) and args:
            executed.add(str(args[0]))
        return real_run(args, *rest, **kwargs)

    subprocess.run = spy
    try:
        return run_all_scanners(dev)
    finally:
        subprocess.run = real_run


def _load(name: str) -> DevResult:
    return DevResult.model_validate_json(
        (REPO_ROOT / "fixtures" / name).read_text(encoding="utf-8")
    )


def _report(label: str, findings: list[Finding]) -> None:
    """Print every finding, so a red run says what the scanners actually saw."""
    print(f"--- {label}: {len(findings)} finding(s)")
    for finding in findings:
        print(
            f"      {finding.tool:9} {finding.severity:8} {finding.rule} "
            f"{finding.file}:{finding.line}"
        )


def main() -> int:
    # `python scripts/scan_gate.py` puts scripts/ on sys.path, NOT the repo
    # root, so `import agentorg` resolves through the installed distribution.
    # In CI that is `pip install -e .` on this very checkout and the two agree.
    # Elsewhere they can silently disagree -- an editable install pointing at a
    # different clone makes this gate scan a tree nobody is reviewing and then
    # report a confident SCAN OK about the wrong code. That is not theoretical:
    # it is exactly what the mutation harness written to falsify this file hit,
    # where three mutations "passed" because the gate never read them.
    imported = Path(agentorg.__file__).resolve().parent.parent
    if imported != REPO_ROOT:
        print(
            f"refusing to run: this gate lives in {REPO_ROOT} but `import "
            f"agentorg` resolved to {imported}. It would scan a different tree "
            f"than the fixtures it is checking. Reinstall with "
            f"`pip install -e .` from {REPO_ROOT}, or set PYTHONPATH to it."
        )
        return 1

    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        """Record a failure instead of raising, so one run reports all of them."""
        if not condition:
            failures.append(message)

    # --- the poisoned diff must block -------------------------------------
    executed: set[str] = set()
    poisoned = _scan_recording_binaries(_load("dev_result_poisoned.json"), executed)
    _report("poisoned", poisoned)
    print(f"      binaries executed: {sorted(executed)}")

    verdict, blocking = compute_security_verdict(
        poisoned, threshold=config.SECURITY_BLOCK_THRESHOLD
    )
    print(f"      verdict={verdict} blocking={len(blocking)}")

    # This is the check that makes the trivy install worth its ~108 MB: the
    # scan job pays for fidelity to the agent's real invocation, so the gate
    # has to confirm it got what was paid for. gitleaks is covered by
    # EXPECTED_BLOCKING and semgrep by the check below; without this one, trivy
    # is covered by nothing at all.
    missing = [name for name in SCANNER_BINARIES if name not in executed]
    require(
        not missing,
        f"the fan-out never executed {missing}. Every scanner in "
        f"agentorg/security/__init__.py must actually shell out; a scanner "
        f"dropped from the tuple raises nothing and would otherwise pass. "
        f"Executed: {sorted(executed)}",
    )
    require(
        verdict == "block",
        f"poisoned diff must block at threshold "
        f"{config.SECURITY_BLOCK_THRESHOLD!r}, got {verdict!r}",
    )
    actual_blocking = Counter(_key(f) for f in blocking)
    require(
        actual_blocking == Counter(EXPECTED_BLOCKING),
        f"poisoned blocking findings changed.\n"
        f"      expected: {sorted(EXPECTED_BLOCKING)}\n"
        f"      actual:   {sorted(actual_blocking.elements())}",
    )
    require(
        any(f.tool == "semgrep" for f in poisoned),
        "semgrep ran but mapped no findings on the poisoned diff; its wrapper "
        "or rules file is broken even though the binary exists",
    )

    # --- the clean diff must not block ------------------------------------
    clean = run_all_scanners(_load("dev_result_clean.json"))
    _report("clean", clean)

    clean_verdict, clean_blocking = compute_security_verdict(
        clean, threshold=config.SECURITY_BLOCK_THRESHOLD
    )
    print(f"      verdict={clean_verdict} blocking={len(clean_blocking)}")

    require(
        clean_verdict == "pass",
        f"clean diff must pass, got {clean_verdict!r} on "
        f"{sorted(_key(f) for f in clean_blocking)}",
    )

    if failures:
        print("\nSCAN GATE FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nSCAN OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
