"""Semgrep wrapper — static analysis for code smells / insecure patterns.

OWNER: Habiba.

WHAT TO BUILD:
    1. Write the changed files to a temp dir.
    2. Run semgrep with our rules and JSON output.
    3. Map each result to a Finding.
    4. Map Semgrep severity -> our Severity.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from ..state import DevResult, Finding


def _write_diff_to_temp(dev: DevResult, temp_dir: str) -> None:
    """Materialize added/changed files from the unified diff."""

    current_file: Path | None = None
    content: list[str] = []

    for line in (dev.diff or "").splitlines():
        # Start of a new file in the diff
        if line.startswith("+++ b/"):
            # Save previous file
            if current_file is not None:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                current_file.write_text(
                    "\n".join(content) + "\n",
                    encoding="utf-8",
                )

            relative = line[len("+++ b/") :].strip()

            # Ignore deleted files
            if relative == "/dev/null":
                current_file = None
                content = []
                continue

            current_file = Path(temp_dir) / relative
            content = []
            continue

        # Only keep added lines.
        # Ignore diff metadata lines such as +++.
        if line.startswith("+") and not line.startswith("+++"):
            content.append(line[1:])

    # Save last file
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
    """Run Semgrep and convert its JSON results into Finding objects."""

    with tempfile.TemporaryDirectory(
        prefix="agentorg-semgrep-"
    ) as temp_dir:

        _write_diff_to_temp(dev, temp_dir)

        report_path = Path(temp_dir) / "semgrep-report.json"

        rules_path = Path(__file__).with_name("semgrep_rules.yml")

        if not rules_path.exists():
            raise FileNotFoundError(
                f"Semgrep rules file not found: {rules_path}"
            )

        result = subprocess.run(
            [
                "semgrep",
                "--config",
                str(rules_path),
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

        # Semgrep can return non-zero when findings exist.
        # Exit codes other than 0/1 indicate an actual execution failure.
        if result.returncode not in (0, 1):
            raise RuntimeError(
                "Semgrep failed with exit code "
                f"{result.returncode}: "
                f"{result.stderr.strip()}"
            )

        # Fail loudly rather than reporting "no findings": an empty list is
        # indistinguishable from a clean scan, and compute_security_verdict([])
        # returns PASS.
        if not report_path.exists():
            raise RuntimeError(
                f"Semgrep wrote no report to {report_path}. "
                f"stderr: {result.stderr.strip()}"
            )

        try:
            data = json.loads(
                report_path.read_text(
                    encoding="utf-8"
                )
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Semgrep produced invalid JSON output."
            ) from exc

    findings: list[Finding] = []

    for result_item in data.get("results", []):
        extra = result_item.get("extra", {})

        start = result_item.get("start", {})

        findings.append(
            Finding(
                tool="semgrep",
                severity=_map_severity(
                    extra.get("severity")
                ),
                rule=result_item.get(
                    "check_id",
                    "unknown",
                ),
                file=result_item.get(
                    "path",
                    "unknown",
                ),
                line=int(
                    start.get("line", 0) or 0
                ),
                description=(
                    extra.get("message")
                    or "Semgrep finding."
                ),
            )
        )

    return findings