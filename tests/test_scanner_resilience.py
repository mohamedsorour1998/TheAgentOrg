"""Trivy earns its place in the fan-out: it must catch a vulnerable pin.

OWNER: Habiba (agentorg/security/). This is her week-2 done-when written as a
test rather than a feature -- `trivy_tool.scan` already works; nothing here
asks it to do anything new.

WHY THIS FILE EXISTS
    Trivy contributes ZERO findings to both demo fixtures, measured: the
    wrappers materialize only the added lines of a diff, and neither fixture
    adds a dependency manifest for trivy to find CVEs in. So of the three
    scanners `run_all_scanners` fans out to, trivy is the only one with no
    assertion behind its OUTPUT -- scripts/scan_gate.py pins gitleaks' two
    critical findings exactly and requires at least one from semgrep, but for
    trivy it can only check that the binary was executed. It is also the only
    one that pulls a ~108 MB vulnerability database, which is why the `test`
    job in CI deliberately installs no scanners at all.

    This file is what earns trivy that download: it shows trivy blocking a
    change on its own findings, with no help from the other two.
"""

import shutil

import pytest

from agentorg import fixtures_loader
from agentorg.common import config
from agentorg.security import trivy_tool
from agentorg.state import SEVERITY_ORDER, DevResult, compute_security_verdict

# A diff that ADDS a dependency manifest pinning two long-known-vulnerable
# releases. Added lines only, because that is all the materialiser in
# common/diff.py hands a scanner -- see its module docstring. `requirements.txt`
# is load-bearing: `trivy fs` finds CVEs by recognising a manifest FILENAME, so
# the same pins in a file called anything else produce nothing.
#
# Measured with real trivy 0.74.0 (database of 2026-08-18): 9 findings, 4 of
# them `high`. Neither the count nor the CVE ids are asserted below and they
# must not be -- trivy's database updates daily, so a test pinned to today's
# ids fails on a random Tuesday on code nobody touched. What is asserted is the
# only property the pipeline actually consumes: a severity that reaches the
# block threshold.
VULNERABLE_PIN_DIFF = (
    "--- /dev/null\n"
    "+++ b/requirements.txt\n"
    "@@ -0,0 +1,2 @@\n"
    "+flask==0.5\n"
    "+requests==2.6.0\n"
)


def _summarize(findings: list) -> str:
    """One line per finding, for a failure message that says what trivy saw."""
    if not findings:
        return "(no findings)"
    return "; ".join(f"{f.tool}:{f.rule}({f.severity})" for f in findings)


@pytest.mark.skipif(
    shutil.which("trivy") is None,
    reason="trivy is not on PATH; see this test's docstring -- the skip is expected",
)
def test_trivy_blocks_a_vulnerable_pin_and_stays_silent_on_the_demo_fixtures():
    """Trivy must block a change that adds a vulnerable pin -- and only that.

    WHY THIS SKIPS RATHER THAN FAILS WITHOUT THE BINARY, AND WHY THAT IS NOT A GAP
        CI's `test` job installs no scanners on purpose (see the comment on its
        "Run tests" step): with nothing on PATH every wrapper raises
        FileNotFoundError, agents/security.py falls back to the fixture verdict,
        and the suite stays a fast offline unit run instead of a 48-second job
        pulling a vulnerability database on every push. A hard failure here
        would therefore be a false alarm about a deliberate choice. The real
        binaries live in the `scan` job, and this assertion is reproducible by
        hand with `trivy --version && pytest -q tests/test_scanner_resilience.py`.

    BOTH HALVES ARE ONE TEST ON PURPOSE. Either alone is satisfied by broken
    code, so they must not be separable:

      * Half 1 alone -- a `scan()` that returned every CVE in the database
        unconditionally, or simply a hardcoded `high` finding, would block the
        vulnerable pin and pass.
      * Half 2 alone -- a `scan()` that returned `[]` always, which is what a
        silently broken wrapper looks like, would report zero on both fixtures
        and pass. That is the exact failure this lane keeps closing:
        compute_security_verdict([]) returns "pass".

    Together they say trivy discriminates: it fires on a vulnerable dependency
    and it is quiet on changes that add none.
    """
    threshold = config.SECURITY_BLOCK_THRESHOLD
    cutoff = SEVERITY_ORDER[threshold]

    # --- half 1: a vulnerable pin must block, on trivy's findings alone -----
    vulnerable = trivy_tool.scan(
        DevResult(
            branch="feat/add-deps",
            diff=VULNERABLE_PIN_DIFF,
            summary="pin flask and requests",
            files_changed=["requirements.txt"],
        )
    )

    assert vulnerable, (
        "trivy reported nothing on a manifest pinning flask==0.5 and "
        "requests==2.6.0. Either the wrapper is broken or its database is "
        "empty -- and an empty findings list is a PASS to "
        "compute_security_verdict, so this cannot be allowed to look clean."
    )
    assert all(f.tool == "trivy" for f in vulnerable), (
        f"trivy_tool.scan must tag every finding tool='trivy'; got "
        f"{_summarize(vulnerable)}"
    )

    at_or_above = [f for f in vulnerable if SEVERITY_ORDER[f.severity] >= cutoff]
    assert at_or_above, (
        f"no trivy finding reached the block threshold {threshold!r}, so the "
        f"vulnerable pin would sail through the gate. Findings were: "
        f"{_summarize(vulnerable)}"
    )

    # The claim the pipeline actually consumes: this blocks with no help from
    # gitleaks or semgrep. Asserted through the real rule in state.py rather
    # than restating it here, so a change to that rule is visible from trivy's
    # side too.
    verdict, blocking = compute_security_verdict(vulnerable, threshold=threshold)
    assert verdict == "block", (
        f"trivy's findings alone must block at threshold {threshold!r}; got "
        f"{verdict!r} from {_summarize(vulnerable)}"
    )
    assert blocking, "a 'block' verdict with an empty blocking list is incoherent"

    # --- half 2: the negative control, on both demo fixtures ---------------
    for poisoned in (False, True):
        fixture_name = "poisoned" if poisoned else "clean"
        findings = trivy_tool.scan(fixtures_loader.dev(poisoned=poisoned))

        assert findings == [], (
            f"the {fixture_name} demo fixture must yield ZERO trivy findings -- "
            f"it adds no dependency manifest. Got {len(findings)}: "
            f"{_summarize(findings)}. Without this, a scan() returning "
            f"everything unconditionally would satisfy the first half of this "
            f"test, and scripts/scan_gate.py's expected-findings pins "
            f"(gitleaks' two criticals on the poisoned diff, nothing blocking "
            f"on the clean one) would go red next."
        )
