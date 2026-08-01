"""Security agent — runs the scanners and applies the deterministic block rule.

OWNER: Sorour wires the agent; Habiba owns the scanners in agentorg/security/.

CRITICAL: the LLM does NOT decide pass/block. It only writes the human-readable
explanation. The verdict comes from compute_security_verdict() in state.py, which
is pure code. That is what makes the poisoned ticket block every single run.
"""

from ..state import RunState, SecurityResult, compute_security_verdict
from ..common import config
from .. import fixtures_loader
from ..security import run_all_scanners

SYSTEM_PROMPT = """You are the Security explainer. You are given findings and a
verdict already computed by code. Write a short, clear explanation of why the
change is blocked or allowed. You may NOT change the verdict."""


def run(state: RunState, use_real_scanners: bool = False) -> SecurityResult:
    """Scan the diff, compute the verdict deterministically, attach an explanation."""
    if not use_real_scanners:
        # STUB path for the demo before Habiba's scanners land: pick block/pass
        # from whether the diff looks poisoned. Deterministic, no LLM.
        poisoned = state.dev is not None and "AKIA" in (state.dev.diff or "")
        return fixtures_loader.security(block=poisoned)

    # REAL path: Habiba's scanners produce findings; the rule decides.
    findings = run_all_scanners(state.dev)
    verdict, blocking = compute_security_verdict(findings, threshold=config.SECURITY_BLOCK_THRESHOLD)
    # TODO(Sorour, wk2): LLM writes `explanation` only.
    return SecurityResult(verdict=verdict, findings=findings, blocking=blocking,
                          explanation="")
