"""Trivy wrapper — scans dependencies / filesystem for known vulnerabilities.

OWNER: Habiba.

Runs Trivy over the materialized changed files and maps vulnerabilities
to the shared Finding contract.

EVERY FAILURE PATH HERE IS FAIL-CLOSED, AND ONE IS NOT
    `compute_security_verdict([])` returns `("pass", [])`, so this wrapper may
    never answer a broken scanner with an empty list. Each failure below returns
    `[error_finding("trivy", ...)]`, which is `high`, which is the block
    threshold. The one exception is a binary that is merely ABSENT: per the
    plan's central ruling that is a development and CI affordance, so it raises
    and agents/security.py falls back to the fixture verdict. The whole
    absent-vs-fault decision lives in `_run.unrunnable_findings`.

    Trivy matters most here of the three. It is the only scanner that contributes
    ZERO findings to both demo fixtures, so on the happy path its output and its
    silent-failure output are the same list -- `[]`. Nothing downstream can tell
    "trivy found nothing" from "trivy did not run", which is why the fault has to
    become a finding at this seam rather than be inferred later.
"""

import json
import tempfile
from pathlib import Path

from ..common import config
from ..common.diff import write_added_files
from ..state import DevResult, Finding
from ._run import (
    ReportShapeError,
    error_finding,
    report_objects,
    report_text,
    run_scanner,
    unrunnable_findings,
)


def _write_diff_to_temp(dev: DevResult, temp_dir: str) -> None:
    """Materialize changed files from the unified diff.

    One materialiser, in agentorg/common/diff.py, shared with the other two
    wrappers and with the developer's poisoned safety net -- see the note in
    gitleaks_tool. Added lines only, exactly as before.
    """

    write_added_files(dev.diff, temp_dir)


def _map_severity(severity: str | None) -> str:
    """Map Trivy severity onto our vocabulary, FAILING CLOSED on the unknown.

    The table itself is COMPLETE for trivy today -- UNKNOWN/LOW/MEDIUM/HIGH/
    CRITICAL is trivy's whole vocabulary -- so unlike semgrep_tool's, whose
    identical `or "low"` default was live and downgraded real CRITICAL findings
    to severity 0, this default is latent. It is changed anyway because the trap
    is the same shape one file over, and "the vocabulary is complete" is a claim
    about the version installed today.

    Note trivy's own UNKNOWN is a MAPPED key, not a fall-through: trivy uses it
    for a CVE whose severity its data sources do not carry, which is a real
    answer about a real finding and stays `low` as before. The default below is
    for a value that is not in trivy's vocabulary at all -- a new severity name,
    or an absent field arriving as `""` from
    `report_text(vulnerability, "Severity", "")`. Those are not trivy saying
    "unknown"; they are this table not recognising what trivy said.
    """

    mapping = {
        "UNKNOWN": "low",
        "LOW": "low",
        "MEDIUM": "medium",
        "HIGH": "high",
        "CRITICAL": "critical",
    }

    return mapping.get((severity or "").upper(), "high")


def scan(dev: DevResult) -> list[Finding]:
    """Run Trivy and convert vulnerabilities to Finding objects."""

    with tempfile.TemporaryDirectory(prefix="agentorg-trivy-") as temp_dir:
        _write_diff_to_temp(dev, temp_dir)

        report_path = Path(temp_dir) / "trivy-report.json"

        result, kind = run_scanner(
            [
                "trivy",
                "fs",
                "--format",
                "json",
                "--output",
                str(report_path),
                temp_dir,
            ],
            timeout=config.SCANNER_TIMEOUT_SECONDS,
        )

        # The command never launched. `kind` carries the absent-vs-fault verdict
        # already, computed from both the exception type and the filesystem.
        #
        # A timeout classifies as a fault, and for trivy that is the fault mode
        # most likely to be real: its first run resolves a ~108 MB vulnerability
        # database, which is why config.SCANNER_TIMEOUT_SECONDS is 120s rather
        # than something tighter.
        if result is None:
            return unrunnable_findings(
                "trivy",
                kind,
                f"the trivy command could not be run (classified {kind!r}); "
                f"timeout was {config.SCANNER_TIMEOUT_SECONDS}s",
            )

        # It ran and reported a failure. A fault whatever SCANNERS_REQUIRED says.
        if result.returncode not in (0, 1):
            return [
                error_finding(
                    "trivy",
                    f"exit code {result.returncode}: {result.stderr.strip()}",
                )
            ]

        # It ran and left no report: a fault, and NOT the same case as an absent
        # binary. There nothing ran and the fixture fallback is right; here the
        # change is genuinely unscanned by a scanner that is installed.
        if not report_path.exists():
            return [
                error_finding(
                    "trivy",
                    f"exit code {result.returncode} but no report at "
                    f"{report_path}. stderr: {result.stderr.strip()}",
                )
            ]

        try:
            data = json.loads(
                report_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            return [
                error_finding(
                    "trivy", f"report at {report_path} is not valid JSON: {exc}"
                )
            ]

    # Valid JSON of the wrong SHAPE is unusable too: `data.get` raises
    # AttributeError on a list or a string, on the fault path, where a blocking
    # finding was supposed to appear.
    if not isinstance(data, dict):
        return [
            error_finding(
                "trivy",
                f"report was {type(data).__name__}, not the expected JSON object",
            )
        ]

    # `.get("Results")` then an explicit None check, NOT `or []`.
    #
    # MEASURED: `or []` collapses every falsy wrong type -- "", 0, False, {} -- to
    # a valid empty list BEFORE the shape guard below, so a malformed report
    # produced zero findings and a `pass` verdict instead of a blocking fault.
    # Through the real `scan()` on `{"Results": ""}`:
    #
    #     findings: []
    #     verdict: ('pass', [])
    #
    # semgrep_tool spells the same guard `.get("results", [])` and trips
    # correctly -- on the byte-equivalent report it returns
    # [('semgrep-scanner-error', 'high')]. This was the copy that drifted, and the
    # two spellings look equivalent at a glance, which is why it survived: `or []`
    # reads as a None-safe default and is one for a well-typed report.
    #
    # `None` stays a CLEAN empty scan, and that is why this is an explicit
    # `is None` rather than handing every non-list to the guard. JSON `null` and a
    # missing key are how trivy spells "no targets", which is trivy's ordinary
    # answer on BOTH demo fixtures -- it is the one scanner contributing zero
    # findings to either. Rejecting None too would put `blocking=1` on the clean
    # run from a perfectly healthy scanner and take the promote path down.
    targets = data.get("Results")
    if targets is None:
        targets = []
    if not isinstance(targets, list) or not all(
        isinstance(target, dict) for target in targets
    ):
        return [
            error_finding(
                "trivy",
                f"report's 'Results' was not a list of objects: "
                f"got {type(targets).__name__}",
            )
        ]

    findings: list[Finding] = []

    # Wrong-typed INNER fields are a fault too, and the top-level guards above
    # cannot see them -- see _run.report_text for the nine measured crashes and
    # the fail-open they produce end to end. `Vulnerabilities` is nested one level
    # deeper than either other wrapper reads, so it needs its own list-of-objects
    # check: a single bare string among well-formed entries made
    # `vulnerability.get` raise AttributeError.
    try:
        for target in targets:
            for vulnerability in report_objects(target, "Vulnerabilities"):
                findings.append(
                    Finding(
                        tool="trivy",
                        severity=_map_severity(
                            report_text(vulnerability, "Severity", "")
                        ),
                        rule=report_text(vulnerability, "VulnerabilityID", "unknown"),
                        file=report_text(target, "Target", "unknown"),
                        line=0,
                        description=(
                            report_text(vulnerability, "Title", "")
                            or report_text(vulnerability, "Description", "")
                            or "Trivy vulnerability finding."
                        ),
                    )
                )
    except ReportShapeError as exc:
        return [error_finding("trivy", f"unusable report: {exc}")]

    return findings