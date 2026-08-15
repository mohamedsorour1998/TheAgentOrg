"""Trivy wrapper — scans dependencies / filesystem for known vulnerabilities.

OWNER: Habiba.

Runs Trivy over the materialized changed files and maps vulnerabilities
to the shared Finding contract.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from ..state import DevResult, Finding


def _write_diff_to_temp(dev: DevResult, temp_dir: str) -> None:
    """Materialize changed files from the unified diff."""

    current_file: Path | None = None
    content: list[str] = []

    for line in (dev.diff or "").splitlines():
        if line.startswith("+++ b/"):
            if current_file is not None:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                current_file.write_text(
                    "\n".join(content) + "\n",
                    encoding="utf-8",
                )

            relative = line[6:].strip()
            current_file = Path(temp_dir) / relative
            content = []
            continue

        if line.startswith("+") and not line.startswith("+++"):
            content.append(line[1:])

    if current_file is not None:
        current_file.parent.mkdir(parents=True, exist_ok=True)
        current_file.write_text(
            "\n".join(content) + "\n",
            encoding="utf-8",
        )


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

        if not report_path.exists():
            return []

        try:
            data = json.loads(
                report_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            return []

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