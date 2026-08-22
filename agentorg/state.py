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

    # THE DEMO SAFETY NET, CARRIED ON THE STATE. Added in week 3 for remote
    # execution; an ADDITION, per the rule at the top of this file.
    #
    # `developer.run(state, poisoned=...)` is a Python keyword argument, and
    # agents/server.py:164 calls `AGENTS[role].run(state)` with no kwargs --
    # over HTTP there is nowhere to put one. The state IS the payload, so a
    # per-call argument the container must see has to travel as a field.
    #
    # The kwarg still wins where it is passed: developer.run reads this only
    # when the kwarg is absent, so graph.py's local call site behaves exactly as
    # it did before this field existed. See agentorg/agents/developer.py.
    #
    # Defaults False, which is what keeps every run already on disk -- and every
    # RunState built without it -- a clean run.
    poisoned: bool = False

    # WHICH PATH ANSWERED: the model, or a fixture. An ADDITION, per the rule at
    # the top of this file. "" means a run written before this field existed --
    # reported as unknown rather than guessed, exactly as scan_provenance's "" is.
    #
    # ADDED 2026-08-22, and the reason is the defect it would have caught. Every
    # model-calling agent in the deployed pipeline had been serving fixtures for a
    # week, because `bedrock:InvokeModel` was implicitDeny on the inference profile
    # `config.BEDROCK_MODEL` names -- the runtime role granted `foundation-model/*`
    # only, and `us.amazon.nova-2-lite-v1:0` is an `inference-profile/` ARN.
    # `llm.text()` catches the denial by design, so every run completed, every job
    # was green, and the plan comment on the target repo matched
    # fixtures/plan_result.json byte for byte. The fallback is correct behaviour;
    # being unable to SEE it is not.
    #
    # A plain `str`, deliberately not a Literal: an older run carrying an
    # unexpected value must read as unknown, not fail validation. This field exists
    # to report honestly, and a field that refuses to load cannot report at all.
    #
    # "mixed" is a real value, not a hedge. A run where the planner reached the
    # model and the reviewer did not is neither a model run nor a fixture run, and
    # collapsing it either way makes a partial outage look total or invisible --
    # the same reasoning that keeps `fixture-fallback` distinct from
    # `fixture-stub`.
    model_provenance: str = ""

    # HOW THIS RUN STARTED. An ADDITION. Defaults "manual", which is what a hand
    # dispatch leaves it as; the EventBridge input transformer sends "issue".
    #
    # It exists because `event:` cannot answer the question. EventBridge triggers
    # the workflow through the same REST dispatch API `gh workflow run` uses, so
    # both read `workflow_dispatch` and NO field distinguishes them -- measured on
    # run 32542152671, which an opened issue started and which reports
    # `workflow_dispatch` like every hand dispatch before it.
    #
    # Trustworthy in the direction that matters: only the rule sends "issue", so a
    # run claiming it was issue-triggered was.
    trigger: str = "manual"


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
        # ADDED 2026-08-22. A new MEMBER of the union, not a rename -- this file is
        # frozen against renames and removals, and an addition breaks nothing that
        # already reads it.
        #
        # It exists because `failed` had no action of its own, so both pipeline
        # paths borrowed one, and the two borrowings were wrong in opposite
        # directions. MEASURED:
        #
        #   run_stage._OUTCOME_ACTIONS mapped failed -> "blocked", so a run whose
        #   revision cap expired rendered "⛔ BLOCKED — the change was stopped"
        #   while its security verdict was `pass` with 0 blocking findings. That
        #   inverts the pipeline's central claim: it says the deterministic rule
        #   stopped a change the scanners had cleared.
        #
        #   The SRE no_go path logged nothing at all, so the run rendered
        #   "… INCOMPLETE — run stopped at sre without an ending" -- and
        #   timeline._outcome reads the LAST row's action, never RunState.status,
        #   so a finished run looked abandoned.
        #
        # A run nobody approved, and a run the rule stopped, are different endings.
        "failed",
    ]
    verdict: str = ""
    summary: str = ""
    artifact_ref: str = ""          # PR url, branch, path to findings json
    # Set on the security rows only. The timeline reads ONLY log.read(run_id),
    # so a fact that stays on the in-memory SecurityResult is a fact the judges
    # never see. Absent on every run logged before week 3; see ScanProvenance.
    scan_provenance: ScanProvenanceOrUnknown = ""
