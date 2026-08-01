"""Security scanner lane.

OWNER: Habiba.

Three wrappers — semgrep, gitleaks, trivy — each take the developer's diff/files
and return a list of Finding (the shape frozen in state.py). run_all_scanners()
fans out to all three and concatenates. The security agent then applies the
deterministic block rule; you do NOT decide pass/block here — you only produce
findings.

This lane is fully self-contained: it depends only on state.py and the scanner
CLIs. It never imports the graph, so you can build and test it in isolation.
"""

from ..state import DevResult, Finding
from .semgrep_tool import scan as _semgrep
from .gitleaks_tool import scan as _gitleaks
from .trivy_tool import scan as _trivy


def run_all_scanners(dev: DevResult | None) -> list[Finding]:
    """Run all three scanners over the developer's change; return all findings."""
    if dev is None:
        return []
    findings: list[Finding] = []
    for scan in (_semgrep, _gitleaks, _trivy):
        findings.extend(scan(dev))
    return findings
