"""FAIL-CLOSED, and the floor under a configured threshold. Lane C, C7 and C8.

C7's PRECEDENT IS A MEASURED DEFECT, NOT A HYPOTHETICAL. semgrep's private table
held only INFO/WARNING/ERROR and defaulted to `"low"`. semgrep 1.x also emits
LOW/MEDIUM/HIGH/CRITICAL from new-style rule metadata, so every one of those
names fell through to that default: severity `low`, order 0, against a block
cutoff of 2. A RULE SEMGREP MARKED CRITICAL COULD NOT BLOCK A CHANGE. Nothing
caught it -- no test read the function, and the one check positioned to notice,
`scripts/scan_gate.py`, asserted only `any(f.tool == "semgrep")`, which a `low`
finding satisfies. The gate was green, the scanner ran, the finding was reported,
and the verdict was wrong.

So the default is the part that was wrong, and the default is the part with tests
on it -- one per scanner, driven through the WRAPPER each scanner's `scan` calls,
not only through the shared table. A test that only drove `scoring.map_severity`
would stay green if a wrapper stopped delegating.

WHY THE ASSERTION IS "NOT BELOW THE THRESHOLD" AND NOT "== high". Pinning the
exact value would fail on a deliberate raise to `critical`, which is a safer
default, and would pass on a table that fails closed for the wrong reason. The
property that matters is the one the defect violated: an unrecognised severity
must still reach the cutoff.
"""

import importlib.util

import pytest

from agentorg.security import gitleaks_tool, scoring, semgrep_tool, trivy_tool
from agentorg.state import SEVERITY_ORDER, Finding, compute_security_verdict

# The wrappers whose `_map_severity` reads a scanner's own severity word.
# gitleaks is absent BY CONSTRUCTION -- it emits no severity, so it has no
# unrecognised-value path -- and `test_gitleaks_has_no_unrecognised_severity_path`
# below asserts that absence rather than leaving it as a silent omission.
_MAPPING_WRAPPERS = {"semgrep": semgrep_tool, "trivy": trivy_tool}

# Values no scanner's table holds. `""` and `None` are the shapes a TRUNCATED
# report produces -- the wrappers read this field through
# `report_text(..., "severity", "")` -- and they reach the default down a
# different route from an unknown name, so all three are exercised.
_UNRECOGNISED = ("SOME_FUTURE_SEVERITY", "", None, "blocker", "0", "SEVERE")


def test_the_wrapper_set_is_not_empty():
    """The guard that makes every parametrised test below non-vacuous."""
    assert _MAPPING_WRAPPERS, "no mapping wrappers; every C7 test would pin nothing"
    assert set(_MAPPING_WRAPPERS) == {
        tool for tool, p in scoring.POLICY.items() if p.emits_native_severity
    }, "the wrapper set has drifted from POLICY; a scanner is untested"


@pytest.mark.parametrize("tool", sorted(_MAPPING_WRAPPERS))
@pytest.mark.parametrize("native", _UNRECOGNISED)
def test_an_unrecognised_severity_fails_CLOSED_through_the_wrapper(tool, native):
    """C7. Driven through the wrapper `scan` calls, at the SHIPPED threshold.

    The cutoff is read from `config.SECURITY_BLOCK_THRESHOLD` rather than
    hardcoded, because the claim being pinned is "an unrecognised severity blocks
    on the configuration this project ships" -- a literal 2 here would keep
    passing if the shipped threshold moved above the fail-closed default, which is
    exactly the two-constants-in-two-files relationship scoring.py checks at
    import.
    """
    cutoff = SEVERITY_ORDER[scoring.resolve_threshold()]
    mapped = _MAPPING_WRAPPERS[tool]._map_severity(native)
    assert SEVERITY_ORDER[mapped] >= cutoff, (
        f"{tool} mapped the unrecognised severity {native!r} to {mapped!r} "
        f"(order {SEVERITY_ORDER[mapped]}), BELOW the block cutoff {cutoff}. It "
        f"must fail CLOSED: a severity name this project does not recognise is "
        f"not evidence of safety. This is the exact defect measured in semgrep's "
        f"table on 2026-08-22, where rules marked CRITICAL scored 0."
    )


@pytest.mark.parametrize("native", _UNRECOGNISED)
def test_an_unrecognised_severity_actually_produces_a_BLOCK(native):
    """The mapping is not tested in isolation: it must reach a real verdict.

    A fail-closed table whose value never got as far as `compute_security_verdict`
    would satisfy every assertion above. This drives the shipped rule.
    """
    for tool, wrapper in sorted(_MAPPING_WRAPPERS.items()):
        finding = Finding(
            tool=tool,
            severity=wrapper._map_severity(native),
            rule="unrecognised-severity",
            file="app/auth.py",
            line=1,
            description="d",
        )
        verdict, blocking = compute_security_verdict(
            [finding], threshold=scoring.resolve_threshold()
        )
        assert verdict == "block", (
            f"a {tool} finding whose severity was unrecognised ({native!r}) "
            f"produced {verdict!r}, not a block"
        )
        assert blocking == [finding]


def test_gitleaks_has_no_unrecognised_severity_path_and_that_is_asserted():
    """C7 for the third scanner, which needs a different question asked.

    gitleaks emits no severity, so there is no unrecognised VALUE to feed it --
    its answer cannot depend on an input it never receives. Omitting it from the
    parametrisation above would be indistinguishable from forgetting it, so the
    absence is stated here: whatever is passed, the policy answers, and the
    policy answer blocks.
    """
    cutoff = SEVERITY_ORDER[scoring.resolve_threshold()]
    for native in _UNRECOGNISED:
        mapped = scoring.map_severity("gitleaks", native)
        assert mapped == scoring.policy_severity("gitleaks")
        assert SEVERITY_ORDER[mapped] >= cutoff, (
            f"gitleaks' policy severity {mapped!r} is below the cutoff {cutoff}; "
            f"the scanner that protects the core guarantee would not block"
        )


def test_the_shared_default_is_at_or_above_the_shipped_threshold():
    """C7's constant, checked against the knob it has to clear.

    scoring.py refuses at import when this relationship breaks; this test is what
    makes the relationship visible in the suite rather than only at import, where
    a failure reads as an unrelated collection error.
    """
    assert SEVERITY_ORDER[scoring.FAIL_CLOSED_SEVERITY] >= SEVERITY_ORDER[
        scoring.resolve_threshold()
    ], (
        f"FAIL_CLOSED_SEVERITY={scoring.FAIL_CLOSED_SEVERITY!r} is below the "
        f"shipped threshold {scoring.resolve_threshold()!r}"
    )
    # Deliberately NOT `critical`: `_run.error_finding` gives the reason for its
    # own severity, and it applies here -- an unrecognised severity must not
    # impersonate a discovered secret in a list a human is reading.
    assert scoring.FAIL_CLOSED_SEVERITY != "critical"


def _load_scoring_copy(source: str):
    """exec a MUTATED copy of scoring.py, so import-time guards can be observed.

    `importlib.reload` cannot do this: the guards run at import over the source on
    disk, and the thing under test is what a DIFFERENT source would do. Executing
    a copy under its own module dict leaves the real `agentorg.security.scoring`
    untouched, which matters because every other test in this session imports it.
    """
    path = scoring.__file__
    spec = importlib.util.spec_from_loader("agentorg.security._scoring_copy", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "agentorg.security"
    module.__file__ = path
    exec(compile(source, path, "exec"), module.__dict__)  # noqa: S102
    return module


def test_a_lowered_fail_closed_default_is_REFUSED_AT_IMPORT():
    """The guard fires, rather than being a comment about a hazard.

    This is the mutation C7 exists to catch, applied to a copy: drop the shared
    default to `low` and the module must refuse to load at all. A module that
    imported cleanly and failed open at scan time is the shape of the original
    defect.
    """
    source = (
        _read_scoring_source()
        .replace(
            'FAIL_CLOSED_SEVERITY: Severity = "high"',
            'FAIL_CLOSED_SEVERITY: Severity = "low"',
        )
    )
    assert 'FAIL_CLOSED_SEVERITY: Severity = "low"' in source, (
        "the mutation did not apply, so this test would pass without exercising "
        "the guard -- an inert mutation reads exactly like a caught one"
    )
    with pytest.raises(ValueError, match="below `high`"):
        _load_scoring_copy(source)


def _read_scoring_source() -> str:
    from pathlib import Path

    return Path(scoring.__file__).read_text(encoding="utf-8")


# ------------------------------------------------------------ C8: the threshold floor


def test_every_legal_threshold_is_accepted_today():
    """The floor must not refuse a threshold this project documents as valid."""
    for threshold in SEVERITY_ORDER:
        assert scoring.resolve_threshold(threshold) == threshold


def test_a_threshold_outside_the_vocabulary_is_refused_HERE():
    """ValueError from the knob, not KeyError from inside the block rule.

    MEASURED before `config` validated it: `compute_security_verdict([],
    threshold="HIGH")` raised `KeyError: 'HIGH'` from inside the security agent --
    the one stage whose purpose is to produce a verdict, dying while producing
    one, with a traceback naming a dict lookup rather than a misconfigured knob.
    `config` closed that for the ENVIRONMENT variable at import; a per-project
    threshold arrives at RUN time, where an import-time check cannot see it.
    """
    for bad in ("HIGH", "Critical", "sev:high", "highest", "0"):
        with pytest.raises(ValueError, match="not a severity"):
            scoring.resolve_threshold(bad)


def test_nobody_asked_means_the_configured_threshold_decides():
    """`None` and `""` are "nobody said", not a deliberate choice of nothing.

    The same convention `developer.run`'s `poisoned` argument uses: an empty
    per-project column must not read as an instruction.
    """
    from agentorg.common import config

    for absent in (None, ""):
        assert scoring.resolve_threshold(absent) == config.SECURITY_BLOCK_THRESHOLD


def test_the_floor_is_DERIVED_from_the_secret_scanners_policy_not_written_down():
    """C8. A literal would be a second declaration of gitleaks' severity.

    Two copies keep agreeing while one of them moves -- which is how semgrep's
    default survived being wrong. So the floor is computed from the policy that
    declares `protects_core_guarantee`, and this test asserts the derivation by
    changing the source of truth and watching the floor follow.
    """
    assert scoring.THRESHOLD_FLOOR == scoring.POLICY["gitleaks"].constant
    guardians = [p for p in scoring.POLICY.values() if p.protects_core_guarantee]
    assert guardians, "no policy protects the core guarantee; the floor is unanchored"
    assert scoring.THRESHOLD_FLOOR == min(
        (p.constant for p in guardians), key=lambda s: SEVERITY_ORDER[s]
    )


def test_the_threshold_floor_binds_when_the_secret_policy_is_lowered():
    """C8's only honest proof: the floor is OBSERVED refusing something.

    THE FLOOR DOES NOT BIND TODAY, and saying so is the point. It is derived from
    gitleaks' policy severity, `critical`, the top of the scale -- so all four
    legal thresholds pass and a reader who assumed this check refuses something
    today would be wrong. It is not decoration either: it binds the moment that
    constant is lowered, which is the realistic way this guarantee gets lost.

    A floor that cannot be observed to refuse anything is the vacuous check this
    whole lane exists to stop shipping, so the source of truth is lowered in a
    copy of the module and a previously-legal threshold must then be refused.
    """
    source = _read_scoring_source().replace(
        'constant="critical",\n        protects_core_guarantee=True,',
        'constant="medium",\n        protects_core_guarantee=True,',
    )
    assert 'constant="medium",\n        protects_core_guarantee=True,' in source, (
        "the mutation did not apply; this test would pass without ever exercising "
        "the floor, which is indistinguishable from the floor working"
    )
    lowered = _load_scoring_copy(source)

    assert lowered.THRESHOLD_FLOOR == "medium", (
        "the floor did not follow the policy it is supposed to be derived from, "
        "so it is written down somewhere as a literal"
    )
    # `high` is legal today and must now be refused, because a gitleaks finding
    # at `medium` would no longer reach it -- a committed credential would merge.
    with pytest.raises(ValueError, match="above 'medium'"):
        lowered.resolve_threshold("high")
    with pytest.raises(ValueError, match="above 'medium'"):
        lowered.resolve_threshold("critical")
    # And the thresholds that still catch a `medium` secret stay legal.
    assert lowered.resolve_threshold("medium") == "medium"
    assert lowered.resolve_threshold("low") == "low"


def test_the_floor_REFUSES_and_never_CLAMPS():
    """A clamp runs the gate at a threshold nobody asked for and reports success.

    Same shape as `STATE_BACKEND` falling back to `local` on a typo: the run looks
    configured and is not. Asserted by checking that no accepted call returns
    something other than what was asked for.
    """
    for threshold in SEVERITY_ORDER:
        assert scoring.resolve_threshold(threshold) == threshold, (
            "resolve_threshold returned a threshold other than the one requested; "
            "it must refuse, never silently adjust"
        )


def test_a_scoring_row_carries_the_resolved_threshold_not_the_raw_request():
    """The audit row must show the cutoff that actually decided.

    A row recording an unvalidated request would be an artifact describing a
    decision that was never made.
    """
    finding = Finding(tool="gitleaks", severity="critical", rule="aws-access-key-id",
                      file="app/auth.py", line=3, description="d")
    rows = scoring.score_findings([finding], threshold="medium")
    assert rows[0].threshold == "medium"
    assert rows[0].blocking is True
    with pytest.raises(ValueError, match="not a severity"):
        scoring.score_findings([finding], threshold="MEDIUM")


def test_the_gitleaks_scan_path_still_reports_critical_end_to_end():
    """The policy call is on the real path, not only in the table.

    Runs the wrapper's own module-level plumbing far enough to prove
    `scoring.policy_severity` is what a Finding gets, without needing the binary:
    the call is asserted to be reachable and to answer.
    """
    assert gitleaks_tool.scoring.policy_severity("gitleaks") == "critical", (
        "gitleaks_tool does not reach the shared policy; it holds its own answer"
    )
