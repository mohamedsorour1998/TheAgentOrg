"""Four fail-open defects in the layer that decides whether a change ships.

Every one is SILENT: the suite stays green, the gate stays green, and the verdict
is wrong. That is the exact shape this project exists to prevent, in the one place
it matters most.

WHY THIS FILE IS SEPARATE FROM tests/test_scanner_resilience.py, which owns the
same lane. That file's subject is a scanner that DID NOT RUN -- absent, broken,
hung, or emitting a report nobody can read -- and every assertion in it is about
turning that into a blocking finding. The four defects here are the opposite
shape: the scanner ran, its output was read, and the answer was wrong anyway. A
severity table that downgrades, a shape guard bypassed before it fires, a fan-out
that discards two thirds of its own work, and a poisoning check reading the wrong
half of the diff. None of them involves a fault, so none would fit that file's
harness, which puts deliberately broken binaries on PATH.
"""

import json
from pathlib import Path

import pytest

from agentorg.security import semgrep_tool, trivy_tool
from agentorg.state import SEVERITY_ORDER, DevResult, compute_security_verdict

BLOCK_CUTOFF = SEVERITY_ORDER["high"]

# A diff with nothing interesting in it. These tests are about how a report is
# READ, not about detection, and a fake scanner ignores the content anyway -- but
# the materialiser must have something to write, or a change to it surfaces here
# as a confusing red.
_HARMLESS_DIFF = "--- /dev/null\n+++ b/app/noop.py\n@@ -0,0 +1 @@\n+VALUE = 1\n"


def _dev() -> DevResult:
    return DevResult(
        branch="feat/x",
        diff=_HARMLESS_DIFF,
        summary="s",
        files_changed=["app/noop.py"],
    )


def _scanner_writing(bin_dir: Path, tool: str, report: str, monkeypatch) -> None:
    """Put a fake `tool` on PATH that writes `report` and exits 0.

    Deliberately the same shape as test_scanner_resilience.py's `_fake_scanner` +
    `_write_report_script` pair, and the constraints that file measured apply
    here too, because PATH is REPLACED rather than prepended:

      * the script may use only shell BUILTINS -- there is no `cat`, no
        `printf(1)`. A `cat > "$arg" <<EOF` heredoc creates the report file EMPTY
        before failing to exec `cat`, which makes the wrapper raise
        JSONDecodeError and return a scanner-error -- so a test asserting "a
        malformed report blocks" passes while actually testing an empty file.
        That is why every assertion below checks WHICH fault it got.
      * argv is WALKED for the report path rather than indexed, so a wrapper that
        reorders its flags is still honoured and one that stops asking for a
        report at all fails loudly.

    Replacing PATH also matters on a machine that HAS trivy -- a demo laptop,
    CI's `scan` job -- where a prepend that failed would silently run the real
    binary and the test would pin nothing.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / tool
    path.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        f"    *{tool}-report.json) echo '{report}' > \"$arg\" ;;\n"
        "  esac\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


@pytest.mark.parametrize(
    ("semgrep_severity", "must_reach_cutoff"),
    [
        ("INFO", False), ("LOW", False),
        ("WARNING", False), ("MEDIUM", False),
        ("ERROR", True), ("HIGH", True), ("CRITICAL", True),
    ],
)
def test_semgrep_severities_that_should_block_do_block(
    semgrep_severity, must_reach_cutoff
):
    """MEASURED before the fix: HIGH and CRITICAL both mapped to `low` (order 0)
    against a cutoff of 2, so a rule semgrep marked CRITICAL could not block."""
    mapped = semgrep_tool._map_severity(semgrep_severity)
    reaches = SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF
    assert reaches is must_reach_cutoff, (
        f"semgrep {semgrep_severity!r} maps to {mapped!r} (order "
        f"{SEVERITY_ORDER[mapped]}) and {'does not reach' if not reaches else 'reaches'} "
        f"the block cutoff {BLOCK_CUTOFF}. Expected "
        f"{'to block' if must_reach_cutoff else 'not to block'}."
    )


def test_an_unrecognised_semgrep_severity_fails_CLOSED():
    """The default must not be the lowest severity.

    An unknown value means semgrep said something this table has not seen. Mapping
    it to `low` means a new severity name silently stops blocking; mapping it high
    means a new name blocks loudly and somebody fixes the table. Only one of those
    is safe to be wrong about.
    """
    mapped = semgrep_tool._map_severity("SOME_FUTURE_SEVERITY")
    assert SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF, (
        f"an unrecognised semgrep severity maps to {mapped!r} (order "
        f"{SEVERITY_ORDER[mapped]}), below the cutoff {BLOCK_CUTOFF}. It must fail "
        f"CLOSED: a severity name this table does not know is not evidence of "
        f"safety."
    )


# The exact severity each name maps to, as opposed to merely which side of the
# block cutoff it lands on.
#
# WHY THIS TABLE EXISTS ALONGSIDE THE CUTOFF TESTS, which is the more interesting
# half. The plan for this fix predicted that deleting `"CRITICAL"` from
# semgrep's mapping would turn
# `test_semgrep_severities_that_should_block_do_block[CRITICAL]` red. MEASURED: it
# does not. Once the default fails CLOSED at `high`, a deleted key falls through
# to `high`, which reaches the cutoff, so every cutoff-shaped assertion stays
# green -- the fail-closed default ABSORBS the missing-key mutation, and the two
# fixes cannot both be pinned by one assertion.
#
# So the cutoff tests pin the block DECISION and this one pins the TABLE. Without
# it, `critical` versus `high` for a CRITICAL finding is pinned by nothing, and
# that distinction is not cosmetic: `agents/security._default_explanation`
# renders the severity into the line on the PR comment and the projector, and
# CLAUDE.md's central discriminator is a set of findings at `critical`.
_SEMGREP_SEVERITIES = {
    "INFO": "low",
    "LOW": "low",
    "WARNING": "medium",
    "MEDIUM": "medium",
    "ERROR": "high",
    "HIGH": "high",
    "CRITICAL": "critical",
}


@pytest.mark.parametrize(("name", "expected"), sorted(_SEMGREP_SEVERITIES.items()))
def test_each_semgrep_severity_maps_to_its_exact_severity(name, expected):
    """Every known name maps to one specific severity, not merely a blocking one."""
    mapped = semgrep_tool._map_severity(name)
    assert mapped == expected, (
        f"semgrep {name!r} mapped to {mapped!r}, expected {expected!r}. If this "
        f"reads {'high'!r} where {'critical'!r} belongs, the key was dropped from "
        f"the table and the fail-closed default answered instead -- which reaches "
        f"the block cutoff, so no cutoff-shaped assertion can see it."
    )


def test_the_semgrep_table_is_case_insensitive_on_the_names_semgrep_sends():
    """`.upper()` is load-bearing: semgrep 1.x sends `error`, not only `ERROR`.

    New-style rule metadata is lower-cased in some versions. A table keyed on
    upper-case names with no normalisation sends every one of those to the
    default -- which now fails CLOSED, so the symptom would be the CLEAN demo run
    blocking on our own INFO rule rather than a poisoned one passing. That is the
    safe direction and still a broken demo.
    """
    for name, expected in _SEMGREP_SEVERITIES.items():
        assert semgrep_tool._map_severity(name.lower()) == expected, (
            f"semgrep {name.lower()!r} did not map to {expected!r}; the "
            f"normalisation that makes case irrelevant is gone"
        )


@pytest.mark.parametrize(
    ("trivy_severity", "must_reach_cutoff"),
    [("UNKNOWN", False), ("LOW", False), ("MEDIUM", False),
     ("HIGH", True), ("CRITICAL", True)],
)
def test_trivy_severities_map_correctly(trivy_severity, must_reach_cutoff):
    """trivy's table is currently complete; this is the tripwire, not a fix."""
    mapped = trivy_tool._map_severity(trivy_severity)
    assert (SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF) is must_reach_cutoff


def test_an_unrecognised_trivy_severity_fails_CLOSED():
    mapped = trivy_tool._map_severity("SOME_FUTURE_SEVERITY")
    assert SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF, (
        f"unrecognised trivy severity maps to {mapped!r}; same fail-closed "
        f"requirement as semgrep"
    )


def test_an_absent_severity_field_still_fails_CLOSED_in_both_wrappers():
    """`None` and `""` take the same default as an unknown name, in both tables.

    Separate from the test above because they reach the default down a different
    route -- `(severity or "").upper()` turns both into `""`, which is not a key
    either mapping holds -- and because a fix that special-cased the empty string
    back to `low` would leave that test green. A report that omits `severity`
    entirely is the likeliest shape of all: `report_text(extra, "severity", "")`
    hands this function the empty string for every semgrep result whose `extra`
    lacks the field, which is exactly what a truncated report looks like.
    """
    for wrapper in (semgrep_tool, trivy_tool):
        for absent in (None, ""):
            mapped = wrapper._map_severity(absent)
            assert SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF, (
                f"{wrapper.__name__} mapped a {absent!r} severity to {mapped!r} "
                f"(order {SEVERITY_ORDER[mapped]}), below the cutoff "
                f"{BLOCK_CUTOFF}. A report that does not say how bad a finding "
                f"is has not said it is harmless."
            )


def test_a_high_severity_finding_actually_produces_a_block():
    """End to end through the real rule, so the mapping is not tested in isolation.

    A severity table that maps correctly but whose values do not reach
    compute_security_verdict would pass every test above.
    """
    from agentorg.state import Finding
    for sev in ("HIGH", "CRITICAL", "ERROR"):
        f = Finding(tool="semgrep", severity=semgrep_tool._map_severity(sev),
                    rule="r", file="app/auth.py", line=1, description="d")
        verdict, blocking = compute_security_verdict([f], threshold="high")
        assert verdict == "block", (
            f"a semgrep {sev} finding produced verdict {verdict!r} with "
            f"{len(blocking)} blocking. It must block."
        )


def test_the_low_severities_still_pass_end_to_end():
    """The negative control for the test above, and it is not decoration.

    A table that mapped EVERYTHING to `critical` satisfies every fail-closed
    assertion in this file. It would also block the clean half of the demo on
    semgrep's own INFO-severity rule -- `agentorg/security/semgrep_rules.yml`
    declares `severity: INFO`, so this is the severity our shipped rules actually
    emit, not a hypothetical.
    """
    from agentorg.state import Finding
    for sev in ("INFO", "LOW", "WARNING", "MEDIUM"):
        f = Finding(tool="semgrep", severity=semgrep_tool._map_severity(sev),
                    rule="r", file="app/auth.py", line=1, description="d")
        verdict, blocking = compute_security_verdict([f], threshold="high")
        assert verdict == "pass", (
            f"a semgrep {sev} finding produced verdict {verdict!r} with "
            f"{len(blocking)} blocking. Mapping every severity up would satisfy "
            f"the fail-closed tests and block the CLEAN demo run on our own "
            f"INFO-severity rule."
        )


# ==========================================================================
# A2 -- trivy's `or []` collapsed a wrong-typed Results field to an empty scan
# BEFORE the shape guard that exists to reject it.
# ==========================================================================

# Falsy values of the wrong TYPE for `Results`, which trivy documents as a list.
# Every one is what `or []` turns into a valid empty list; every one is what
# `.get("Results")` plus an explicit None check hands to the shape guard instead.
#
# `None` is deliberately NOT here, and its absence is the point of the separate
# test below: JSON `null` and a missing key are how trivy legitimately spells
# "no targets", so those must stay a clean empty scan rather than becoming a
# fault. A test that lumped them in would demand the fail-closed direction for
# the one shape that is genuinely fine.
_WRONG_TYPED_RESULTS = ["", 0, False, {}]


@pytest.mark.parametrize("wrong_value", _WRONG_TYPED_RESULTS)
def test_a_wrong_typed_results_field_is_a_FAULT_not_an_empty_scan(
    wrong_value, tmp_path, monkeypatch
):
    """MEASURED: `data.get("Results") or []` collapsed every falsy wrong type to a
    valid empty list BEFORE the shape guard, so a malformed trivy report produced
    zero findings and a `pass` instead of a blocking fault.

    Measured through the real `scan()`, on `{"Results": ""}`:

        findings: []
        verdict: ('pass', [])

    Its sibling wrapper spells the same guard `.get("results", [])` and trips
    correctly -- semgrep on the byte-equivalent malformed report returns
    `[('semgrep-scanner-error', 'high')]`. Two spellings of one guard, one file
    apart; one failed open.

    DRIVEN THROUGH `scan()` RATHER THAN A PARSE HELPER, because there is no parse
    helper -- trivy_tool inlines the report read in `scan`. The plan for this fix
    named `_findings_from_report`, which does not exist in this wrapper.
    """
    _scanner_writing(
        tmp_path / "bin", "trivy", f'{{"Results": {json.dumps(wrong_value)}}}',
        monkeypatch,
    )

    findings = trivy_tool.scan(_dev())

    assert findings, (
        f"a Results field of {wrong_value!r} produced NO findings. It was treated "
        f"as an empty scan, which reports `pass` over a report nobody read -- and "
        f"compute_security_verdict([]) returns ('pass', []), so this is the "
        f"silent pass."
    )
    assert [f.rule for f in findings] == ["trivy-scanner-error"], (
        f"expected exactly one blocking trivy-scanner-error, got "
        f"{[(f.rule, f.severity) for f in findings]}"
    )

    description = findings[0].description
    assert "Results" in description, (
        f"the fault must name the field it rejected, so an operator can tell a "
        f"malformed report from a dead binary. Got {description!r}"
    )
    assert "not valid JSON" not in description, (
        f"this report IS valid JSON. A parse error means the fake scanner wrote "
        f"an empty file and the shape guard was never reached -- the exact way a "
        f"`cat` heredoc faked results twice in this suite. Got {description!r}"
    )

    verdict, blocking = compute_security_verdict(findings, threshold="high")
    assert verdict == "block", (
        f"a malformed report must BLOCK; got {verdict!r}. The finding existing is "
        f"not enough if its severity does not reach the threshold."
    )
    assert blocking == findings, "the fault must be the blocking finding"


@pytest.mark.parametrize("report", ['{"Results": null}', "{}"])
def test_an_absent_or_null_results_field_is_still_a_CLEAN_scan(
    report, tmp_path, monkeypatch
):
    """The negative control, and it is what stops the fix overshooting.

    `null` and a missing key are how trivy spells "no targets", which is trivy's
    ordinary answer on both demo fixtures -- CLAUDE.md records trivy as the only
    scanner contributing ZERO findings to either one. A fix that rejected every
    non-list, `None` included, would turn the CLEAN half of the demo into
    `blocking=1` on a healthy scanner, which takes the promote path down.

    So this test is why the implementation is `.get("Results")` plus an explicit
    `is None` check rather than a bare `isinstance(..., list)` rejection.
    """
    _scanner_writing(tmp_path / "bin", "trivy", report, monkeypatch)

    findings = trivy_tool.scan(_dev())

    assert findings == [], (
        f"a Results field of {report} must be a clean empty scan, not a fault. "
        f"Got {[(f.rule, f.severity) for f in findings]}. trivy reports no "
        f"targets on both demo fixtures, so this is the shape the CLEAN run "
        f"depends on."
    )


def test_the_two_wrappers_answer_the_same_malformed_shape_the_same_way(
    tmp_path, monkeypatch
):
    """The asymmetry itself, asserted -- because that is what the defect WAS.

    Neither wrapper was wrong about `Results` in isolation; they DISAGREED about
    one shape, and the copy that drifted is by definition the one nobody noticed.
    Measured before the fix on the byte-equivalent report `{"<results>": ""}`:

        trivy   -> []                                  verdict pass
        semgrep -> [('semgrep-scanner-error', 'high')]  verdict block

    Pinning the pair means a future edit to either spelling has to break this
    test to reintroduce a divergence, rather than only the wrapper it touched.
    """
    _scanner_writing(tmp_path / "bin-t", "trivy", '{"Results": ""}', monkeypatch)
    trivy_findings = trivy_tool.scan(_dev())

    _scanner_writing(tmp_path / "bin-s", "semgrep", '{"results": ""}', monkeypatch)
    semgrep_findings = semgrep_tool.scan(_dev())

    assert semgrep_findings, (
        "semgrep produced no finding for a wrong-typed results field, so this "
        "test's control is broken and it can no longer detect a divergence"
    )
    assert [f.rule for f in trivy_findings] == ["trivy-scanner-error"], (
        f"trivy: {[(f.rule, f.severity) for f in trivy_findings]}"
    )
    assert [f.rule for f in semgrep_findings] == ["semgrep-scanner-error"], (
        f"semgrep: {[(f.rule, f.severity) for f in semgrep_findings]}"
    )

    trivy_verdict = compute_security_verdict(trivy_findings, threshold="high")[0]
    semgrep_verdict = compute_security_verdict(semgrep_findings, threshold="high")[0]
    assert trivy_verdict == semgrep_verdict == "block", (
        f"the two wrappers disagree about one malformed shape: trivy "
        f"{trivy_verdict!r}, semgrep {semgrep_verdict!r}. That disagreement IS "
        f"the defect -- one spelling of the guard fails open."
    )


# ==========================================================================
# A3 -- semgrep is FIRST in the fan-out and its raise ended the loop, so the
# other two wrappers' findings and their blocking faults were discarded.
# ==========================================================================


def test_one_absent_scanner_does_not_discard_the_others(monkeypatch):
    """MEASURED: semgrep runs first and its FileNotFoundError ended the fan-out,
    so gitleaks' and trivy's findings -- and their blocking faults -- were thrown
    away. `wrappers actually invoked: ['semgrep']`.

    `agentorg/security/__init__.py` records 117 of 121 fan-out calls taking this
    path, because semgrep is first and CI installs no binaries. This is the
    ordinary path, not an edge case.
    """
    from agentorg import security as sec
    from agentorg.state import Finding

    called: list[str] = []

    def _absent(dev):
        called.append("semgrep")
        raise FileNotFoundError("semgrep is not installed")

    def _finds(name):
        def _scan(dev):
            called.append(name)
            return [Finding(tool=name, severity="critical", rule=f"{name}-r",
                            file="app/auth.py", line=1, description="d")]
        return _scan

    monkeypatch.setattr(sec, "_semgrep", _absent)
    monkeypatch.setattr(sec, "_gitleaks", _finds("gitleaks"))
    monkeypatch.setattr(sec, "_trivy", _finds("trivy"))
    sec.reset_scanner_cache()

    dev = DevResult(branch="b", diff="--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n+x\n",
                    summary="s", files_changed=["app/auth.py"])
    try:
        findings = sec.run_all_scanners(dev)
    except FileNotFoundError:
        pytest.fail(
            f"the fan-out aborted on the first absent scanner. Wrappers invoked: "
            f"{called}. gitleaks and trivy never ran, so their findings and any "
            f"blocking faults were discarded -- and this is CI's normal path, not "
            f"an edge case."
        )
    assert "gitleaks" in called and "trivy" in called, f"invoked only {called}"
    tools = {f.tool for f in findings}
    assert {"gitleaks", "trivy"} <= tools, f"findings came only from {tools}"


def test_a_blocking_FAULT_survives_an_absence_in_an_earlier_wrapper(monkeypatch):
    """The fail-open half of A3, and the reason it is not merely lost coverage.

    semgrep ABSENT (the dev affordance, which raises) plus gitleaks BROKEN (a
    fault, which must block). Before the fix semgrep's raise came first, so
    gitleaks' blocking `gitleaks-scanner-error` was never produced;
    agents/security.py answered the raise with the FIXTURE verdict, and the
    fixture verdict for a clean diff is `pass`. A broken scanner reported clean.

    Distinct from the test above, which uses ordinary findings: this one is about
    a fault, and a fault is the thing the whole `_run.py` module exists to turn
    into a block. It is also the case a cache must never store.
    """
    from agentorg import security as sec
    from agentorg.security._run import error_finding

    def _absent(dev):
        raise FileNotFoundError("semgrep is not installed")

    monkeypatch.setattr(sec, "_semgrep", _absent)
    monkeypatch.setattr(
        sec, "_gitleaks", lambda dev: [error_finding("gitleaks", "exit code 2")]
    )
    monkeypatch.setattr(sec, "_trivy", lambda dev: [])
    sec.reset_scanner_cache()

    findings = sec.run_all_scanners(_dev())

    assert [f.rule for f in findings] == ["gitleaks-scanner-error"], (
        f"a broken gitleaks behind an absent semgrep produced "
        f"{[(f.rule, f.severity) for f in findings]}. Its fault must survive the "
        f"earlier absence, or a broken scanner is answered by the fixture verdict "
        f"-- which for a clean diff is `pass`."
    )
    verdict, _blocking = compute_security_verdict(findings, threshold="high")
    assert verdict == "block", (
        f"the surviving fault must BLOCK; got {verdict!r}. Surviving the loop is "
        f"not enough if it does not reach the verdict."
    )


def test_a_PARTIAL_fan_out_is_never_memoised(monkeypatch):
    """The cache interaction, which is load-bearing and which `_is_fault_free`
    alone does NOT cover.

    An absence produces NO finding, so a partial result carrying one absence and
    clean findings from the other two is fault-FREE by `_is_fault_free`'s test --
    it inspects `rule` strings and there is no rule to inspect for a wrapper that
    never ran. So the store must be gated on the absence as well, or the demo's
    next repeat of that diff is answered from a scan that skipped a scanner, on a
    machine where the binary is now installed. That is the same defect the module
    docstring closes for faults, one level over.

    The retry is asserted by COUNTING wrapper invocations, not by comparing
    results: an implementation that stored the partial answer and returned it
    would produce an identical findings list, so equality proves nothing here.
    """
    from agentorg import security as sec

    calls: list[str] = []

    def _absent(dev):
        calls.append("semgrep")
        raise FileNotFoundError("semgrep is not installed")

    def _clean(name):
        def _scan(dev):
            calls.append(name)
            return [sec.Finding(tool=name, severity="low", rule=f"{name}-noop",
                                file="app/noop.py", line=1,
                                description="a clean scan that found something")]
        return _scan

    monkeypatch.setattr(sec, "_semgrep", _absent)
    monkeypatch.setattr(sec, "_gitleaks", _clean("gitleaks"))
    monkeypatch.setattr(sec, "_trivy", _clean("trivy"))
    sec.reset_scanner_cache()

    first = sec.run_all_scanners(_dev())
    assert calls == ["semgrep", "gitleaks", "trivy"], (
        f"the first call must fan out to all three despite the absence; got {calls!r}"
    )
    assert {f.rule for f in first} == {"gitleaks-noop", "trivy-noop"}, (
        f"expected the two present scanners' findings, got "
        f"{[(f.tool, f.rule) for f in first]}. An empty result here would make "
        f"the cache assertion below vacuous."
    )

    sec.run_all_scanners(_dev())

    assert calls == ["semgrep", "gitleaks", "trivy"] * 2, (
        f"the second call did not re-enter the fan-out: {calls!r}. A result "
        f"assembled from a partial fan-out was MEMOISED, so a later call on a "
        f"machine where semgrep is installed is answered by the scan that "
        f"skipped it. `_is_fault_free` cannot catch this -- an absence leaves no "
        f"finding, so it inspects nothing."
    )


def test_every_scanner_absent_still_RAISES_so_the_fixture_answers(monkeypatch):
    """The path 117 of 121 shipped fan-out calls take, and it must not change.

    This is the one that makes the isolation safe rather than a new fail-open. If
    the loop swallowed every absence and returned what it had, a machine with NO
    scanners installed -- CI's `test` job, by design -- would hand back `[]`, and
    `compute_security_verdict([])` returns `("pass", [])`. Every poisoned run in
    CI would go green. The raise is what routes this to
    agents/security.py's FIXTURE verdict, which still blocks a diff carrying an
    AWS key.

    So an absence is isolated from the OTHER wrappers, not from the caller.
    """
    from agentorg import security as sec

    def _absent(tool):
        def _scan(dev):
            raise FileNotFoundError(f"{tool} is not installed")
        return _scan

    for name in ("_semgrep", "_gitleaks", "_trivy"):
        monkeypatch.setattr(sec, name, _absent(name.removeprefix("_")))
    sec.reset_scanner_cache()

    with pytest.raises(FileNotFoundError) as caught:
        sec.run_all_scanners(_dev())

    message = str(caught.value)
    for tool in ("semgrep", "gitleaks", "trivy"):
        assert tool in message, (
            f"the raise must name every scanner that was absent so an operator "
            f"can see it was not just the first one. {tool!r} missing from "
            f"{message!r}"
        )


def test_an_absence_with_nothing_to_show_RAISES_rather_than_reporting_pass(
    monkeypatch,
):
    """A partial fan-out whose present scanners found NOTHING is indistinguishable
    from a clean full scan, so it must not be reported as one.

    semgrep absent; gitleaks and trivy present, healthy, and quiet. Returning
    `[]` here would be `("pass", [])` from a fan-out that skipped a scanner --
    and `[]` is precisely the shape `_run.unrunnable_findings` refuses to
    produce, for exactly this reason. Raising routes it to the fixture verdict,
    which is what happens today.

    This is the assertion that keeps the A3 isolation strictly fail-CLOSED: an
    absence is invisible only when the wrappers that DID run produced something
    to judge.
    """
    from agentorg import security as sec

    def _absent(dev):
        raise FileNotFoundError("semgrep is not installed")

    monkeypatch.setattr(sec, "_semgrep", _absent)
    monkeypatch.setattr(sec, "_gitleaks", lambda dev: [])
    monkeypatch.setattr(sec, "_trivy", lambda dev: [])
    sec.reset_scanner_cache()

    with pytest.raises(FileNotFoundError):
        sec.run_all_scanners(_dev())


def test_SCANNERS_REQUIRED_still_turns_an_absence_into_a_blocking_fault(
    monkeypatch, tmp_path
):
    """The knob's semantics are unchanged by the isolation -- asserted, not assumed.

    With SCANNERS_REQUIRED set, `_run.unrunnable_findings` returns a blocking
    `*-scanner-error` instead of raising, so the isolation never sees an
    exception and every tool is named. Three faults, `blocking=3`, on a CLEAN
    diff -- which is what CLAUDE.md records for a runtime carrying the knob but
    not the binaries.

    Driven through the real wrappers with an EMPTY directory as PATH rather than
    stubs, so it exercises `unrunnable_findings` itself: a stubbed wrapper would
    let the knob be honoured nowhere and this test would still pass. PATH is
    REPLACED, and it must point at a directory that is genuinely empty -- an
    earlier draft of this test used the CWD, which passes only because no scanner
    binary happens to sit in the repo root, and would silently start running real
    scanners on a machine where one did.
    """
    from agentorg import security as sec
    from agentorg.common import config

    empty = tmp_path / "no-binaries-here"
    empty.mkdir()
    assert list(empty.iterdir()) == [], "the PATH directory must be empty"
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)
    sec.reset_scanner_cache()

    findings = sec.run_all_scanners(_dev())

    assert {f.rule for f in findings} == {
        "semgrep-scanner-error",
        "gitleaks-scanner-error",
        "trivy-scanner-error",
    }, (
        f"SCANNERS_REQUIRED must name every absent tool, not just the first. Got "
        f"{[(f.tool, f.rule) for f in findings]}. If this names only semgrep, the "
        f"absent branch is raising under the knob again -- which reintroduces the "
        f"abort in the configuration the security runtime actually runs."
    )
    verdict, blocking = compute_security_verdict(findings, threshold="high")
    assert verdict == "block" and len(blocking) == 3, (
        f"expected block with blocking=3, got {verdict!r} with {len(blocking)}"
    )


# ==========================================================================
# A4 -- `_looks_poisoned` was the whole-diff substring scan common/diff.py was
# written to delete. It chooses which fixture stands in on the fallback path, so
# it is choosing between `block` and `pass`.
# ==========================================================================


def test_looks_poisoned_reads_the_change_not_the_whole_diff_text():
    """MEASURED both directions. This function chooses which fixture stands in on
    the fallback path, so it is choosing between `block` and `pass`.

    CLAUDE.md records the whole-diff substring form costing 2 blocks in 5 live runs.
    The developer agent already does this correctly via added_files() and a real
    AKIA[0-9A-Z]{16} regex; the security agent is the straggler.
    """
    from agentorg.agents import security as sec_agent
    from agentorg.state import RunState

    def _state(diff: str) -> RunState:
        s = RunState(ticket_id="T-1", ticket_text="x")
        s.dev = DevResult(branch="b", diff=diff, summary="s", files_changed=["app/auth.py"])
        return s

    removed = ('--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1,2 +1,1 @@\n'
               '-AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n+import os\n')
    assert sec_agent._looks_poisoned(_state(removed)) is False, (
        "a key on a REMOVED line read as poisoned. That is the shape of every "
        "revision after the reviewer asks for credentials to be taken out, and "
        "CLAUDE.md records this exact confusion costing 2 blocks in 5 live runs."
    )

    added = ('--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n'
             '+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n')
    assert sec_agent._looks_poisoned(_state(added)) is True, (
        "an ADDED key did not read as poisoned -- the fix went too far and the "
        "poisoned demo would pick the pass fixture"
    )


def test_looks_poisoned_requires_a_real_key_shape_not_the_four_letters_AKIA():
    """`"AKIA" in text` is a substring test, not a credential test.

    Two measured consequences of the old form, and they point opposite ways:

      * it fired on any prose containing the letters -- a ticket discussing
        `AKIA` prefixes, a comment naming the pattern -- so a clean change could
        pick the BLOCK fixture;
      * and it missed every credential that is not an AWS key id. A GitHub PAT
        added on a `+` line measured False.

    The regex is `AKIA[0-9A-Z]{16}`, imported rather than re-spelled -- see the
    test below on why a fifth copy is the thing to avoid here.
    """
    from agentorg.agents import security as sec_agent
    from agentorg.state import RunState

    def _looks(diff: str) -> bool:
        s = RunState(ticket_id="T-1", ticket_text="x")
        s.dev = DevResult(branch="b", diff=diff, summary="s", files_changed=["app/auth.py"])
        return sec_agent._looks_poisoned(s)

    mentions = ('--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n'
                '+Never commit a key beginning AKIA to this repository.\n')
    assert _looks(mentions) is False, (
        "prose mentioning the four letters AKIA read as poisoned. The substring "
        "form cannot tell a credential from a sentence about credentials, and "
        "this direction makes a CLEAN change pick the block fixture."
    )

    real = ('--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n'
            '+KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    assert _looks(real) is True, (
        "a real AKIA-shaped key on an added line must read as poisoned"
    )


def test_looks_poisoned_uses_the_shared_regex_rather_than_a_fifth_private_copy():
    """The anti-drift pin. CLAUDE.md: four private copies of "what does this
    change contain?" drifted until the poisoned demo stopped blocking.

    Substituting the developer's compiled pattern must change the security
    agent's answer. If it does not, this module has grown its own copy, and the
    two will agree only until one is edited -- which is precisely how the 2-of-5
    failure happened.

    The substitution is made on the DEVELOPER module, deliberately: that is the
    module that owns the pattern, so following it there is what "shared" means.
    """
    import re

    from agentorg.agents import developer as dev_agent
    from agentorg.agents import security as sec_agent
    from agentorg.state import RunState

    s = RunState(ticket_id="T-1", ticket_text="x")
    s.dev = DevResult(
        branch="b",
        diff='--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n+KEY = "AKIAIOSFODNN7EXAMPLE"\n',
        summary="s",
        files_changed=["app/auth.py"],
    )
    assert sec_agent._looks_poisoned(s) is True, "control: this diff is poisoned"

    original = dev_agent._AWS_KEY
    try:
        # A pattern that cannot match the fixture key. If the security agent still
        # says True, it is reading something other than this object.
        dev_agent._AWS_KEY = re.compile(r"THIS_PATTERN_MATCHES_NOTHING_AKIA")
        assert sec_agent._looks_poisoned(s) is False, (
            "the security agent's answer did not follow developer._AWS_KEY, so it "
            "carries its own copy of the credential pattern. That is the fifth "
            "copy common/diff.py exists to prevent -- the copies agree until one "
            "is edited, and CLAUDE.md records that drift costing 2 blocks in 5 "
            "live runs."
        )
    finally:
        dev_agent._AWS_KEY = original


def test_an_UNPARSEABLE_diff_is_treated_as_poisoned_rather_than_clean():
    """Lane B's `added_files` raises ValueError on a non-empty diff that parses to
    zero files. A diff this parser cannot read is NOT evidence of cleanliness.

    The direction is the whole point. Letting the ValueError escape, or answering
    False, would make an unreadable diff pick the PASS fixture -- and this
    function is only ever called on the fallback path, i.e. when the scanners
    have already failed. Both signals lost at once, reported as clean.

    Answering True picks the block fixture, which is the fail-closed choice: a
    human then looks at a run that stopped, rather than at nothing.
    """
    from agentorg.agents import security as sec_agent
    from agentorg.common.diff import added_files
    from agentorg.state import RunState

    unparseable = "this text is not a unified diff and names no files at all"
    with pytest.raises(ValueError):
        added_files(unparseable)

    s = RunState(ticket_id="T-1", ticket_text="x")
    s.dev = DevResult(branch="b", diff=unparseable, summary="s", files_changed=[])
    assert sec_agent._looks_poisoned(s) is True, (
        "a diff the parser could not read was treated as clean. added_files "
        "raises rather than returning {} precisely so this cannot be mistaken "
        "for an empty change, and this function runs only when the scanners have "
        "ALREADY failed -- so answering False loses both signals and reports pass."
    )


def test_an_EMPTY_or_absent_diff_is_not_poisoned():
    """The negative control for the refusal above, and it must not raise.

    `added_files(None)` and `added_files("")` both return `{}` WITHOUT raising --
    that is deliberate in Lane B's parser, and it is a real call: `state.dev` may
    be None early in a run, and a DevResult whose diff is "" is a different
    question from an unparseable one. A fix that treated every falsy answer as
    poisoned would block the CLEAN demo run.
    """
    from agentorg.agents import security as sec_agent
    from agentorg.state import RunState

    empty = RunState(ticket_id="T-1", ticket_text="x")
    empty.dev = DevResult(branch="b", diff="", summary="s", files_changed=[])
    assert sec_agent._looks_poisoned(empty) is False, (
        "an empty diff read as poisoned; added_files('') returns {} without "
        "raising and that must stay a clean answer"
    )

    no_dev = RunState(ticket_id="T-1", ticket_text="x")
    assert no_dev.dev is None, "control: this state has no DevResult"
    assert sec_agent._looks_poisoned(no_dev) is False, (
        "a state with no DevResult read as poisoned"
    )


def test_the_fallback_fixture_choice_follows_looks_poisoned_end_to_end(monkeypatch):
    """The consequence, through `security.run`, because that is what this decides.

    `_looks_poisoned` is not interesting in itself -- it is the argument to
    `fixtures_loader.security(block=...)` on the path taken when the scanners
    raise. So the removed-line diff must produce a PASS fixture and the
    added-line diff a BLOCK fixture, both stamped `fixture-fallback`.

    Without this test the two above could pass against a function whose answer
    nothing reads.
    """
    from agentorg.agents import security as sec_agent
    from agentorg.state import RunState

    def _boom(dev):
        raise FileNotFoundError("no scanners installed")

    monkeypatch.setattr(sec_agent, "run_all_scanners", _boom)

    def _run(diff: str):
        s = RunState(ticket_id="T-1", ticket_text="x")
        s.dev = DevResult(branch="b", diff=diff, summary="s", files_changed=["app/auth.py"])
        return sec_agent.run(s)

    removed = ('--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1,2 +1,1 @@\n'
               '-AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n+import os\n')
    cleaned = _run(removed)
    assert cleaned.verdict == "pass", (
        f"a revision that REMOVES the key fell back to the block fixture "
        f"({cleaned.verdict!r}). This is the shape of every revision after the "
        f"reviewer asks for credentials to be taken out."
    )
    assert cleaned.scan_provenance == "fixture-fallback", (
        f"expected fixture-fallback, got {cleaned.scan_provenance!r} -- if this "
        f"says `scanners` the stub did not take effect and this test pins nothing"
    )

    added = ('--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n'
             '+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n')
    poisoned = _run(added)
    assert poisoned.verdict == "block", (
        f"a diff ADDING the key fell back to the pass fixture "
        f"({poisoned.verdict!r}) -- the poisoned demo would go green with no "
        f"scanner involved"
    )
    assert poisoned.scan_provenance == "fixture-fallback"
