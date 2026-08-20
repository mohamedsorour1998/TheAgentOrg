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

THE FAN-OUT ALSO ORDERS, BECAUSE THE RENDERED GATE HAS TO READ THE SAME TWICE

    No scanner orders its report, and this function concatenates, so without a
    sort the findings list carries whatever order the tools happened to emit.
    That order is not cosmetic: `compute_security_verdict` builds `blocking` by
    comprehension over `findings`, and `agents/security._default_explanation`
    joins `blocking` into the one line that reaches the PR comment and the
    projector. Measured across ten runs of the poisoned fixture with real
    gitleaks, the explanation led with `aws-secret-access-key` six times and
    `aws-access-key-id` four -- same verdict, same count, same finding set, and a
    different sentence on stage each time. `_sort_key` below is the fix, and it is
    applied once across all three tools rather than inside each wrapper.

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
        117 of 121 fan-out calls do exactly that -- semgrep is first and no
        binary is installed in CI's `test` job. Nothing is stored on that path,
        and in particular the exception is NOT stored to be re-raised: a
        replayed raise is a memoised fault wearing a different hat, and it would
        keep reporting "semgrep is not installed" after semgrep was installed.
        There is NO try/except in this module at all -- verified by AST walk,
        zero `Try` nodes -- so the raise propagates untouched and the store line
        below it is simply never reached. agents/security.py catches it and falls
        back to the fixture verdict, unchanged. An earlier version of this
        paragraph said "the `try` below has no `except`", which sent a reader
        hunting for a construct that was never here; the absence of the handler
        is the stronger property, so it is now what this says.

    CONSEQUENCE WORTH KNOWING BEFORE YOU MEASURE THIS: because 117 calls raise
    and the remaining 4 return only faults, a CORRECT cache leaves the shipped
    suite's wrapper-invocation count exactly where it was -- measured 129 at
    1171470, unchanged after this landed. Check that split rather than trusting
    it: a raising call costs 1 wrapper invocation because semgrep dies first, a
    completing one costs 3, and 117 + 4*3 = 129. An earlier version of this
    paragraph said 116 and 5, which totals 131 and cannot be right -- and
    `_gitleaks` and `_trivy` are each invoked exactly 4 times, which is the
    completing-call count. A measurement that shows that number
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
    lifetimes here are a pipeline run, a pytest session, and a demo.

    MEASURED AT THIS COMMIT, by spying `_diff_key` and the cache dict over a full
    `pytest -q`: the whole suite computes SEVEN distinct keys and only FOUR of
    them are ever STORED -- the other three belong to calls that raise, and a
    raise stores nothing. Four is therefore the high-water mark for a session
    that never cleared. The method is given because the bare number is what goes
    stale: an earlier version of this paragraph said "six", which was the count of
    distinct diffs in the SHIPPED suite at 1171470 and never the number of cache
    entries, and the task report that accompanied it said "eight", which counted
    the `dev is None` pseudo-key that never reaches `_diff_key` at all. Re-measure
    rather than adjusting by hand.

    The argument does not rest on the exact value -- it holds at four, seven or a
    few hundred. A long-lived server would need a cap; nothing in this repository
    is one, and an LRU would add a second thing to get wrong beside the fault
    rule. `reset_scanner_cache()` exists so the suite can clear it between tests,
    and is the hook a long-running caller would use per run.
"""

import hashlib
from typing import get_args

from ..state import DevResult, Finding
from . import _run
from .gitleaks_tool import scan as _gitleaks
from .semgrep_tool import scan as _semgrep
from .trivy_tool import scan as _trivy

# sha256(full diff text) -> the findings the fan-out produced for it. Only
# fault-free results are ever stored; see the module docstring.
_CACHE: dict[str, list[Finding]] = {}


def _fault_rules() -> frozenset[str]:
    """Every rule `error_finding` can produce, for every tool the lane knows.

    This is what "is this result a fault?" is decided against, and BOTH halves
    are derived rather than written down: the tool list from `ScannerTool`, the
    rule spelling from `error_finding` itself.

    WHY THIS AND NOT A CLEANER SIGNAL: there isn't one. `error_finding` returns a
    plain `Finding`, the shape frozen in state.py, carrying no marker field -- and
    adding one would mean touching a frozen model to serve a cache. Severity is
    not it either: `high` is what a real semgrep hit reports, so `severity ==
    "high"` would refuse to cache genuine findings. The `rule` string is the only
    thing that distinguishes a fault from a finding.

    WHY THE TOOL LIST COMES FROM `get_args(ScannerTool)` AND NOT FROM A TUPLE
    WRITTEN HERE. The first version of this code iterated a literal
    `("semgrep", "gitleaks", "trivy")` under a comment claiming that deriving the
    set meant "a fourth tool added to `ScannerTool` cannot leave a fault silently
    cacheable". That claim was FALSE and was caught in review by measurement: a
    fourth tool's `bandit-scanner-error` is simply absent from a set built off
    three hardcoded names, `_is_fault_free` returns True, and that tool's
    transient timeout gets pinned to the diff for the life of the process. The
    fix is to make the claim true, so `ScannerTool` -- the Literal in _run.py that
    already exists to make a mistyped tool an authoring-time error -- is now the
    single source of the tool list.

    WHY IT IS A FUNCTION AND NOT A MODULE-LEVEL CONSTANT, which is the obvious
    shape and looks like a wasted recomputation. A constant is computed once at
    import, which makes the derivation UNTESTABLE: the reviewer replaced the
    derived constant with a byte-identical hardcoded literal and dropped the
    `error_finding` import, and all 186 tests passed, because today the two
    spellings produce the same three strings. Computing it live is what lets
    `test_the_fault_rule_set_is_derived_from_the_tool_type_and_not_restated`
    patch either source of truth and observe this follow. Do not "optimise" it back into a constant
    without replacing that pin -- the cost is three `error_finding` calls per
    cache MISS, against three subprocess scanners on the same path.

    Both names are read THROUGH the `_run` module rather than imported as bare
    names, for the reason `unrunnable_findings`'s docstring in _run.py gives
    about `config.SCANNERS_REQUIRED` (that rationale is in the FUNCTION's
    docstring, not the module's): a `from ._run import error_finding` binds the
    value at import, before any test can substitute it, so the coupling would
    again be unobservable.
    """
    return frozenset(
        _run.error_finding(tool, "").rule for tool in get_args(_run.ScannerTool)
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
    fault_rules = _fault_rules()
    return all(finding.rule not in fault_rules for finding in findings)


def _sort_key(finding: Finding) -> tuple[str, str, int, str, str, str]:
    """A TOTAL order over findings, so the rendered gate reads the same twice.

    THE DEFECT THIS CLOSES. No scanner orders its report and the fan-out simply
    concatenates, so the ORDER of the findings list is whatever the tools
    happened to emit. Measured on the poisoned fixture across ten runs of real
    gitleaks: the explanation led with `aws-secret-access-key` six times and
    `aws-access-key-id` four. Verdict, count and finding-SET were identical every
    time -- only the rendered order moved. `compute_security_verdict` builds
    `blocking` by comprehension over `findings`, so it inherits that order, and
    `_default_explanation` joins it into the string on the PR comment and the
    projector.

    WHY ALL SIX FIELDS. The key must be TOTAL: if two DISTINCT findings can tie,
    `sorted` keeps them in input order and the defect survives for that pair.
    `Finding` has exactly six fields -- tool, severity, rule, file, line,
    description -- so a key over all six can only tie on findings that are equal
    in every field the model carries, and those are indistinguishable in the
    rendered output anyway. Verified: `list(Finding.model_fields)` is those
    six, and `test_the_sort_key_reads_every_field_a_finding_carries` builds one
    MINIMAL pair per field, so dropping any single component turns it red --
    that pin exists because review measured a narrowed `(tool, file, line,
    rule)` key passing the entire suite.
    A shorter key is tempting and wrong. Keying on `(tool, rule)` alone leaves
    any two hits of the SAME rule in one file tied -- one credential pattern
    matching two lines, which is the ordinary shape of a secrets finding. Note
    the demo fixture's own two AWS hits are NOT that case: they carry different
    rules (`aws-access-key-id` and `aws-secret-access-key`) and so are separated
    even by `(tool, rule)`. An earlier version of this paragraph claimed they
    were the measured pair; they are not, and the argument for totality does not
    need them to be.

    WHY THIS ORDER OF COMPONENTS, which is a readability choice and not a
    correctness one: any total key fixes the defect. Findings group by tool, then
    by file, then run down the file by line, which is the order a reviewer reads
    a diff in. `rule`, `severity` and `description` are tie-breakers that only
    matter for two findings at the same tool/file/line.

    NOT SEVERITY-FIRST, deliberately. Ordering the worst finding first would read
    better on a projector, but `severity` is the one field `compute_security_verdict`
    consumes, and making the gate's INPUT order depend on it invites a future
    reader to believe the order carries meaning. The verdict is computed from
    severity regardless of position -- pinned by the existing threshold tests --
    and this key leaves that untouched.
    """
    return (
        finding.tool,
        finding.file,
        finding.line,
        finding.rule,
        finding.severity,
        finding.description,
    )


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

    # Sorted HERE, once, across all three tools -- not per-wrapper. Per-wrapper
    # sorting would leave each tool's block internally ordered and the BLOCKS in
    # fan-out order, which is stable only for as long as nobody reorders that
    # tuple; and it would need the same key written in three files, which is how
    # this lane ended up with four drifting copies of the diff materialiser.
    #
    # BEFORE the cache store, so what gets memoised is what a fresh call returns.
    # Sorting after the store -- or in the `cached is not None` branch -- would
    # make a cached result and a fresh one differ in ORDER, which the brief
    # forbids and which no `len()` or set-based assertion would catch.
    #
    # This sorts a MIXED clean+fault result too. That is intentional and harmless:
    # `_is_fault_free` and `_fault_rules` are membership tests over `rule`, so
    # neither reads position, and a reordered list containing a fault is still
    # refused by the store. A fault's `file` is `<semgrep scanner>` and its line
    # is 0, so faults sort among the findings rather than to a fixed end -- the
    # verdict is unaffected either way, since severity alone decides it.
    findings.sort(key=_sort_key)

    if _is_fault_free(findings):
        _CACHE[key] = _copy(findings)
    return findings
