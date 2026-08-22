"""SRE agent — final go/no-go: is CI green and are the SLOs met?

OWNER: Sorour.  Strands agent on AgentCore.

Keep this small (it is first on the cut-list). For the demo, checking that CI
passed is enough for a `go`.
"""

from .. import fixtures_loader
from ..common import llm
from ..state import RunState, SREResult

SYSTEM_PROMPT = """You are the SRE. Given CI status and SLO checks, return go or
no_go with a short rationale. Output must match the SREResult schema."""


def run(state: RunState) -> SREResult:
    """STUB: returns the fixture (go). REAL: read CI status via github_ops / Actions."""
    # TODO(Sorour, wk3): real check — CI passing => go.
    #
    # The stamp is honest about what this stub is: it serves the fixture on every
    # call, so it records a fixture fallback on every call. Without it a run in
    # which the other four agents all reached the model would be labelled a model
    # run while one fifth of it was a fixture -- which is the partial-outage case
    # the asymmetry in llm._record exists to catch.
    llm.record_fixture_fallback()
    return fixtures_loader.sre()
