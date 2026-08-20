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
from ..state import (
    Finding,
    RunState,
    ScanProvenance,
    SecurityResult,
    compute_security_verdict,
)

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

# Longest exception text interpolated into the one-line WARNING below. The
# scanner wrappers embed raw subprocess stderr in their messages -- semgrep_tool
# raises f"Semgrep failed with exit code {rc}: {result.stderr.strip()}" -- so
# str(exc) is only as bounded as the CLI is talkative. A chatty 50KB stderr
# renders a 50,000-character, 2,500-line "one line". 200 chars is enough to
# carry the exit code and the first sentence of the real error, which is what
# tells you which scanner broke and roughly why; the rest is at DEBUG.
MAX_LOG_DETAIL_CHARS = 200


def _one_line(text: str, limit: int = MAX_LOG_DETAIL_CHARS) -> str:
    """Collapse to a single bounded line, marking any truncation.

    Both halves are load-bearing. Capping length alone does not guarantee one
    line -- stderr can put newlines inside the first 200 characters -- and
    collapsing newlines alone does not bound length. The marker names the full
    size so nobody mistakes a truncated message for the whole error.
    """
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit]}... [{len(text)} chars total, full text at DEBUG]"


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


def _with_provenance(result: SecurityResult, provenance: ScanProvenance) -> SecurityResult:
    """Stamp WHERE this verdict came from, without touching the verdict itself.

    A copy rather than an in-place set, because the two fixture paths get their
    result from `fixtures_loader.security`, which returns a freshly validated
    model each call -- but mutating whatever a loader hands back is the habit
    that turns a shared fixture into shared mutable state the first time one is
    cached. `update=` cannot reach `verdict`, `findings` or `blocking` from
    here, so this cannot become a way to change a decision.

    This exists because the fixture paths and the scanner path build their
    results in three different places, and provenance that each of them sets by
    hand is provenance one of them will eventually forget to set.
    """
    return result.model_copy(update={"scan_provenance": provenance})


def run(state: RunState, use_real_scanners: bool = True) -> SecurityResult:
    """Scan the diff, decide in code, then attach an explanation.

    Every return carries `scan_provenance`, and that is the whole reason this
    function has three of them worth telling apart. The verdict alone does not
    say whether the scanners ran: a raise from Habiba's lane is answered with
    the FIXTURE verdict below, which still blocks a poisoned diff, so "block"
    on a machine with no scanners installed and "block" from real gitleaks were
    indistinguishable on disk. graph.py writes this value into the run's log
    row, and agentorg/timeline.py renders it. See state.ScanProvenance.
    """
    if not use_real_scanners:
        # STUB path, kept for demos that must not shell out at all. A CHOICE,
        # not a fault -- distinct from the fallback below, which is a fault.
        return _with_provenance(
            fixtures_loader.security(block=_looks_poisoned(state)), "fixture-stub"
        )

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
        # One bounded line at WARNING naming the cause, everything else at
        # DEBUG. During the demo this is a projector line, and a wall of text
        # immediately above `status=blocked` reads as a crash. The exception
        # text is passed through _one_line because str(exc) is unbounded -- see
        # MAX_LOG_DETAIL_CHARS. The DEBUG record keeps the full message: it is
        # rendered from exc_info, so nothing is dropped, only demoted. Nothing
        # here can reach the verdict; logging cannot affect control flow.
        logging.getLogger(__name__).warning(
            "scanners failed (%s: %s); falling back to the fixture verdict",
            type(exc).__name__,
            _one_line(str(exc)),
        )
        logging.getLogger(__name__).debug("scanner failure traceback", exc_info=True)
        # Fall back to the FIXTURE, never to an empty findings list -- see the
        # module docstring. This still blocks a diff carrying an AWS key.
        #
        # Stamped "fixture-fallback": the WARNING above goes to the Python
        # logger, which is not the run's artifact. Without this stamp the only
        # record that no scanner ran was a stderr line nobody reads back, and
        # the log row said "blocked" exactly as a real scan would.
        return _with_provenance(
            fixtures_loader.security(block=_looks_poisoned(state)), "fixture-fallback"
        )

    verdict, blocking = compute_security_verdict(
        findings, threshold=config.SECURITY_BLOCK_THRESHOLD
    )
    return SecurityResult(
        verdict=verdict,
        findings=findings,
        blocking=blocking,
        explanation=_explain(verdict, blocking),
        # The only path on which compute_security_verdict actually ran.
        scan_provenance="scanners",
    )
