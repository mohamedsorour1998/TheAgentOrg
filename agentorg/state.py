"""
Shared data contracts for The Agent Org.

Every agent reads and writes these shapes. Agree them at kickoff and freeze them.

Rule after week 1: you may ADD optional fields. Never rename or remove one.
A rename breaks all five lanes at once and nobody notices until integration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Shared vocabulary
# --------------------------------------------------------------------------

Severity = Literal["low", "medium", "high", "critical"]
Actor = Literal["planner", "developer", "reviewer", "security", "sre", "human", "system"]
Stage = Literal["plan", "gate1", "develop", "review", "security", "gate2", "sre", "gate3", "promote"]

SEVERITY_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
