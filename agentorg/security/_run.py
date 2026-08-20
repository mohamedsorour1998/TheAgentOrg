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

WHERE THE `SCANNERS_REQUIRED` DECISION LIVES, AND WHY IT MOVED HERE

    An earlier version of this section said the knob's decision did NOT belong
    in this module -- that each wrapper would make it, "because that is where
    the fixture-fallback path is chosen". Task 3 then wrote the three wrappers
    and measured what that costs: three byte-identical copies of one
    security-relevant fork, in three files owned as one lane. This repository
    has already paid that bill once, in `common/diff.py`, where four private
    copies of "what does this change contain?" drifted until the poisoned demo
    stopped blocking (measured: it blocked on 2 of 5 live runs). Duplicating
    the absent-vs-required fork invites exactly that shape of failure, and the
    copy that drifts is by definition the one nobody noticed.

    So `unrunnable_findings` below owns it, once. The wrappers still choose
    WHETHER to call it -- they alone know a `None` from `run_scanner` is what
    happened -- but not what it means.

THE FIELD READERS COVER TODAY'S DEREFERENCES, AND NOTHING ENFORCES THAT

    READ THIS BEFORE ADDING A FIELD READ TO ANY WRAPPER'S PARSE LOOP. The
    `report_text` / `report_int` / `report_mapping` / `report_objects` group
    below exists because a report can parse as valid JSON, satisfy every
    top-level shape guard, and still carry a wrong-typed INNER field that crashes
    the loop on dereference. MEASURED, 9 of 9 such cases raised before these
    readers existed -- ValueError, TypeError, AttributeError, pydantic
    ValidationError -- and end to end that was a FAIL-OPEN: on a clean diff with
    all three scanners installed and misbehaving, `security.run` returned
    `verdict=pass, blocking=0`, because the crash reached agents/security.py's
    fixture fallback and the fixture verdict for a clean diff is "pass".

    The readers cover every field the three loops read TODAY -- MEASURED by an AST
    walk, 17 call sites over 16 distinct keys: gitleaks 4, semgrep 7, trivy 6.
    (16 rather than 17 because `Description` is read by both gitleaks and trivy.)
    The per-tool split is given so the next reader can check the claim against the
    files instead of trusting the total; the same AST walk found zero bare
    `.get()` calls and zero subscripts inside the guarded parse loops, which is
    what "every field" means here.

    THAT COUNT WAS WRONG IN THE FIRST VERSION OF THIS SECTION, and the way it was
    wrong is worth more than the correction. It said "eleven", which is
    gitleaks' 4 plus semgrep's 7 with trivy's 6 silently omitted -- and eleven is
    also, coincidentally, the exact number of cases in
    `test_wrong_typed_inner_fields_block_rather_than_crashing`'s table, so the
    figure looked corroborated by a real number nearby. It was never measured. It
    appeared in the same commit that fixed a different unmeasured count elsewhere
    in this lane, inside the paragraph below headed READ THIS BEFORE ADDING A
    FIELD READ -- i.e. the sentence most likely to be copied forward. If you
    change these readers, re-measure rather than adjusting the number by hand.

    The readers are not a schema and they are not applied automatically. A new
    field read written as a bare `container.get("Whatever")` reintroduces the same
    crash class, in the same fail-open direction, and:

      * ruff will NOT catch it -- a `.get()` is unremarkable code;
      * no test will catch it either, unless someone writes the wrong-typed case
        for that specific field;
      * the crash surfaces at the security gate, which is the worst place for it.

    The only structural hint is the `except ReportShapeError` block wrapping each
    wrapper's parse loop. If you add a field read, add it through a reader.

    AND THE READERS THEMSELVES WERE MUTATION-TESTED, because one of them was
    unpinned on first writing: `report_int`'s final fall-through raise -- the
    branch for a value that is neither int, bool, nor str -- was reached by NO
    test. Replacing it with `return default` left the entire suite GREEN at 175
    passed. It was found by mutating the code, not by reading it, in the very
    round whose purpose was closing this crash class. Two cases were added for
    it. Treat any change to these readers the same way: mutate the branch and
    confirm a test goes red, because a silently-defaulting reader is
    indistinguishable from a working one until a scanner misbehaves.
"""

import logging
import shutil
import subprocess
from typing import Literal

from ..common import config
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


class ReportShapeError(Exception):
    """A scanner report parsed, but a field the wrapper reads has the wrong type.

    Raised by the `report_*` readers below and caught by each wrapper's parse
    loop, which converts it to a blocking `error_finding`. A dedicated exception
    rather than a sentinel return, because the loops dereference these values
    immediately: a sentinel would have to be checked at every one of the 17 call
    sites (measured; see the module docstring for the per-tool split), and the one
    site that forgot would crash exactly as before.

    NOT a subclass of ValueError or TypeError. The wrappers must catch THIS and
    not the crash it replaces, or a genuine bug in the mapping code would be
    silently reported as a scanner fault -- which is the fail-CLOSED direction,
    but it would also make a real defect look like someone else's broken binary.
    """


def report_text(container: dict, key: str, default: str) -> str:
    """Read a string field from a scanner report, or reject the report.

    WHY THE TOP-LEVEL SHAPE GUARDS ARE NOT ENOUGH -- MEASURED, 9 of 9 cases.
    Each wrapper checks that its report is a list-of-objects (gitleaks) or an
    object whose results key is a list-of-objects (semgrep, trivy). Every one of
    those guards passes for a report whose INNER fields are wrong-typed, and the
    parse loop then crashes on the dereference:

      | report                                      | crash                     |
      |---------------------------------------------|---------------------------|
      | gitleaks StartLine: "not-an-int"            | ValueError                |
      | gitleaks File: {...}                        | TypeError in os.path      |
      | gitleaks RuleID: [...]                      | pydantic ValidationError  |
      | gitleaks Description: {...}                 | pydantic ValidationError  |
      | semgrep extra: "not-a-dict"                 | AttributeError            |
      | semgrep start: "not-a-dict"                 | AttributeError            |
      | semgrep start.line: "x"                     | ValueError                |
      | trivy Vulnerabilities: ["not-an-object"]    | AttributeError            |
      | trivy Severity: {...}                       | AttributeError in .upper  |

    END TO END that is a FAIL-OPEN, which is why this is not merely tidiness. On
    a CLEAN diff with all three scanners installed and all three emitting
    wrong-typed inner fields, `security.run` measured `verdict=pass, blocking=0`:
    the exception escapes the wrapper, agents/security.py catches it and falls
    back to the fixture verdict, and the fixture verdict for a clean diff is
    "pass". A change was promoted although no scanner output was ever read --
    the same shape this module exists to close, one level deeper than the
    top-level guards reach.

    MITIGATING, and it is why this is a guard rather than an emergency: real
    gitleaks 8.21.2 and semgrep 1.172.0 reports are well-typed at every level
    (measured: StartLine int, File str, extra dict, start.line int, and no
    result missing `extra`). So reaching this needs an already-misbehaving
    scanner -- which is precisely the case the rest of this module assumes.

    A MISSING key is not a fault: every call site passes the default the wrapper
    used before, and a report legitimately omits optional fields. Only a key
    that is PRESENT with an unusable type is rejected. `None` is treated as
    absent for the same reason -- JSON `null` is how these tools spell "no
    value".
    """
    value = container.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ReportShapeError(
            f"field {key!r} was {type(value).__name__}, expected a string"
        )
    return value


def report_int(container: dict, key: str, default: int) -> int:
    """Read an integer field from a scanner report, or reject the report.

    Accepts a JSON number, and a string of digits because gitleaks has shipped
    both for `StartLine` across versions -- `int("12")` is what the wrapper did
    before and that behaviour is preserved. Rejects anything else, including a
    non-numeric string, which used to raise ValueError from inside the loop.

    `bool` is excluded deliberately: it is an `int` subclass in Python, so
    `isinstance(True, int)` is True and a report carrying `"StartLine": true`
    would silently become line 1. That is a wrong-typed field, not a line
    number.
    """
    value = container.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        raise ReportShapeError(f"field {key!r} was a boolean, expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ReportShapeError(
                f"field {key!r} was the non-numeric string {value!r}, "
                f"expected an integer"
            ) from exc
    raise ReportShapeError(
        f"field {key!r} was {type(value).__name__}, expected an integer"
    )


def report_mapping(container: dict, key: str) -> dict:
    """Read a nested object from a scanner report, or reject the report.

    semgrep's `extra` and `start` are read this way. Before this, `extra: "x"`
    made `extra.get(...)` raise AttributeError -- the single most likely of the
    nine measured cases, because `extra` is where semgrep puts severity and
    message and a truncated or streamed report can plausibly mangle it.
    """
    value = container.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ReportShapeError(
            f"field {key!r} was {type(value).__name__}, expected an object"
        )
    return value


def report_objects(container: dict, key: str) -> list[dict]:
    """Read a list-of-objects field from a scanner report, or reject the report.

    trivy's `Vulnerabilities` is read this way: a list containing a bare string
    made `vulnerability.get(...)` raise AttributeError. Checks every element
    rather than just the type of the list, because a report that is mostly
    well-formed with one bad entry is the realistic shape and is exactly the one
    a spot check misses.
    """
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReportShapeError(
            f"field {key!r} was {type(value).__name__}, expected a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ReportShapeError(
                f"field {key!r}[{index}] was {type(item).__name__}, "
                f"expected an object"
            )
    return value


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

    CALLED WITHOUT A HINT THIS DEGRADES, AND IT DEGRADES TOWARDS FAILING OPEN.
    With no hint there is only the filesystem to consult, so the answer is
    whatever `shutil.which` says. MEASURED, hintless, row by row:

      * binary absent from PATH -> "absent"  correct
      * real file, `+x` bit gone -> "absent"  WRONG, this is a FAULT
      * argv0 is a directory     -> "absent"  WRONG, this is a FAULT
      * on PATH, broken shebang  -> "fault"   correct (`which` resolves it)
      * malformed argv           -> "fault"   correct

    The two that leak are precisely the two the plan's ruling names by name -- a
    lost `+x` bit and a noexec mount -- and both leak in the dangerous
    direction: classified "absent", they take the fixture-fallback path and fail
    OPEN under `SCANNERS_REQUIRED=true`. Note which row is NOT a problem here:
    the broken shebang comes out correct hintless, because `which` finds it. So
    "we don't run scanners behind shebangs, the hintless path is safe for us" is
    exactly the wrong conclusion to draw -- it reasons about the one row that
    works. Pass the hint, or call `run_scanner`, which cannot forget to.
    `test_classify_failure_without_a_hint_degrades_exactly_where_documented`
    pins every row above.

    Defaults to "fault" when uncertain. That direction is deliberate: guessing
    "fault" on a genuinely absent binary makes CI noisy and is caught by the
    four fallback-dependent `len(blocking) == 2` assertions immediately (of eight
    such assertions in the suite -- see config.SCANNERS_REQUIRED for both
    measured counts), whereas guessing "absent" on a real fault fails OPEN and is
    caught by nothing.
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
    returns a plausible answer, and MEASURED, that answer is "absent" for the two
    fault modes the ruling names by name -- a scanner whose `+x` bit is gone, and
    an argv0 that resolves to a directory. Both then take the fixture-fallback
    path and fail OPEN under SCANNERS_REQUIRED. A single call site removes the
    opportunity; see `classify_failure`'s docstring for the full hintless table.
    """
    observed: list[FailureKind] = []
    result = safe_run(cmd, timeout=timeout, _observed=observed)
    if result is not None:
        return result, None
    hint = observed[0] if observed else None
    return None, classify_failure(cmd, kind_hint=hint)


def unrunnable_findings(
    tool: ScannerTool, kind: FailureKind | None, reason: str
) -> list[Finding]:
    """The RULING, in one place: what a wrapper does when its scanner did not run.

    Returns a list that is NEVER EMPTY -- `[error_finding(tool, reason)]` -- or
    RAISES `FileNotFoundError`. There is no third outcome and in particular no
    `[]`, which is the whole reason this is a function rather than three copies
    of an `if` in the three wrappers.

      * `kind == "fault"` -> a blocking finding. The scanner is installed and
        broken: a timeout, an OS error, a non-zero exit, an unreadable report.
      * `kind == "absent"` with `SCANNERS_REQUIRED` false -> RAISES, which is the
        pre-existing behaviour this must not change. agents/security.py catches
        it and falls back to the FIXTURE verdict, which still blocks the poisoned
        diff on its two AWS-key findings. Eight assertions across the suite read
        `len(blocking) == 2`, four of them dependent on this path -- both counts
        measured; see config.SCANNERS_REQUIRED, which carries the site list.
      * `kind == "absent"` with `SCANNERS_REQUIRED` true -> a blocking finding.
        The knob promotes absent to fault for the demo machine and production
        images, where an uninstalled scanner is a real defect.

    WHY IT RAISES INSTEAD OF RETURNING `[]` FOR THE THIRD CASE, which is the
    obvious shape and was the first draft. `compute_security_verdict([])` returns
    `("pass", [])`. So an `[]` returned from here is one careless `return` in one
    wrapper away from being the silent-pass bug this lane has now closed four
    times -- and that `return` would be invisible in review, because returning
    the value a helper handed you is what correct code looks like. Raising means
    no call site can produce an empty findings list even by accident: the shape
    does not exist to be returned. The cost is a function that both returns and
    raises, which is worth saying out loud, and is why this docstring leads with
    it.

    WHY THE KNOB IS READ THROUGH THE MODULE, not imported as a bare name. The
    suite flips it with `monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)`,
    which rebinds the module attribute. `from ..common.config import
    SCANNERS_REQUIRED` would bind the value at import time -- before any fixture
    runs -- so the knob would silently ignore both the tests and the demo
    machine's environment. tests/conftest.py's header makes the same point about
    `config.LLM_DISABLED`; this is that trap in this lane.

    `kind` accepts None so a wrapper can hand over whatever `run_scanner`
    returned without a narrowing dance. None means the command RAN, so a caller
    reaching here with it has confused a bad exit code for a failure to launch;
    treated as a fault, because that direction is caught by a red test and the
    other direction fails open.
    """
    if kind == "absent" and not config.SCANNERS_REQUIRED:
        raise FileNotFoundError(
            f"{tool} is not installed, so this change was NOT scanned by it. "
            f"Set SCANNERS_REQUIRED=true to make that a blocking finding "
            f"instead of a fixture fallback. Detail: {_one_line(reason)}"
        )

    if kind == "absent":
        return [
            error_finding(
                tool,
                f"SCANNERS_REQUIRED is set and {tool} is not installed: {reason}",
            )
        ]

    return [error_finding(tool, reason)]


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
