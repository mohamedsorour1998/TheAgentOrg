"""Semgrep wrapper — static analysis for code smells / insecure patterns.

OWNER: Habiba.

WHAT TO BUILD:
    1. Write the changed files to a temp dir.
    2. Run `semgrep --config auto --json <dir>`.
    3. Map each result to a Finding; map semgrep severity -> our Severity.

STUB: returns a single low finding so the pass-path demo has something to show.
"""

from ..state import DevResult, Finding


def scan(dev: DevResult) -> list[Finding]:
    # TODO(Habiba): replace with a real `semgrep --json` subprocess + parse.
    if "redis.Redis(" in (dev.diff or ""):
        return [Finding(tool="semgrep", severity="low",
                        rule="python.flask.missing-timeout",
                        file=dev.files_changed[0] if dev.files_changed else "unknown",
                        line=7, description="Redis client created without a socket timeout.")]
    return []
