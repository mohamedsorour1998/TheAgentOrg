"""Security scanner lane.

OWNER: Habiba.

Three wrappers — semgrep, gitleaks, trivy — each take the developer's diff/files
and return a list of Finding (the shape frozen in state.py). run_all_scanners()
fans out to all three and concatenates. The security agent then applies the
deterministic block rule; you do NOT decide pass/block here — you only produce
findings.

This lane is self-contained: it depends only on state.py, the scanner CLIs, and
common/diff.py — the one materialiser that decides what "this change contains"
means, shared with the developer's poisoned safety net so the two cannot
disagree about it again. It never imports the graph, so you can build and test
it in isolation.

THE FAN-OUT MEMOISES, AND WHAT IT REFUSES TO REMEMBER IS THE POINT

    A repeat of the same diff costs nothing: `_CACHE` maps sha256 of the full
    diff text to the findings the three wrappers produced for it. The demo runs
    the same two fixtures over and over, and trivy alone can spend a minute on
    a cold vulnerability database, so a re-scan on stage is dead projector time.

    But a cache over a GATE is not ordinary memoisation, because two of the
    three things the fan-out can hand back must NOT be stored:

      * A FAULT. `_run.error_finding` produces a blocking finding whose only
        claim is "this scanner did not run" -- a timeout, an unreadable report,
        a lost `+x` bit. Store that and one transient failure answers every
        later scan of that diff for the life of the process, including the
        demo's next repeat, on a machine where the scanner is now healthy. So a
        result containing ANY fault is returned and dropped. `_is_fault_free`
        below is the test, and it is `all`, not `any`: two clean scanners and
        one dead one is still an answer that must be retried.
      * A RAISE. The absent-scanner path signals absence by raising
        (`_run.unrunnable_findings`), and MEASURED over this suite at 1171470,
        116 of 121 fan-out calls do exactly that -- semgrep is first and no
        binary is installed in CI's `test` job. Nothing is stored on that path,
        and in particular the exception is NOT stored to be re-raised: a
        replayed raise is a memoised fault wearing a different hat, and it would
        keep reporting "semgrep is not installed" after semgrep was installed.
        The `try` below therefore has no `except` -- the raise propagates and the
        cache line is simply never reached. agents/security.py catches it and
        falls back to the fixture verdict, unchanged.

    CONSEQUENCE WORTH KNOWING BEFORE YOU MEASURE THIS: because 116 calls raise
    and the remaining few return only faults, a CORRECT cache leaves the shipped
    suite's wrapper-invocation count exactly where it was -- measured 129 at
    1171470, unchanged after this landed. A measurement that shows that number
    DROPPING has found the defect this docstring is about, not a working cache.
    The cache can only be demonstrated against wrappers that return clean
    findings; see tests/test_scanner_resilience.py's Task 4 section.

    WHY A COPY GOES OUT AND A COPY GOES IN. `findings` is handed straight to
    `compute_security_verdict`, which reads `severity` and nothing else. Hand a
    caller the cached list and one `finding.severity = "low"` -- or one `del` --
    silently rewrites what every later caller sees, which is a fail-open with no
    scanner involved. The copy is DEEP for the same reason: copying the list
    alone still shares the Finding objects, and severity lives on the object.

    NO EVICTION, DELIBERATELY. One entry per distinct diff, and the process
    lifetimes here are a pipeline run, a pytest session, and a demo -- bounded
    at six distinct diffs across the whole suite (measured). A long-lived server
    would need a cap; nothing in this repository is one, and an LRU would add a
    second thing to get wrong beside the fault rule. `reset_scanner_cache()`
    exists so the suite can clear it between tests, and is the hook a
    long-running caller would use per run.
"""

import hashlib

from ..state import DevResult, Finding
from ._run import error_finding
from .gitleaks_tool import scan as _gitleaks
from .semgrep_tool import scan as _semgrep
from .trivy_tool import scan as _trivy

# sha256(full diff text) -> the findings the fan-out produced for it. Only
# fault-free results are ever stored; see the module docstring.
_CACHE: dict[str, list[Finding]] = {}

# Every rule `error_finding` can produce, derived from the function itself rather
# than from a hand-written list of three strings. This is what "is this result a
# fault?" is decided against, and deriving it means a fourth tool added to
# `ScannerTool` cannot leave a fault silently cacheable.
#
# WHY THIS AND NOT A CLEANER SIGNAL: there isn't one. `error_finding` returns a
# plain `Finding`, the shape frozen in state.py, carrying no marker field -- and
# adding one would mean touching a frozen model to serve a cache. Severity is
# not it either: `high` is what a real semgrep hit reports, so `severity ==
# "high"` would refuse to cache genuine findings. The `rule` string is the only
# thing that distinguishes a fault from a finding, and building the set by
# CALLING error_finding keeps this in step with it by construction instead of by
# a comment asking the next author to remember.
_FAULT_RULES = frozenset(
    error_finding(tool, "").rule for tool in ("semgrep", "gitleaks", "trivy")
)


def _diff_key(dev: DevResult) -> str:
    """sha256 of the FULL diff text -- the whole question a scan answers.

    The full text, not `common/diff.py`'s materialised added lines, even though
    the added lines are all a scanner ever reads. Two diffs with identical `+`
    lines and different removals would share a key, and this key would then be
    claiming they are the same scan on the strength of a second module's rules
    about what counts as "in this change". Hashing the input the fan-out was
    actually handed keeps the cache's notion of sameness independent of that.

    `or ""` because `diff` is typed `str` but the None-safe reading is what
    `added_files` and `_looks_poisoned` both already do, and a cache is the
    wrong place to be the first thing that raises on it.
    """
    return hashlib.sha256((dev.diff or "").encode("utf-8")).hexdigest()


def _is_fault_free(findings: list[Finding]) -> bool:
    """True when NO finding is a scanner fault, so this result may be stored.

    `all`, not `any`. The realistic failure is one dead scanner among three, and
    that answer is two-thirds worth keeping -- which is exactly why it must not
    be kept. Caching it pins one tool's transient timeout to this diff for the
    life of the process while the findings list still looks mostly correct.
    """
    return all(finding.rule not in _FAULT_RULES for finding in findings)


def _copy(findings: list[Finding]) -> list[Finding]:
    """A deep copy, so cache and caller share no mutable object.

    Deep because a shallow list copy still shares the Finding objects, and
    `severity` -- the one field `compute_security_verdict` reads -- is settable
    on a pydantic model. Used on the way IN as well as OUT: the list the
    wrappers built is the caller's too on a miss.
    """
    return [finding.model_copy(deep=True) for finding in findings]


def reset_scanner_cache() -> None:
    """Forget every memoised scan. Public, and the test suite depends on it.

    A process-lifetime cache is shared across tests, so without this one test's
    stubbed wrappers answer the next test's scan of the same diff -- and the
    diffs here are shared fixtures, so that collision is the default rather than
    an unlucky case. tests/test_scanner_resilience.py calls this from an autouse
    fixture. It lives in this module rather than in the test file because
    reaching into `_CACHE` from a test would pin the cache's private shape.
    """
    _CACHE.clear()


def run_all_scanners(dev: DevResult | None) -> list[Finding]:
    """Run all three scanners over the developer's change; return all findings.

    Memoised on sha256 of the diff. See the module docstring for what is
    deliberately NOT stored -- a fault, and a raise -- and why storing either
    would be a fail-open.
    """
    if dev is None:
        # No diff, so no key, and nothing to save by remembering: this path
        # scans nothing. Giving it the empty-diff key would be worse than
        # useless -- `None` and a DevResult whose diff is "" are different
        # questions, and an empty diff is one that must still be SCANNED. A
        # fresh list each time, because a shared `[]` handed to every caller is
        # a mutable object they all hold.
        return []

    key = _diff_key(dev)
    cached = _CACHE.get(key)
    if cached is not None:
        return _copy(cached)

    findings: list[Finding] = []
    # No `except` here on purpose. An absent scanner raises, and that raise must
    # reach agents/security.py untouched AND leave no trace here -- see the
    # module docstring on why a stored exception is a memoised fault.
    for scan in (_semgrep, _gitleaks, _trivy):
        findings.extend(scan(dev))

    if _is_fault_free(findings):
        _CACHE[key] = _copy(findings)
    return findings
