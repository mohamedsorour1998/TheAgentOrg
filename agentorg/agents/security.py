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

# The logger is fetched inline at each call site rather than bound to a
# module-level `_log`. BLE001 wants the handler to hold a logging call it can
# statically resolve to the logging module AND carry the traceback (exc_info=
# True, or .exception()); the LEVEL is irrelevant. A module-level alias defeats
# the resolution and turns `ruff check agentorg` red. See llm.py for the
# measured rule.
import logging

from .. import fixtures_loader
from ..common import config, llm
from ..security import run_all_scanners
from ..state import Finding, RunState, SecurityResult, compute_security_verdict

SYSTEM_PROMPT = """You are the Security explainer. You are given a verdict and a
list of blocking findings that were ALREADY decided by code. Write 1-3 plain
sentences explaining why the change was blocked or allowed, naming the tools and
rules. You may NOT change the verdict. Return plain text, no JSON."""

# Longest model reply accepted as an explanation. This string is shown on the
# projector and posted to the PR by github_ops.post_comment, so an unbounded
# reply is a way for a misbehaving model to put a wall of text where "Blocked:
# ..." should be. 2000 chars is ~4x the longest honest answer to the prompt
# above (1-3 sentences runs 150-400) while still fitting on a screen. Anything
# past it is not a long explanation, it is evidence the model ignored the
# instruction -- so the reply is DISCARDED for the deterministic prose rather
# than truncated, since half a wall of text is still a wall of text.
MAX_EXPLANATION_CHARS = 2000


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
    verdict. It is also length-capped: see MAX_EXPLANATION_CHARS.
    """
    findings_txt = "\n".join(
        f"- {f.tool} {f.rule} {f.severity} {f.file}:{f.line} {f.description}"
        for f in blocking
    ) or "(none)"
    reply = llm.text(
        SYSTEM_PROMPT, f"VERDICT: {verdict}\nBLOCKING FINDINGS:\n{findings_txt}"
    )
    if reply and len(reply) > MAX_EXPLANATION_CHARS:
        logging.getLogger(__name__).warning(
            "model explanation was %d chars (cap %d); using the deterministic one",
            len(reply),
            MAX_EXPLANATION_CHARS,
        )
        reply = None
    return reply if reply else _default_explanation(verdict, blocking)


def run(state: RunState, use_real_scanners: bool = True) -> SecurityResult:
    """Scan the diff, decide in code, then attach an explanation."""
    if not use_real_scanners:
        # STUB path, kept for demos that must not shell out at all.
        return fixtures_loader.security(block=_looks_poisoned(state))

    try:
        findings = run_all_scanners(state.dev)
    except Exception as exc:
        # Catching broadly is deliberate and must stay that way. The three CLI
        # wrappers are Habiba's lane and their failure surface moves as she
        # iterates -- FileNotFoundError with no binary on PATH, RuntimeError on
        # a non-zero exit or an unwritten report, OSError, a JSON parse error.
        # A narrow clause is correct only on the day it is written, and its
        # failure mode is the pipeline crashing at the gate that exists to stop
        # bad code. Note ruff will NOT catch that regression: a narrowed except
        # with no logging at all is BLE001-clean, so lint silently blesses the
        # more dangerous option.
        #
        # One line at WARNING naming the cause, traceback at DEBUG. During the
        # demo this is a projector line, and 26 lines of traceback immediately
        # above `status=blocked` reads as a crash. Nothing here can reach the
        # verdict: logging level cannot affect control flow.
        logging.getLogger(__name__).warning(
            "scanners failed (%s: %s); falling back to the fixture verdict",
            type(exc).__name__,
            exc,
        )
        logging.getLogger(__name__).debug("scanner failure traceback", exc_info=True)
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
