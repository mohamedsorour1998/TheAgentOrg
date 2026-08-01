"""Gitleaks wrapper — finds committed secrets (this is what blocks the demo).

OWNER: Habiba.

WHAT TO BUILD (see docs/plan/habiba.md):
    1. Write the diff/files to a temp dir.
    2. Run `gitleaks detect --source <dir> --report-format json --no-git`.
    3. Parse the JSON report; map each leak to a Finding.
    4. Secrets (AWS keys etc.) are severity "critical" — that trips the block rule.

STUB: if the diff contains an AWS-key pattern, return two critical findings so
the poisoned demo blocks even before your real code lands.
"""

import re

from ..state import DevResult, Finding

_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")


def scan(dev: DevResult) -> list[Finding]:
    # TODO(Habiba): replace with a real `gitleaks detect` subprocess + JSON parse.
    if _AWS_KEY.search(dev.diff or ""):
        return [
            Finding(tool="gitleaks", severity="critical", rule="aws-access-key-id",
                    file=dev.files_changed[0] if dev.files_changed else "unknown",
                    line=4, description="AWS access key id committed in source."),
            Finding(tool="gitleaks", severity="critical", rule="aws-secret-access-key",
                    file=dev.files_changed[0] if dev.files_changed else "unknown",
                    line=5, description="AWS secret access key committed in source."),
        ]
    return []
