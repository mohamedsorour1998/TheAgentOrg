"""Gitleaks wrapper — finds committed secrets.

OWNER: Habiba.

Runs the real gitleaks CLI and converts its JSON report into Finding objects.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..state import DevResult, Finding

CONFIG_PATH = (
    Path(__file__).resolve().parent / "gitleaks.toml"
)


def _repo_relative(path: str, temp_dir: str) -> str:
    """Strip the scratch directory so findings name the repo path.

    Scanners run against a temp copy of the diff, so their reports carry paths
    like /var/folders/../agentorg-gitleaks-ab12/app/auth.py. That string ends up
    in the PR comment and on screen during the demo; `app/auth.py` is what a
    reviewer needs to see.
    """
    try:
        return os.path.relpath(path, temp_dir)
    except ValueError:
        return path


def _write_diff_to_temp(dev: DevResult, temp_dir: str) -> None:
    """Materialize changed files from the unified diff."""

    current_file: Path | None = None
    content: list[str] = []

    for line in (dev.diff or "").splitlines():
        if line.startswith("+++ b/"):
            if current_file is not None:
                current_file.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
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
        current_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        current_file.write_text(
            "\n".join(content) + "\n",
            encoding="utf-8",
        )


def scan(dev: DevResult) -> list[Finding]:
    """Run real gitleaks and map its JSON report to Finding objects."""

    with tempfile.TemporaryDirectory(
        prefix="agentorg-gitleaks-"
    ) as temp_dir:

        _write_diff_to_temp(dev, temp_dir)

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
                "--config",
                str(CONFIG_PATH),
                "--no-git",
                "--no-banner",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        # Gitleaks returns:
        # 0 = no leaks
        # 1 = leaks found
        if result.returncode not in (0, 1):
            raise RuntimeError(
                "Gitleaks failed with exit code "
                f"{result.returncode}: "
                f"{result.stderr.strip()}"
            )

        # A scanner that cannot report must fail loudly. Returning [] here
        # would mean "no secrets found", and compute_security_verdict([])
        # returns PASS -- a poisoned change would sail through while every
        # test stayed green. Raise; the security agent catches it and falls
        # back to the fixture verdict.
        if not report_path.exists():
            raise RuntimeError(
                f"Gitleaks wrote no report to {report_path}. "
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
                f"Gitleaks report at {report_path} is not valid JSON: {exc}"
            ) from exc

    findings: list[Finding] = []

    for leak in data or []:
        rule_id = leak.get(
            "RuleID",
            "unknown",
        )

        findings.append(
            Finding(
                tool="gitleaks",
                severity="critical",
                rule=rule_id,
                file=_repo_relative(
                    leak.get("File", "unknown"),
                    temp_dir,
                ),
                line=int(
                    leak.get(
                        "StartLine",
                        0,
                    ) or 0
                ),
                description=(
                    leak.get("Description")
                    or "Secret detected by Gitleaks."
                ),
            )
        )

    return findings