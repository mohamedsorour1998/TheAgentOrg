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

SEVERITY IS A POLICY, NOT A MEASUREMENT
    gitleaks reports no severity field, so every Finding below takes its
    severity from `scoring.policy_severity("gitleaks")` -- a CONSTANT BY RULE,
    `critical`, because a committed credential has no lesser grade. The rule
    itself, the alternatives that were rejected, and what it costs the block
    threshold are in `POLICY["gitleaks"]` in `security/scoring.py`. This wrapper
    ASKS for the value rather than restating it, so the policy has one home a
    reader can find and one place to change.
"""

import json
import os
import tempfile
from pathlib import Path

from ..common import config
from ..common.diff import write_added_files
from ..state import DevResult, Finding
from . import scoring
from ._run import (
    ReportShapeError,
    error_finding,
    report_int,
    report_text,
    run_scanner,
    unrunnable_findings,
)

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
    # `null` is accepted as an empty report purely defensively. MEASURED with
    # real gitleaks 8.21.2 on a clean tree: it writes `[]`, not `null`. So this
    # is not a case the binary is known to produce -- it costs one comparison
    # and keeps a plausible variant off the fault path, which is the whole
    # justification. Do not cite it as gitleaks behaviour.
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

    # Wrong-typed INNER fields are a fault too, and the top-level guard above
    # cannot see them -- see _run.report_text for the nine measured crashes and
    # the fail-open they produce end to end. Every field this loop dereferences
    # goes through a reader that rejects an unusable type instead of raising from
    # the middle of a Finding construction.
    try:
        for leak in leaks:
            findings.append(
                Finding(
                    tool="gitleaks",
                    # WAS `severity="critical"`. Same value, and now the code
                    # says what it is DOING: this is a POLICY -- a rule this
                    # project applies -- not a measurement read off the report.
                    # gitleaks emits no severity field at all (RuleID, File,
                    # StartLine, Description, an entropy score, and nothing that
                    # ranks the hit), so there is nothing to map and a severity
                    # has to come from somewhere. A bare literal here states
                    # neither of those facts, and a reader cannot tell a
                    # deliberate rule from a forgotten TODO.
                    #
                    # The honest consequence, said where it applies: because the
                    # value is constant and at the top of the scale, the block
                    # threshold has ONE input to compare for this scanner rather
                    # than four. It still runs -- it does not discriminate.
                    #
                    # `scoring.policy_severity`, not `map_severity`: the name
                    # distinguishes "the scanner told us nothing and we have a
                    # rule" from "we mapped what the scanner said". Called
                    # through the MODULE, per this repo's standing rule for
                    # anything a test may need to substitute -- `from .scoring
                    # import policy_severity` binds the value at import, before
                    # any test runs, and the coupling stops being observable.
                    #
                    # The rule, the rejected alternatives (entropy ranking,
                    # per-rule severities) and the rationale a judge reads are in
                    # `POLICY["gitleaks"]`; do not copy them here.
                    severity=scoring.policy_severity("gitleaks"),
                    rule=report_text(leak, "RuleID", "unknown"),
                    file=_repo_relative(
                        report_text(leak, "File", "unknown"),
                        temp_dir,
                    ),
                    line=report_int(leak, "StartLine", 0),
                    description=(
                        report_text(leak, "Description", "")
                        or "Secret detected by Gitleaks."
                    ),
                )
            )
    except ReportShapeError as exc:
        return [error_finding("gitleaks", f"unusable report: {exc}")]

    return findings