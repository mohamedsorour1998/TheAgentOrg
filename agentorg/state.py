"""
Shared data contracts for The Agent Org.

Every agent reads and writes these shapes. Agree them at kickoff and freeze them.

Rule after week 1: you may ADD optional fields. Never rename or remove one.
A rename breaks all five lanes at once and nobody notices until integration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Shared vocabulary
# --------------------------------------------------------------------------

Severity = Literal["low", "medium", "high", "critical"]
Actor = Literal["planner", "developer", "reviewer", "security", "sre", "human", "system"]
Stage = Literal["plan", "gate1", "develop", "review", "security", "gate2", "sre", "gate3", "promote"]

# WHERE A SECURITY VERDICT CAME FROM. Added in week 3 for the timeline UI.
#
# "blocked" proves two different things depending on this value, and until it
# existed nothing on disk told them apart: agents/security.py answers a scanner
# raise with the FIXTURE verdict, which still blocks a diff carrying an AWS key,
# so a fixture block and a real gitleaks block wrote byte-identical log rows.
# On a machine with no scanners on PATH -- which is CI, and was the demo laptop --
# the fixture path is the DEFAULT, not an edge case.
#
# Inferring this after the fact is not possible: the block fixture's explanation
# is "Two AWS credentials are hardcoded in app/auth.py. Move them to the
# environment and rotate the key before merging." -- specific, plausible, naming
# a real file and a real remediation, and indistinguishable from real gitleaks
# output. So it is RECORDED at the call site when the run happens, and the
# renderer reports absence as unknown rather than guessing.
#
#   "scanners"          run_all_scanners returned; compute_security_verdict decided.
#   "fixture-fallback"  a scanner RAISED; the fixture verdict stood in. A fault.
#   "fixture-stub"      use_real_scanners=False; nobody asked for a scan. A choice.
#
# The last two are kept apart deliberately. Both are fixture verdicts, but one is
# a scanner that failed and one is a scan that was never requested -- collapsing
# them would hide a broken gate behind a deliberate demo setting.
ScanProvenance = Literal["scanners", "fixture-fallback", "fixture-stub"]

# "" is the fourth, unnameable state: a row written before this field existed.
# Typed as a Literal union rather than a bare str so a typo at a call site is a
# ValidationError when the row is written, not a mystery in the renderer.
ScanProvenanceOrUnknown = ScanProvenance | Literal[""]

SEVERITY_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------
# Agent results — one per role agent
# --------------------------------------------------------------------------

class PlanResult(BaseModel):
    tasks: list[str]
    acceptance_criteria: list[str]
    target_files: list[str]
    notes: str = ""


class DevResult(BaseModel):
    branch: str
    diff: str                       # unified diff, single string
    summary: str
    files_changed: list[str]
    pr_url: str | None = None       # filled in by github_ops, not the agent


class ReviewComment(BaseModel):
    file: str
    line: int
    note: str


class ReviewResult(BaseModel):
    verdict: Literal["approve", "changes_requested"]
    comments: list[ReviewComment] = Field(default_factory=list)
    must_fix: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    tool: Literal["semgrep", "gitleaks", "trivy"]
    severity: Severity
    rule: str
    file: str
    line: int
    description: str


class SecurityResult(BaseModel):
    verdict: Literal["pass", "block"]
    findings: list[Finding] = Field(default_factory=list)
    blocking: list[Finding] = Field(default_factory=list)
    explanation: str = ""           # LLM writes this; it does NOT set the verdict
    # Which of agents/security.run's three paths produced the verdict above.
    # It rides on the RESULT rather than being handed back separately because
    # graph.py reaches this agent through `security.run(state)` and tests
    # monkeypatch that exact name -- a second entry point would slip the seam.
    scan_provenance: ScanProvenanceOrUnknown = ""


class SLOCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class SREResult(BaseModel):
    verdict: Literal["go", "no_go"]
    ci_status: Literal["passing", "failing", "unknown"]
    slo_checks: list[SLOCheck] = Field(default_factory=list)
    estimated_cost_note: str = ""
    notes: str = ""


# --------------------------------------------------------------------------
# The block rule lives here, in code, not in a prompt.
# This is what makes the demo fire every single time.
# --------------------------------------------------------------------------

def compute_security_verdict(
    findings: list[Finding],
    threshold: Severity = "high",
) -> tuple[Literal["pass", "block"], list[Finding]]:
    """Block if any finding is at or above the threshold severity."""
    cutoff = SEVERITY_ORDER[threshold]
    blocking = [f for f in findings if SEVERITY_ORDER[f.severity] >= cutoff]
    return ("block" if blocking else "pass"), blocking


# --------------------------------------------------------------------------
# Human gates
# --------------------------------------------------------------------------

class HumanDecision(BaseModel):
    gate: Literal["gate1", "gate2", "gate3"]
    decision: Literal["approved", "rejected", "overridden"]
    by: str
    at: str = Field(default_factory=_now)
    reason: str = ""


# --------------------------------------------------------------------------
# The state that flows through the Strands graph
# --------------------------------------------------------------------------

class RunState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    ticket_id: str
    ticket_text: str
    started_at: str = Field(default_factory=_now)

    plan: PlanResult | None = None
    dev: DevResult | None = None
    review: ReviewResult | None = None
    security: SecurityResult | None = None
    sre: SREResult | None = None

    decisions: list[HumanDecision] = Field(default_factory=list)
    revision_count: int = 0         # capped by MAX_REVISION_LOOPS
    status: Literal["running", "blocked", "rejected", "promoted", "failed"] = "running"


# --------------------------------------------------------------------------
# Decision log — one row per event, append only. Never update, never delete.
# --------------------------------------------------------------------------

class LogEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: str = Field(default_factory=_now)
    run_id: str
    ticket_id: str
    actor: Actor
    stage: Stage
    action: Literal[
        "opened", "proposed", "reviewed", "blocked", "passed",
        "approved", "rejected", "overridden", "merged", "promoted",
    ]
    verdict: str = ""
    summary: str = ""
    artifact_ref: str = ""          # PR url, branch, path to findings json
    # Set on the security rows only. The timeline reads ONLY log.read(run_id),
    # so a fact that stays on the in-memory SecurityResult is a fact the judges
    # never see. Absent on every run logged before week 3; see ScanProvenance.
    scan_provenance: ScanProvenanceOrUnknown = ""
