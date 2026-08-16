"""Security agent — scanners, the deterministic block rule, and prose.

OWNER: Sorour wires the agent; Habiba owns the scanners in agentorg/security/.

CRITICAL: the LLM does NOT decide pass/block. compute_security_verdict() --
pure code in state.py -- does. The model is handed a verdict that is already
final and asked only to explain it. That is what makes the poisoned ticket
block on every single run rather than most of them.

The scanner call is wrapped, and what it falls back TO is the load-bearing
part. compute_security_verdict([]) returns ("pass", []), so a scanner failure
turned into "no findings" would send a poisoned change green while every test
in the suite stayed green with it -- the one bug that survives CI and takes the
demo down. The scanners therefore raise rather than return [] on failure, and
this module answers a raise with the FIXTURE verdict, which still blocks a diff
carrying an AWS key. Never substitute an empty list here.
"""

import logging

from .. import fixtures_loader
from ..common import config, llm
from ..security import run_all_scanners
from ..state import Finding, RunState, SecurityResult, compute_security_verdict

SYSTEM_PROMPT = """You are the Security explainer. You are given a verdict and a
list of blocking findings that were ALREADY decided by code. Write 1-3 plain
sentences explaining why the change was blocked or allowed, naming the tools and
rules. You may NOT change the verdict. Return plain text, no JSON."""


def _looks_poisoned(state: RunState) -> bool:
    """Does the diff carry an AWS access key id? Pure string check, no model."""
    return state.dev is not None and "AKIA" in (state.dev.diff or "")


def _default_explanation(verdict: str, blocking: list[Finding]) -> str:
    """Deterministic prose, used whenever no model answers."""
    if verdict == "block":
        return "Blocked: " + "; ".join(
            f"{f.tool}:{f.rule} ({f.severity}) in {f.file}:{f.line}" for f in blocking
        )
    return "Passed: no findings at or above the block threshold."


def _explain(verdict: str, blocking: list[Finding]) -> str:
    """Let the model write the prose; fall back to a fixed string.

    The verdict is passed in already decided. Whatever comes back is used as
    text and nothing else -- it is never parsed, and it never reaches the
    verdict.
    """
    findings_txt = "\n".join(
        f"- {f.tool} {f.rule} {f.severity} {f.file}:{f.line} {f.description}"
        for f in blocking
    ) or "(none)"
    reply = llm.text(
        SYSTEM_PROMPT, f"VERDICT: {verdict}\nBLOCKING FINDINGS:\n{findings_txt}"
    )
    return reply if reply else _default_explanation(verdict, blocking)


def run(state: RunState, use_real_scanners: bool = True) -> SecurityResult:
    """Scan the diff, decide in code, then attach an explanation."""
    if not use_real_scanners:
        # STUB path, kept for demos that must not shell out at all.
        return fixtures_loader.security(block=_looks_poisoned(state))

    try:
        findings = run_all_scanners(state.dev)
    except Exception:
        # The scanner lane is missing a binary or crashed. Catching broadly is
        # deliberate: three CLI wrappers can fail in more ways than they can
        # enumerate (FileNotFoundError with no binary on PATH, RuntimeError on
        # a non-zero exit or an unwritten report, OSError, a parse error), and
        # any one escaping would take the pipeline down at the gate that exists
        # to stop bad code. Log with the traceback so a run that quietly used
        # its fixture can still be explained afterwards; the logger is fetched
        # inline because ruff's BLE001 only accepts that form at the call site.
        logging.getLogger(__name__).warning(
            "scanners failed; falling back to the fixture verdict", exc_info=True
        )
        # Fall back to the FIXTURE, never to an empty findings list -- see the
        # module docstring. This still blocks a diff carrying an AWS key.
        return fixtures_loader.security(block=_looks_poisoned(state))

    verdict, blocking = compute_security_verdict(
        findings, threshold=config.SECURITY_BLOCK_THRESHOLD
    )
    return SecurityResult(
        verdict=verdict,
        findings=findings,
        blocking=blocking,
        explanation=_explain(verdict, blocking),
    )
