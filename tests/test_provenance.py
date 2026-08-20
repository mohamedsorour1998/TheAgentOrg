"""The provenance discriminator, tested against both modes. Owner: Aya.

An instrument that cannot report the failing case is not an instrument. This repo
has shipped two that could not: a recorder patched onto a seam a fixture had
already replaced, reporting a reassuring zero; and a same-size edit inside one
mtime second that left CPython serving stale bytecode. tests/provenance.py is
about to label every row of the DORA table, so it is pinned here first.

BLACK-BOX vs INSIDE-OUT: two of these tests are black-box -- they call
graph.run_pipeline and read only the returned RunState, which is the view
tests/test_scanner_resilience.py's 1941 inside-out lines do not cover. The other
five are pure-Python checks on the discriminator itself.

Run: pytest -q tests/test_provenance.py
"""

import pytest

from agentorg import graph
from agentorg.state import Finding, RunState, SecurityResult
from tests import provenance as prov

TICKET_TEXT = "Add a per-IP login rate limit."


def _state_with_lines(first: int, second: int) -> RunState:
    """A RunState carrying two AWS findings at chosen line numbers."""
    findings = [
        Finding(tool="gitleaks", severity="critical", rule="aws-access-key-id",
                file="app/auth.py", line=first, description="access key"),
        Finding(tool="gitleaks", severity="critical", rule="aws-secret-access-key",
                file="app/auth.py", line=second, description="secret key"),
    ]
    state = RunState(ticket_id="P", ticket_text=TICKET_TEXT)
    state.security = SecurityResult(verdict="block", findings=findings,
                                    blocking=findings, explanation="x")
    return state


def test_the_fixture_lines_and_the_real_scanner_lines_are_not_identical():
    """The whole discriminator rests on this. If they ever coincide, it is dead.

    NOT "do not overlap" -- they DO overlap, at line 4. That is the measured
    reason no single-line observation can tell the two modes apart and only the
    whole set can. What the discriminator needs is that the two sets are not
    IDENTICAL, which is what `!=` asserts. Do not "tighten" this to
    `isdisjoint()`: that would assert something false and break a correct pin.
    """
    assert prov.FIXTURE_LINES != prov.REAL_SCANNER_LINES
    assert prov.FIXTURE_LINES == frozenset({4, 5})
    assert prov.REAL_SCANNER_LINES == frozenset({3, 4})


def test_fixture_line_numbers_are_recognised_as_the_fixture():
    """Runs in whatever mode this machine is in; only meaningful without binaries."""
    if prov.binaries_installed():
        pytest.skip(f"needs a machine with no scanners: {prov.describe_mode()}")
    assert prov.answered_from_fixture(_state_with_lines(4, 5)) is True


def test_real_scanner_line_numbers_raise_when_no_binaries_are_installed():
    """The instrument must REFUSE rather than guess when the signals disagree."""
    if prov.binaries_installed():
        pytest.skip(f"needs a machine with no scanners: {prov.describe_mode()}")
    with pytest.raises(RuntimeError, match="no binaries are on PATH"):
        prov.answered_from_fixture(_state_with_lines(3, 4))


def test_unknown_line_numbers_raise_rather_than_defaulting():
    """A third line pair means both pins are stale. Refuse, do not pick one."""
    with pytest.raises(RuntimeError, match="match neither"):
        prov.answered_from_fixture(_state_with_lines(11, 12))


def test_a_real_pipeline_run_is_labelled_by_the_discriminator(provenance):
    """End to end: the label the DORA table will print must match reality."""
    if prov.binaries_installed():
        pytest.skip(f"needs a machine with no scanners: {prov.describe_mode()}")
    provenance.none_installed()

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    assert state.status == "blocked"
    assert prov.answered_from_fixture(state) is True
    assert prov.answered_from_real_scanners(state) is False


def test_the_fake_scanner_directory_keeps_git_reachable(provenance):
    """THE MEASURED TRAP: replacing PATH breaks github_ops' real git calls.

    A fake-binary directory that REPLACES PATH makes run_pipeline die at
    github_ops.py:114 with FileNotFoundError for 'git', before the security
    stage. Prepending keeps git reachable. This test is what stops someone
    "simplifying" Provenance._activate into a setenv without prepend.
    """
    provenance.all_broken()

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    # It got past open_pr, which means git resolved.
    assert state.dev is not None and state.dev.pr_url, (
        "open_pr must have run; if this is None, PATH was replaced rather than "
        "prepended and git was unreachable"
    )
    assert state.status == "blocked"
    assert {f.rule for f in state.security.blocking} == {
        "semgrep-scanner-error", "gitleaks-scanner-error", "trivy-scanner-error",
    }


def test_describe_mode_names_the_ambient_mode():
    """Used in skip messages and in the DORA report header, so it must be true."""
    described = prov.describe_mode()
    if prov.binaries_installed():
        assert "REAL-SCANNER" in described or "HALF-PROVISIONED" in described
    else:
        assert "FIXTURE-FALLBACK" in described
