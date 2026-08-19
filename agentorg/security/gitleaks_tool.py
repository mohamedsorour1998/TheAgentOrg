"""Gitleaks wrapper — finds committed secrets.

OWNER: Habiba.

Runs the real gitleaks CLI and converts its JSON report into Finding objects.

EVERY FAILURE PATH HERE IS FAIL-CLOSED, AND ONE IS NOT
    `compute_security_verdict([])` returns `("pass", [])`, so this wrapper may
    never answer a broken scanner with an empty list -- that reports a poisoned
    change as clean while the suite stays green. So each failure below returns
    `[error_finding("gitleaks", ...)]`, which is `high`, which is the block
    threshold.

    The one exception is a binary that is merely ABSENT, which per the plan's
    central ruling is a development and CI affordance rather than a fault: that
    path RAISES, agents/security.py catches it and falls back to the fixture
    verdict, and the poisoned diff still blocks on its two AWS-key findings.
    `SCANNERS_REQUIRED=true` promotes absent to fault. That whole decision lives
    in `_run.unrunnable_findings`, not here -- three copies of one
    security-relevant fork is how `common/diff.py` got its four drifting
    materialisers.
"""

import json
import os
import tempfile
from pathlib import Path

from ..common import config
from ..common.diff import write_added_files
from ..state import DevResult, Finding
from ._run import error_finding, run_scanner, unrunnable_findings

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
    """Materialize changed files from the unified diff.

    The parsing lives in agentorg/common/diff.py rather than here, because the
    developer's poisoned safety net has to ask the same question this answers
    -- "what does this change contain?" -- and when the two were written out
    separately they drifted apart and the poisoned demo stopped blocking. This
    wrapper stays as the name the scanner calls; only added lines are ever
    written, exactly as before.
    """

    write_added_files(dev.diff, temp_dir)


def scan(dev: DevResult) -> list[Finding]:
    """Run real gitleaks and map its JSON report to Finding objects."""

    with tempfile.TemporaryDirectory(
        prefix="agentorg-gitleaks-"
    ) as temp_dir:

        _write_diff_to_temp(dev, temp_dir)

        report_path = Path(temp_dir) / "gitleaks-report.json"

        result, kind = run_scanner(
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
            timeout=config.SCANNER_TIMEOUT_SECONDS,
        )

        # The command never launched. `kind` already carries the absent-vs-fault
        # verdict, computed from BOTH the exception type and the filesystem --
        # see _run.classify_failure for why either signal alone gets two of the
        # five fault modes wrong, in the fail-open direction.
        if result is None:
            return unrunnable_findings(
                "gitleaks",
                kind,
                f"the gitleaks command could not be run (classified {kind!r}); "
                f"timeout was {config.SCANNER_TIMEOUT_SECONDS}s",
            )

        # Gitleaks returns:
        # 0 = no leaks
        # 1 = leaks found
        #
        # Anything else is the binary reporting that it broke. It RAN, so this is
        # a fault with no ambiguity to resolve and it blocks whatever
        # SCANNERS_REQUIRED says. This used to raise, which reached the fixture
        # fallback -- fine when no binary is installed, wrong when one is: the
        # fixture verdict describes the DEMO diff, not whatever is being scanned.
        if result.returncode not in (0, 1):
            return [
                error_finding(
                    "gitleaks",
                    f"exit code {result.returncode}: {result.stderr.strip()}",
                )
            ]

        # The binary ran and left no report. Distinct from the absent case above
        # and NOT the same answer: there the report is missing because nothing
        # ever ran, and the fixture fallback is right. Here gitleaks ran and
        # produced nothing usable, so the change is genuinely unscanned.
        if not report_path.exists():
            return [
                error_finding(
                    "gitleaks",
                    f"exit code {result.returncode} but no report at "
                    f"{report_path}. stderr: {result.stderr.strip()}",
                )
            ]

        try:
            data = json.loads(
                report_path.read_text(
                    encoding="utf-8"
                )
            )
        except json.JSONDecodeError as exc:
            return [
                error_finding(
                    "gitleaks", f"report at {report_path} is not valid JSON: {exc}"
                )
            ]

    # Valid JSON of the wrong SHAPE is still unusable, and it does not announce
    # itself the way a parse error does. gitleaks' report is a list of objects,
    # and `for leak in "some string"` iterates CHARACTERS until `leak.get` raises
    # AttributeError from inside the loop below -- an exception on the fault path
    # at the exact moment the pipeline is trying to report that a scanner failed.
    # `null` is a real gitleaks report meaning "no leaks", so it is not a fault.
    leaks = [] if data is None else data
    if not isinstance(leaks, list) or not all(isinstance(leak, dict) for leak in leaks):
        return [
            error_finding(
                "gitleaks",
                f"report was not the expected JSON list of objects: "
                f"got {type(data).__name__}",
            )
        ]

    findings: list[Finding] = []

    for leak in leaks:
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