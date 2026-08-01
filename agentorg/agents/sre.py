"""SRE agent — final go/no-go: is CI green and are the SLOs met?

OWNER: Sorour.  Strands agent on AgentCore.

Keep this small (it is first on the cut-list). For the demo, checking that CI
passed is enough for a `go`.
"""

from ..state import RunState, SREResult
from .. import fixtures_loader

SYSTEM_PROMPT = """You are the SRE. Given CI status and SLO checks, return go or
no_go with a short rationale. Output must match the SREResult schema."""


def run(state: RunState) -> SREResult:
    """STUB: returns the fixture (go). REAL: read CI status via github_ops / Actions."""
    # TODO(Sorour, wk3): real check — CI passing => go.
    return fixtures_loader.sre()
