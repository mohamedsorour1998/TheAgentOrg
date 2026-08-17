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
    1. run_all_scanners -- the exact function agents/security.py calls -- runs
       to completion against three real CLIs. It fans out in the order
       (semgrep, gitleaks, trivy) and each wrapper shells out via
       subprocess.run, which raises FileNotFoundError when its binary is
       absent. So reaching the end of this script at all proves all three
       binaries ran. That is why nothing here is wrapped in try/except: a
       missing scanner must take the job down, loudly.
    2. The poisoned diff yields exactly the two critical gitleaks findings the
       demo blocks on, at the exact rules and lines.
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

import sys
from pathlib import Path

import agentorg
from agentorg.common import config
from agentorg.security import run_all_scanners
from agentorg.state import DevResult, Finding, compute_security_verdict

REPO_ROOT = Path(__file__).resolve().parent.parent

# Measured on gitleaks 8.21.2 with agentorg/security/gitleaks.toml. Two rules,
# two findings, one per poisoned line. If a scanner or rule change moves these,
# this gate goes red ON PURPOSE: gitleaks.toml says in its own comments that the
# demo asserts exactly two blocking findings, and a third overlapping rule has
# already broken that once. Update the pin deliberately, do not loosen it.
EXPECTED_BLOCKING = {
    ("gitleaks", "aws-access-key-id", "app/auth.py", 3),
    ("gitleaks", "aws-secret-access-key", "app/auth.py", 4),
}


def _key(finding: Finding) -> tuple[str, str, str, int]:
    """The identity of a finding, for comparison against EXPECTED_BLOCKING."""
    return (finding.tool, finding.rule, finding.file, finding.line)


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
    poisoned = run_all_scanners(_load("dev_result_poisoned.json"))
    _report("poisoned", poisoned)

    verdict, blocking = compute_security_verdict(
        poisoned, threshold=config.SECURITY_BLOCK_THRESHOLD
    )
    print(f"      verdict={verdict} blocking={len(blocking)}")

    require(
        verdict == "block",
        f"poisoned diff must block at threshold "
        f"{config.SECURITY_BLOCK_THRESHOLD!r}, got {verdict!r}",
    )
    require(
        {_key(f) for f in blocking} == EXPECTED_BLOCKING,
        f"poisoned blocking findings changed.\n"
        f"      expected: {sorted(EXPECTED_BLOCKING)}\n"
        f"      actual:   {sorted(_key(f) for f in blocking)}",
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
