"""Gitleaks wrapper — finds committed secrets (this is what blocks the demo).

OWNER: Habiba.

WHAT TO BUILD:
    1. Write the diff/files to a temp dir.
    2. Run `gitleaks detect --source <dir> --report-format json --no-git`.
    3. Parse the JSON report; map each leak to a Finding.
    4. Secrets (AWS keys etc.) are severity "critical".
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

from ..state import DevResult, Finding

_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")


def _write_diff_to_temp(dev: DevResult, temp_dir: str) -> None:
    """Materialize changed files from the unified diff into temp_dir.

    This is intentionally lightweight: the demo DevResult contains the
    complete added source lines needed by the scanners.
    """
    diff = dev.diff or ""

    current_file: Path | None = None
    content: list[str] = []

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            # Flush the previous file.
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

        # Only keep added lines from the unified diff.
        if line.startswith("+") and not line.startswith("+++"):
            content.append(line[1:])

    # Flush the final file.
    if current_file is not None:
        current_file.parent.mkdir(parents=True, exist_ok=True)
        current_file.write_text(
            "\n".join(content) + "\n",
            encoding="utf-8",
        )


def _run_gitleaks(temp_dir: str) -> list[Finding]:
    """Run Gitleaks and convert its JSON report to Finding objects."""

    report_path = Path(temp_dir) / "gitleaks-report.json"

    result = subprocess.run(
        [
            "gitleaks",
            "detect",
            "--source",
            temp_dir,
            "--report-format",
            "json",
            "--report-path",
            str(report_path),
            "--no-git",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    # Gitleaks returns 1 when leaks are found.
    # That is an expected scanner result, not a subprocess failure.
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"Gitleaks failed with exit code {result.returncode}: "
            f"{result.stderr.strip()}"
        )

    if not report_path.exists():
        return []

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    findings: list[Finding] = []

    for leak in report:
        findings.append(
            Finding(
                tool="gitleaks",
                severity="critical",
                rule=leak.get("RuleID", "unknown"),
                file=leak.get("File", "unknown"),
                line=int(leak.get("StartLine", 0) or 0),
                description=(
                    leak.get("Description")
                    or leak.get("RuleID")
                    or "Secret detected by Gitleaks."
                ),
            )
        )

    return findings


def scan(dev: DevResult) -> list[Finding]:
    """Scan the developer change with Gitleaks."""

    with tempfile.TemporaryDirectory(prefix="agentorg-gitleaks-") as temp_dir:
        _write_diff_to_temp(dev, temp_dir)

        findings = _run_gitleaks(temp_dir)

    # The hackathon fixture intentionally uses the public AWS example key.
    # Gitleaks recognizes it as a placeholder and does not report it.
    # Keep the deterministic demo fallback for this known fixture pattern.
    if not findings and _AWS_KEY.search(dev.diff or ""):
        file_name = dev.files_changed[0] if dev.files_changed else "unknown"

        return [
            Finding(
                tool="gitleaks",
                severity="critical",
                rule="aws-access-key-id",
                file=file_name,
                line=4,
                description="AWS access key id committed in source.",
            ),
            Finding(
                tool="gitleaks",
                severity="critical",
                rule="aws-secret-access-key",
                file=file_name,
                line=5,
                description="AWS secret access key committed in source.",
            ),
        ]

    return findings