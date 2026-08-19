"""The fail-safe scanner runner: a scanner fault becomes a BLOCKING finding.

OWNER: Habiba (agentorg/security/).

WHY THIS MODULE EXISTS

    `compute_security_verdict([])` returns `("pass", [])`. That single fact is
    the reason this file is here. It means any path that turns a broken scanner
    into an empty findings list reports a poisoned change as clean -- and does
    so while every test in the suite stays green, because "no findings" is
    exactly what a genuinely clean scan looks like. This project has closed that
    same failure three separate times. It is called failing OPEN: the gate that
    exists to stop bad code passes it, precisely because the gate did not run.

    The two pieces below are the fix, and they split the problem in half:

      * `safe_run` makes a fault OBSERVABLE instead of fatal. A missing binary,
        a hang, an unreadable executable -- all become `None`, one value the
        caller must handle, rather than four exception types the caller has to
        know about in advance.
      * `error_finding` makes the fault BLOCKING. A fault becomes a Finding at
        the block threshold, so the fan-out's answer to "a scanner died" is a
        non-empty findings list that fails the gate. Never `[]`.

    Neither one decides pass/block. `compute_security_verdict` in state.py does,
    as it does for every other finding. `error_finding` reaches the verdict the
    same way a real gitleaks hit does, through severity alone -- so there is no
    second, parallel decision path to keep in sync with the first.

WHAT DOES NOT BELONG HERE

    Deciding whether a given `None` is a fault at all. Per the plan's central
    ruling, a binary that is merely ABSENT is a development and CI affordance
    that keeps the existing fixture-fallback path, while a binary that is
    present and BROKEN is a fault -- and `config.SCANNERS_REQUIRED` promotes the
    former to the latter. `safe_run` reports; the per-tool wrappers judge.
"""

import logging
import subprocess

from ..state import Finding

# `line=0` on an error finding: there is no source line, because the failure is
# in the scanner, not in the diff. Zero is what the wrappers already use for a
# report entry with no line, so the renderers handle it -- see gitleaks_tool's
# `int(leak.get("StartLine", 0) or 0)`.
_NO_SOURCE_LINE = 0

# `file` names the tool rather than a path, for the same reason. This string is
# rendered by agents/security.py's _default_explanation as "{file}:{line}", so it
# appears on the projector and in the PR comment as e.g.
# "gitleaks:gitleaks-scanner-error (high) in <gitleaks scanner>:0" -- which reads
# as a tooling failure and not as a finding about the developer's code.
_SCANNER_PSEUDO_FILE = "<{tool} scanner>"

# How much of a failure reason survives into the Finding description. The reasons
# passed in carry raw subprocess stderr, which is only as bounded as the CLI is
# talkative -- semgrep can emit tens of kilobytes. This description reaches a PR
# comment and the demo screen, so it is capped for the same reason
# agents/security.py caps its log line at MAX_LOG_DETAIL_CHARS. 300 leaves room
# for an exit code and the first sentence or two of the real error, which is what
# identifies the fault.
MAX_REASON_CHARS = 300


def _one_line(text: str, limit: int = MAX_REASON_CHARS) -> str:
    """Collapse to a single bounded line, marking any truncation.

    Both halves matter: capping length alone does not guarantee one line, since
    stderr can put newlines inside the first 300 characters, and collapsing
    newlines alone does not bound length. Mirrors the helper in
    agents/security.py deliberately -- the two are the same requirement applied
    to the two places unbounded CLI output escapes into rendered prose.
    """
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit]}... [{len(text)} chars total]"


def error_finding(tool: str, reason: str) -> Finding:
    """A scanner fault, as a finding that BLOCKS.

    THE SEVERITY IS THE ENTIRE POINT. `"high"` is `config`'s block threshold, so
    `compute_security_verdict` blocks on one of these alone. Lower it to
    `"medium"` and this lane silently reverts to failing open -- the finding
    would still be produced and still be logged, the verdict would read "pass",
    and a change would be promoted on the word of a scanner that never ran.
    Nothing but `tests/test_scanner_resilience.py` would notice, which is why
    that test asserts the literal severity AND the computed verdict.

    It is deliberately not `"critical"`. Critical is what gitleaks reports for a
    committed credential -- an actual vulnerability in the change. A dead
    scanner is a tooling failure: it must block just as hard, but it should not
    impersonate a discovered secret in a list a human is reading.
    """
    return Finding(
        tool=tool,
        severity="high",
        rule=f"{tool}-scanner-error",
        file=_SCANNER_PSEUDO_FILE.format(tool=tool),
        line=_NO_SOURCE_LINE,
        description=f"{tool} could not be run, so this change was NOT scanned: "
        f"{_one_line(reason)}",
    )


def safe_run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess | None:
    """Run a scanner. Return its result, or `None` if it could not run at all.

    Returns `None` -- never raises -- when the command cannot produce a result:
    the binary is missing, it hangs past `timeout`, or the OS refuses to execute
    it. Every other outcome, including a non-zero exit, comes back as a real
    `CompletedProcess`.

    THE NON-ZERO EXIT IS NOT A FAILURE, and `check=False` is contract rather
    than detail. gitleaks exits 1 when it finds secrets and semgrep exits 1 when
    it finds matches -- the poisoned demo depends on both. Treating non-zero as
    "could not run" would replace two real critical findings with a
    scanner-error and take scripts/scan_gate.py's exact expected-findings pins
    red. Judging WHICH exit codes are acceptable stays in each wrapper, where
    the per-tool knowledge already lives.

    THE EXCEPTION SET IS MEASURED, NOT ASSUMED (CPython 3.14.6):

      * missing binary            -> FileNotFoundError, an OSError subclass
      * present but not runnable  -> PermissionError ([Errno 13]), also OSError
      * hang past the timeout     -> subprocess.TimeoutExpired, a SubprocessError
        and NOT an OSError -- so it needs its own clause. A handler catching only
        OSError, the obvious guess after the missing-binary case, lets every
        timeout escape.
      * malformed argv (e.g. [])  -> IndexError, in neither hierarchy

    That last one is why the final clause is broad. The caller must be able to
    treat `None` as the whole failure surface; a narrow clause is correct only
    on the day it is written, and its failure mode is the pipeline crashing at
    the gate that exists to stop bad code. Note that ruff will NOT catch that
    regression -- a narrowed `except` with no logging is BLE001-clean, so lint
    blesses the more dangerous option. Ordered specific-first so the log line
    names the real fault instead of a generic one.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # WARNING, not DEBUG: a hang is the fault most likely to be a real
        # problem in the environment rather than a missing dev tool, and under
        # SCANNERS_REQUIRED it is what blocks the run.
        logging.getLogger(__name__).warning(
            "scanner command %r exceeded its %ss timeout; treating it as unrunnable",
            cmd[0] if cmd else cmd,
            timeout,
        )
        return None
    except FileNotFoundError:
        # The ordinary no-binaries-installed case, which is how CI's `test` job
        # runs on purpose, so this is DEBUG. It is not silent: the caller turns
        # it into either the fixture-fallback path or an error_finding, and both
        # of those are loud.
        logging.getLogger(__name__).debug(
            "scanner binary %r is not on PATH", cmd[0] if cmd else cmd
        )
        return None
    except OSError:
        # Installed but unrunnable -- a lost +x bit, a noexec mount, a name that
        # resolves to a directory. Distinct from absent, and a real fault.
        logging.getLogger(__name__).warning(
            "scanner command %r could not be executed", cmd[0] if cmd else cmd,
            exc_info=True,
        )
        return None
    except Exception:
        # Deliberately broad; see the docstring. Whatever it was, the scanner
        # produced no result, and that is the only thing the caller can act on.
        # exc_info carries the full traceback so nothing is lost by returning a
        # value instead of propagating.
        logging.getLogger(__name__).warning(
            "scanner command %r failed unexpectedly; treating it as unrunnable",
            cmd[0] if cmd else cmd,
            exc_info=True,
        )
        return None
