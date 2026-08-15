"""Semgrep wrapper — static analysis for code smells / insecure patterns.

OWNER: Habiba.

WHAT TO BUILD:
    1. Write the changed files to a temp dir.
    2. Run `semgrep --config auto --json <dir>`.
    3. Map each result to a Finding; map semgrep severity -> our Severity.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from ..state import DevResult, Finding


def _write_diff_to_temp(dev: DevResult, temp_dir: str) -> None:
    """Materialize the changed files from the unified diff."""

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
    """Map Semgrep severity to our Finding severity vocabulary."""

    mapping = {
        "INFO": "low",
        "WARNING": "medium",
        "ERROR": "high",
    }

    return mapping.get((severity or "").upper(), "low")


def scan(dev: DevResult) -> list[Finding]:
    """Run Semgrep and convert its JSON output to Finding objects."""

    with tempfile.TemporaryDirectory(prefix="agentorg-semgrep-") as temp_dir:
        _write_diff_to_temp(dev, temp_dir)

        report_path = Path(temp_dir) / "semgrep-report.json"

        result = subprocess.run(
            [
                "semgrep",
                "--config",
                "auto",
                "--json",
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

        # Semgrep may return non-zero when findings exist.
        # Treat that as a scanner result, not automatically as an error.
        if result.returncode not in (0, 1):
            raise RuntimeError(
                f"Semgrep failed with exit code {result.returncode}: "
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

    for result_item in data.get("results", []):
        extra = result_item.get("extra", {})

        findings.append(
            Finding(
                tool="semgrep",
                severity=_map_severity(extra.get("severity")),
                rule=result_item.get("check_id", "unknown"),
                file=result_item.get("path", "unknown"),
                line=int(
                    result_item.get("start", {}).get("line", 0) or 0
                ),
                description=(
                    extra.get("message")
                    or "Semgrep finding."
                ),
            )
        )

    return findings