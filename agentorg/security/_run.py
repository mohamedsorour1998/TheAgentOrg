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

ABSENT IS NOT BROKEN, AND THE OBVIOUS TEST FOR IT IS WRONG

    Per the plan's central ruling, a binary that is merely ABSENT is a
    development and CI affordance that keeps the existing fixture-fallback path,
    while one that is present and BROKEN is a fault that must block. `safe_run`
    returns `None` for both -- it reports, the per-tool wrappers judge -- so it
    also has to say WHICH, or the wrapper is left guessing. `classify_failure`
    is that answer, and it exists because both single-signal answers are wrong.

    MEASURED on CPython 3.14.6, five fault modes against two candidate
    discriminators:

    | fault                      | truth  | `shutil.which` alone | exception alone |
    |----------------------------|--------|----------------------|-----------------|
    | binary absent from PATH    | ABSENT | ABSENT  correct      | ABSENT  correct |
    | real file, `+x` bit gone   | FAULT  | ABSENT  WRONG        | FAULT   correct |
    | argv0 is a directory       | FAULT  | ABSENT  WRONG        | FAULT   correct |
    | on PATH, broken shebang    | FAULT  | FAULT   correct      | ABSENT  WRONG   |
    | malformed argv (`[]`)      | FAULT  | n/a                  | FAULT   correct |

    `shutil.which` alone misreads the two cases the ruling names by name -- "a
    lost +x bit, a noexec mount" -- because `which` requires the `+x` bit it is
    being asked about, and a directory is simply not on PATH. Both would take
    the fixture-fallback path and fail OPEN under `SCANNERS_REQUIRED=true`,
    inverting the knob's entire purpose.

    The exception type alone misreads the fourth: a scanner sitting on PATH with
    an unresolvable interpreter raises `FileNotFoundError` -- errno 2 names the
    missing INTERPRETER, not the scanner -- so a handler reading the exception
    type would call an installed-but-broken scanner "absent".

    So the discriminator is the CONJUNCTION, and needs both halves:
    ABSENT iff the failure was `FileNotFoundError` AND `shutil.which(argv0)`
    finds nothing. Everything else that failed is a FAULT. MEASURED: this
    classifies all five rows above correctly.

WHAT DOES NOT BELONG HERE

    The `SCANNERS_REQUIRED` decision itself. This module reports absent vs
    fault; whether an ABSENT scanner is nonetheless treated as a fault is the
    wrapper's call in Task 3, because that is where the fixture-fallback path
    is chosen.
"""

import logging
import shutil
import subprocess
from typing import Literal

from ..state import Finding

# The tools the fan-out knows about. Mirrors Finding.tool's Literal in state.py
# (FROZEN, so this cannot import a name from there that does not exist) and is
# declared so a mistyped tool name is an authoring-time error. Without it the
# mistake surfaces as a pydantic ValidationError raised from error_finding --
# i.e. a crash on the fault path, at the exact moment the pipeline is trying to
# report that a scanner failed.
ScannerTool = Literal["semgrep", "gitleaks", "trivy"]

# What `classify_failure` answers. "absent" is the development/CI affordance
# that keeps the fixture-fallback path; "fault" must become a blocking
# error_finding. See the module docstring for why neither shutil.which nor the
# exception type can produce this on its own.
FailureKind = Literal["absent", "fault"]

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


def error_finding(tool: ScannerTool, reason: str) -> Finding:
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


def _note(observed: list[FailureKind] | None, kind: FailureKind) -> None:
    """Record what a failure looked like, for run_scanner. Never raises."""
    if observed is not None:
        observed.append(kind)


def _argv0(cmd: list[str]) -> str | None:
    """The binary a command names, or None if it names nothing.

    `cmd` reaching here malformed is the whole reason safe_run has a broad
    handler, so this must not assume a non-empty list of strings. Measured:
    `[]` raises IndexError and `[None]` raises TypeError inside subprocess, and
    both must classify as a FAULT rather than crashing the classifier too.
    """
    if not cmd:
        return None
    first = cmd[0]
    return first if isinstance(first, str) else None


def classify_failure(
    cmd: list[str], *, kind_hint: FailureKind | None = None
) -> FailureKind:
    """Was this command ABSENT, or present and BROKEN?

    Answers the question Task 3's wrappers must ask about every `None` from
    `safe_run`, per the plan's central ruling: an absent binary keeps the
    fixture-fallback path, a broken one becomes a blocking `error_finding`.

    `kind_hint` is what `safe_run` observed from the exception type -- pass it
    through when you have it. It is the half of the discriminator that
    `shutil.which` cannot supply, and vice versa; see the module docstring's
    measured table for the two fault modes each half gets wrong on its own.
    Called without a hint, this can only consult the filesystem, so it will
    report a present-but-broken binary as "absent" -- which is why the wrappers
    should always pass what safe_run told them.

    Defaults to "fault" when uncertain. That direction is deliberate: guessing
    "fault" on a genuinely absent binary makes CI noisy and is caught by the
    five `len(blocking) == 2` assertions immediately, whereas guessing "absent"
    on a real fault fails OPEN and is caught by nothing.
    """
    if kind_hint == "fault":
        return "fault"

    argv0 = _argv0(cmd)
    if argv0 is None:
        # Malformed argv: nothing was ever going to run, and that is a defect in
        # the caller rather than a missing tool.
        return "fault"

    # The conjunction. A FileNotFoundError whose binary `which` also cannot find
    # is the ordinary "not installed" case; a FileNotFoundError for a binary that
    # IS on PATH means the errno-2 came from something the binary needs (a
    # missing interpreter behind its shebang), which is a broken install.
    return "absent" if shutil.which(argv0) is None else "fault"


def run_scanner(
    cmd: list[str], *, timeout: int
) -> tuple[subprocess.CompletedProcess | None, FailureKind | None]:
    """`safe_run` plus the absent-vs-fault verdict. This is what Task 3 wants.

    Returns `(result, None)` when the command ran -- any exit code -- and
    `(None, kind)` when it did not, where `kind` is "absent" or "fault" already
    classified with BOTH halves of the discriminator.

    It exists so a wrapper cannot forget to pass `safe_run`'s observation into
    `classify_failure`. Forgetting is not a loud mistake: the classifier still
    returns an answer, and the answer is "absent" for a broken-shebang scanner,
    which under SCANNERS_REQUIRED fails OPEN. A single call site removes the
    opportunity.
    """
    observed: list[FailureKind] = []
    result = safe_run(cmd, timeout=timeout, _observed=observed)
    if result is not None:
        return result, None
    hint = observed[0] if observed else None
    return None, classify_failure(cmd, kind_hint=hint)


def safe_run(
    cmd: list[str],
    *,
    timeout: int,
    _observed: list[FailureKind] | None = None,
) -> subprocess.CompletedProcess | None:
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

    `_observed` is a private out-parameter for `run_scanner`: the handler appends
    what the exception type implied, so the absent-vs-fault classification does
    not need a second copy of these clauses. Two copies would drift, and the one
    that drifted would be the one Task 3 calls. Callers pass `timeout` only; the
    public signature is unchanged.
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
            _argv0(cmd) or cmd,
            timeout,
        )
        _note(_observed, "fault")
        return None
    except FileNotFoundError:
        # The ordinary no-binaries-installed case, which is how CI's `test` job
        # runs on purpose, so this is DEBUG. It is not silent: the caller turns
        # it into either the fixture-fallback path or an error_finding, and both
        # of those are loud.
        logging.getLogger(__name__).debug(
            "scanner binary %r is not on PATH", _argv0(cmd) or cmd
        )
        _note(_observed, "absent")
        return None
    except OSError:
        # Installed but unrunnable -- a lost +x bit, a noexec mount, a name that
        # resolves to a directory. Distinct from absent, and a real fault.
        logging.getLogger(__name__).warning(
            "scanner command %r could not be executed", _argv0(cmd) or cmd,
            exc_info=True,
        )
        _note(_observed, "fault")
        return None
    except Exception:
        # Deliberately broad; see the docstring. Whatever it was, the scanner
        # produced no result, and that is the only thing the caller can act on.
        # exc_info carries the full traceback so nothing is lost by returning a
        # value instead of propagating.
        logging.getLogger(__name__).warning(
            "scanner command %r failed unexpectedly; treating it as unrunnable",
            _argv0(cmd) or cmd,
            exc_info=True,
        )
        _note(_observed, "fault")
        return None
