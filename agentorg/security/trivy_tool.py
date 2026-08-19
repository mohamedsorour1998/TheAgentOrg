"""Trivy wrapper — scans dependencies / filesystem for known vulnerabilities.

OWNER: Habiba.

Runs Trivy over the materialized changed files and maps vulnerabilities
to the shared Finding contract.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from ..common.diff import write_added_files
from ..state import DevResult, Finding


def _write_diff_to_temp(dev: DevResult, temp_dir: str) -> None:
    """Materialize changed files from the unified diff.

    One materialiser, in agentorg/common/diff.py, shared with the other two
    wrappers and with the developer's poisoned safety net -- see the note in
    gitleaks_tool. Added lines only, exactly as before.
    """

    write_added_files(dev.diff, temp_dir)


def _map_severity(severity: str | None) -> str:
    """Map Trivy severity to our Finding severity vocabulary."""

    mapping = {
        "UNKNOWN": "low",
        "LOW": "low",
        "MEDIUM": "medium",
        "HIGH": "high",
        "CRITICAL": "critical",
    }

    return mapping.get((severity or "").upper(), "low")


def scan(dev: DevResult) -> list[Finding]:
    """Run Trivy and convert vulnerabilities to Finding objects."""

    with tempfile.TemporaryDirectory(prefix="agentorg-trivy-") as temp_dir:
        _write_diff_to_temp(dev, temp_dir)

        report_path = Path(temp_dir) / "trivy-report.json"

        result = subprocess.run(
            [
                "trivy",
                "fs",
                "--format",
                "json",
                "--output",
                str(report_path),
                temp_dir,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        if result.returncode not in (0, 1):
            raise RuntimeError(
                f"Trivy failed with exit code {result.returncode}: "
                f"{result.stderr.strip()}"
            )

        # Fail loudly rather than reporting "no findings": an empty list is
        # indistinguishable from a clean scan, and compute_security_verdict([])
        # returns PASS.
        if not report_path.exists():
            raise RuntimeError(
                f"Trivy wrote no report to {report_path}. "
                f"stderr: {result.stderr.strip()}"
            )

        try:
            data = json.loads(
                report_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Trivy report at {report_path} is not valid JSON: {exc}"
            ) from exc

    findings: list[Finding] = []

    for target in data.get("Results", []) or []:
        vulnerabilities = target.get("Vulnerabilities", []) or []

        for vulnerability in vulnerabilities:
            findings.append(
                Finding(
                    tool="trivy",
                    severity=_map_severity(
                        vulnerability.get("Severity")
                    ),
                    rule=vulnerability.get(
                        "VulnerabilityID",
                        "unknown",
                    ),
                    file=target.get(
                        "Target",
                        "unknown",
                    ),
                    line=0,
                    description=(
                        vulnerability.get("Title")
                        or vulnerability.get("Description")
                        or "Trivy vulnerability finding."
                    ),
                )
            )

    return findings