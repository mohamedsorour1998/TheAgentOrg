"""Semgrep wrapper — static analysis for code smells / insecure patterns.

OWNER: Habiba.

WHAT TO BUILD:
    1. Write the changed files to a temp dir.
    2. Run semgrep with our rules and JSON output.
    3. Map each result to a Finding.
    4. Map Semgrep severity -> our Severity.

EVERY FAILURE PATH HERE IS FAIL-CLOSED, AND ONE IS NOT
    `compute_security_verdict([])` returns `("pass", [])`, so this wrapper may
    never answer a broken scanner with an empty list. Each failure below returns
    `[error_finding("semgrep", ...)]`, which is `high`, which is the block
    threshold. The one exception is a binary that is merely ABSENT: per the
    plan's central ruling that is a development and CI affordance, so it raises
    and agents/security.py falls back to the fixture verdict. The whole
    absent-vs-fault decision lives in `_run.unrunnable_findings`.
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
    report_int,
    report_mapping,
    report_text,
    run_scanner,
    unrunnable_findings,
)
from .gitleaks_tool import _repo_relative


def _write_diff_to_temp(dev: DevResult, temp_dir: str) -> None:
    """Materialize added/changed files from the unified diff.

    One materialiser, in agentorg/common/diff.py, shared with the other two
    wrappers and with the developer's poisoned safety net -- see the note in
    gitleaks_tool for what four private copies of this cost. Added lines only,
    deleted files ignored, exactly as before.
    """

    write_added_files(dev.diff, temp_dir)


def _map_severity(severity: str | None) -> str:
    """Map Semgrep severity onto our vocabulary, FAILING CLOSED on the unknown.

    MEASURED 2026-08-22, when this table held only INFO/WARNING/ERROR and
    defaulted to "low": semgrep 1.x also emits LOW/MEDIUM/HIGH/CRITICAL from
    new-style rule metadata, and every one of those fell through to severity 0
    against a block cutoff of 2 (`SEVERITY_ORDER["high"]`). A rule semgrep marked
    CRITICAL could not block a change. Nothing caught it: no test read this
    function, and scripts/scan_gate.py asserts only `any(f.tool == "semgrep")`,
    which a `low` finding satisfies.

    THE DEFAULT IS "high", NOT "low", AND THAT IS THE WHOLE POINT. An
    unrecognised value means semgrep said something this table has not seen.
    Defaulting low means a future severity name silently stops blocking;
    defaulting high means it blocks loudly and somebody fixes the table. Only one
    of those is safe to be wrong about, and this project's signature defect is
    the other one.

    THE DEFAULT ALSO CATCHES AN ABSENT FIELD, deliberately. `scan` reads this
    through `report_text(extra, "severity", "")`, so a result whose `extra` omits
    `severity` -- the shape of a truncated report -- arrives here as `""` and
    takes the same fail-closed route. A report that does not say how bad a
    finding is has not said it is harmless.

    The mapping is not symmetric with trivy's and must not be "harmonised" into
    one table: semgrep's ERROR is its top level and means `high`, while trivy
    spells its top level CRITICAL. Both are listed here because semgrep emits
    both vocabularies depending on how a rule declares its metadata.
    """

    mapping = {
        "INFO": "low",
        "LOW": "low",
        "WARNING": "medium",
        "MEDIUM": "medium",
        "ERROR": "high",
        "HIGH": "high",
        "CRITICAL": "critical",
    }

    return mapping.get((severity or "").upper(), "high")


def scan(dev: DevResult) -> list[Finding]:
    """Run Semgrep and convert its JSON results into Finding objects."""

    with tempfile.TemporaryDirectory(
        prefix="agentorg-semgrep-"
    ) as temp_dir:

        _write_diff_to_temp(dev, temp_dir)

        report_path = Path(temp_dir) / "semgrep-report.json"

        rules_path = Path(__file__).with_name("semgrep_rules.yml")

        # A missing rules file is a FAULT, never an "absent scanner". The file
        # ships inside this package, so its absence means a broken install or a
        # bad build -- semgrep itself may be perfectly present. It also cannot
        # reach the fixture-fallback path on a machine that HAS semgrep, because
        # then the gate would report the demo fixture's verdict for whatever
        # change is actually being scanned. Note the FileNotFoundError this used
        # to raise was indistinguishable, to the handler in agents/security.py,
        # from the no-binary case.
        if not rules_path.exists():
            return [
                error_finding(
                    "semgrep",
                    f"its rules file is missing from the installed package: "
                    f"{rules_path}",
                )
            ]

        result, kind = run_scanner(
            [
                "semgrep",
                "--config",
                str(rules_path),
                "--json",
                "--output",
                str(report_path),
                temp_dir,
            ],
            timeout=config.SCANNER_TIMEOUT_SECONDS,
        )

        # The command never launched. `kind` carries the absent-vs-fault verdict
        # already, computed from both the exception type and the filesystem.
        if result is None:
            return unrunnable_findings(
                "semgrep",
                kind,
                f"the semgrep command could not be run (classified {kind!r}); "
                f"timeout was {config.SCANNER_TIMEOUT_SECONDS}s",
            )

        # Semgrep exits 1 when it HAS findings -- the poisoned demo depends on
        # that -- so only other codes mean it broke. It ran, so this is a fault
        # whatever SCANNERS_REQUIRED says.
        if result.returncode not in (0, 1):
            return [
                error_finding(
                    "semgrep",
                    f"exit code {result.returncode}: {result.stderr.strip()}",
                )
            ]

        # It ran and left no report: a fault, and NOT the same case as an absent
        # binary. There nothing ran and the fixture fallback is right; here the
        # change is genuinely unscanned by a scanner that is installed.
        if not report_path.exists():
            return [
                error_finding(
                    "semgrep",
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
                    "semgrep", f"report at {report_path} is not valid JSON: {exc}"
                )
            ]

    # Valid JSON of the wrong SHAPE is unusable too, and it fails LOUDLY in a way
    # a reader does not expect: `data.get` raises AttributeError on a list or a
    # string, on the fault path, where a blocking finding was supposed to appear.
    if not isinstance(data, dict):
        return [
            error_finding(
                "semgrep",
                f"report was {type(data).__name__}, not the expected JSON object",
            )
        ]

    results = data.get("results", [])
    if not isinstance(results, list) or not all(
        isinstance(item, dict) for item in results
    ):
        return [
            error_finding(
                "semgrep",
                f"report's 'results' was not a list of objects: "
                f"got {type(results).__name__}",
            )
        ]

    findings: list[Finding] = []

    # `temp_dir` IS STILL IN SCOPE HERE, and that is worth stating because the
    # obvious reading says otherwise. `with` is not a scope in Python -- only
    # functions are -- so the name the `with` statement bound above is live for
    # the rest of `scan`, even though the DIRECTORY it names was deleted when the
    # block exited. Nothing below needs that directory to exist: `_repo_relative`
    # is `os.path.relpath`, which is pure string arithmetic on two paths.
    # MEASURED, with os.stat/lstat/listdir/readlink instrumented: relpath of an
    # absolute path against an absolute base makes ZERO filesystem calls.
    #
    # So the parse stays OUT here rather than being moved inside the block. The
    # alternative -- moving the JSON parse and the Finding construction inside
    # the `with` -- would work too, but it widens the window in which the scratch
    # tree is still on disk for no gain, and it would reindent a hundred lines of
    # audited fault handling for a two-line fix.
    #
    # The one case where relpath DOES touch the filesystem is a RELATIVE input,
    # because it must call getcwd() to resolve it. That matters here: semgrep is
    # invoked with an absolute target so it reports absolute paths, but a report
    # that omitted `path` entirely would hand the default "unknown" to relpath and
    # get back a long `../../..`-prefixed path built from the CWD. gitleaks has
    # the identical behaviour on its own default -- measured, both produce
    # `../../...../unknown` -- so this wrapper is now no worse than the one the
    # brief points at, and the shared helper means a fix to that reaches both.

    # Wrong-typed INNER fields are a fault too, and the top-level guards above
    # cannot see them -- see _run.report_text for the nine measured crashes and
    # the fail-open they produce end to end. `extra` is the likeliest of them for
    # this wrapper: it holds severity and message, so a truncated or mangled
    # report makes `extra.get` raise AttributeError.
    try:
        for result_item in results:
            extra = report_mapping(result_item, "extra")
            start = report_mapping(result_item, "start")

            findings.append(
                Finding(
                    tool="semgrep",
                    severity=_map_severity(
                        report_text(extra, "severity", "")
                    ),
                    rule=report_text(result_item, "check_id", "unknown"),
                    file=_repo_relative(
                        report_text(result_item, "path", "unknown"),
                        temp_dir,
                    ),
                    line=report_int(start, "line", 0),
                    description=(
                        report_text(extra, "message", "")
                        or "Semgrep finding."
                    ),
                )
            )
    except ReportShapeError as exc:
        return [error_finding("semgrep", f"unusable report: {exc}")]

    return findings