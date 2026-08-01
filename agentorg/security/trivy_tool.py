"""Trivy wrapper — scans dependencies / filesystem for known vulnerabilities.

OWNER: Habiba.

WHAT TO BUILD:
    1. Run `trivy fs --format json <dir>` over the changed files / requirements.
    2. Map each vulnerability to a Finding; map trivy severity -> our Severity.

STUB: returns nothing (no vulnerable deps in the demo diff). Wire the real
subprocess when semgrep + gitleaks are done.
"""

from ..state import DevResult, Finding


def scan(dev: DevResult) -> list[Finding]:
    # TODO(Habiba): replace with a real `trivy fs --format json` subprocess + parse.
    return []
