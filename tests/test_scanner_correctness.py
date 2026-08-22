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

import pytest

from agentorg.security import semgrep_tool, trivy_tool
from agentorg.state import SEVERITY_ORDER, compute_security_verdict

BLOCK_CUTOFF = SEVERITY_ORDER["high"]


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
