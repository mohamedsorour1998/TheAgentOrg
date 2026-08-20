"""Chaos: broken scanners, seen from OUTSIDE the pipeline. Owner: Aya.

Pairs with Habiba's agentorg/security/_run.py and uses her vocabulary: a binary
that is ABSENT is a development and CI affordance that keeps the fixture-fallback
path; one that is present and BROKEN is a FAULT that must block. "Failing OPEN"
is her name for the gate passing bad code because the gate did not run.

WHICH SIDE OF THE LINE THIS FILE SITS ON. tests/test_scanner_resilience.py
covers every fault from the INSIDE -- it calls gitleaks_tool.scan,
run_all_scanners and security_agent.run directly, and asserts on findings,
severities and raised exceptions. This file adds nothing to that. Every
assertion here is BLACK BOX: it drives `graph.run_pipeline` end to end and reads
only the returned RunState -- status, security.verdict, the blocking list, and
whether the run reached SRE. Each test names its side explicitly below. The one
exception is test 4, which patches a seam BELOW the security agent so the
agent's real fallback, the real block rule and the real graph all still run;
that is still an outside-in test, just with the fan-out stubbed.

MEASURED ANSWER, and it is better news than expected: the poisoned ticket ends
`blocked` in every provenance mode. What MOVES is the count -- len(blocking) is
2 when the FIXTURE answered and 3 when three scanner faults were reported. So
`== 2` is a provenance-dependent assertion, which is why every test here that
depends on a mode declares it through the `provenance` fixture rather than
inheriting whatever the machine happens to have installed.

TWO TRAPS THAT WOULD MAKE THESE TESTS PASS WHILE MEASURING NOTHING.

1. The knob is a module-level constant, evaluated at import time
   (agentorg/common/config.py:83) and read as a module attribute at
   agentorg/security/_run.py:520. So it is flipped with
   `monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)`.
   `monkeypatch.setenv("SCANNERS_REQUIRED", "true")` would toggle NOTHING --
   config was imported long before the fixture ran -- and test 5 would go green
   while exercising the knob-off path it claims to be the counter-example to.
2. PATH must be PREPENDED, never replaced. github_ops.open_pr shells out to real
   `git` on the offline path conftest.py forces on every test, so a replaced
   PATH kills run_pipeline with FileNotFoundError on 'git' before security is
   reached. tests/provenance.py does the prepending once so nothing repeats it.

IMPORT PATH CONSTRAINT: the `provenance` fixture imports tests.provenance, and
tests/ has no __init__.py. pyproject.toml sets pythonpath = ["."], so that works
under pytest and under `python -m` from the repository ROOT, and nowhere else.

Run: pytest -q tests/test_chaos_scanner.py
"""

from agentorg import graph
from agentorg.agents import security
from agentorg.common import config

TICKET_TEXT = "Add a per-IP login rate limit."

FAULT_RULES = {
    "semgrep-scanner-error",
    "gitleaks-scanner-error",
    "trivy-scanner-error",
}


def test_the_poisoned_ticket_blocks_even_when_every_scanner_is_broken(provenance):
    """A FAULT must never become a silent pass, through the whole pipeline.

    BLACK BOX: drives run_pipeline, reads only the RunState. The inside view of
    the same fault -- that each wrapper turns a non-zero exit into one
    error_finding -- is test_scanner_resilience.py's, and is not repeated here.

    All three binaries present and exiting non-zero. Habiba's wrappers turn each
    into a blocking error_finding at severity "high", compute_security_verdict
    blocks on it, and the graph halts. No fixture is involved: this is the real
    scanner path, faulting.
    """
    provenance.all_broken()

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    assert state.status == "blocked"
    assert state.security.verdict == "block"
    assert state.sre is None, "a blocked run must never reach SRE"

    # WHICH mechanism blocked it, not merely that something did. Three
    # scanner-error findings mean the wrappers reported faults; two AWS-key
    # findings would mean the FIXTURE answered and this test proved nothing
    # about a broken scanner. That distinction is the whole point of the
    # assertion -- see the RED step, which weakens it to `status == "blocked"`
    # and shows the test then passes in the wrong mode.
    rules = {f.rule for f in state.security.blocking}
    assert rules == FAULT_RULES, (
        f"expected three reported faults; got {sorted(rules)}. If these are the "
        f"aws-* rules, the fixture fallback answered and no scanner fault was "
        f"exercised at all."
    )


def test_a_broken_scanner_blocks_a_CLEAN_change_too(provenance):
    """The fail-closed direction, on the ticket where it is visible.

    BLACK BOX: run_pipeline end to end on the clean ticket. The severity literal
    is asserted directly from the inside at test_scanner_resilience.py:225; what
    is new here is that it survives the whole pipeline and reaches state.status.

    On a poisoned diff, "blocked" is the right answer for two independent
    reasons, so it cannot distinguish a working gate from a broken one. On a
    CLEAN diff there is nothing to find, so blocking can only come from the
    faults -- which makes this the assertion that proves the fault reached the
    verdict rather than the credentials did.
    """
    provenance.all_broken()

    state = graph.run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)

    assert state.status == "blocked", (
        "three faulting scanners must block a clean change: the gate reports "
        "that it could not scan, rather than reporting that it found nothing"
    )
    assert len(state.security.blocking) == 3
    assert all(f.severity == "high" for f in state.security.blocking)
    # Stated, not inferred. all_broken() on a CLEAN diff can only produce fault
    # findings, so the count above is already sufficient -- but naming the set
    # makes that self-evident and matches how tests 1 and 5 discriminate.
    assert {f.rule for f in state.security.blocking} == FAULT_RULES


def test_the_poisoned_ticket_blocks_with_no_scanners_installed(provenance):
    """The ABSENT path -- how CI and every laptop on this team actually runs.

    BLACK BOX, and it is the one test here whose job is to LABEL a mode rather
    than to find a bug. This is the mode Aya's week-1 determinism test has
    always run in, and it is named here so the fact is written down somewhere:
    the verdict comes from fixtures/security_result_block.json and
    compute_security_verdict is never called. The block is real; its PROVENANCE
    is a fixture. `len(blocking) == 2` is therefore a claim about that JSON
    file, not about the block rule.
    """
    provenance.none_installed()

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    assert state.status == "blocked"
    assert len(state.security.blocking) == 2
    assert provenance.answered_from_fixture(state), (
        "with no binaries installed the fixture must be what answered; if this "
        "is False, someone installed scanners and this test is silently "
        "measuring the other mode"
    )


def test_a_blind_scanner_is_the_one_way_the_poison_could_ship(monkeypatch):
    """The boundary, asserted as a boundary rather than as an expectation.

    compute_security_verdict([]) returns ("pass", []) -- measured. So a fan-out
    that returned zero findings on a poisoned diff would promote it. Habiba's
    module exists to make that unreachable, and this test pins the CONSEQUENCE
    from the outside so the reason that guarantee matters stays visible.

    WHICH SEAM ACTUALLY GUARANTEES IT, MEASURED -- and it is NOT the obvious one.
    `unrunnable_findings` (never [] -- either [error_finding(...)] or raises)
    covers the ABSENT path only. Under the fault mode the other tests here use,
    `provenance.all_broken()`, the fakes `exit 2`, which means the command RAN:
    `result is None` is false, so `unrunnable_findings` is NEVER REACHED. Each
    wrapper handles that case in its own `returncode not in (0, 1)` branch and
    returns [error_finding(...)] directly -- gitleaks_tool.py:124,
    semgrep_tool.py:119, trivy_tool.py:103. Verified by instrumenting both
    branches: zero hits on the fault path.

    This matters to anyone mutating code to check these tests can fail. The
    plan's own RED step aimed at `unrunnable_findings`' fault branch and ALL
    FIVE TESTS STAYED GREEN, because that branch is dead with respect to them.
    The reachable seam is the wrappers' bad-exit branch; mutating THAT to return
    [] reddens two tests and promotes the poisoned ticket.

    The patch is on `security.run_all_scanners`, i.e. BELOW the security agent,
    so the agent's real code, its real fixture fallback, the real block rule and
    the real graph all still run. Patching `graph.security.run` instead -- which
    the original spec did -- bypasses every one of them and asserts only that a
    returned value is returned. That is why this is still an outside-in test.

    No provenance mode is needed: the fan-out never executes, so no binary is
    consulted and the result is the same in all three modes.
    """
    monkeypatch.setattr(security, "run_all_scanners", lambda dev: [])

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    # This IS the fail-open, and it is asserted so that a future fix is visible
    # as a red test with a note telling the reader what to do -- not as a
    # surprise.
    assert state.status == "promoted", (
        "an empty findings list on a poisoned diff currently promotes. If this "
        "is red, either a guard now rejects an empty scan or the graph no "
        "longer promotes on a pass verdict. The first is a FIX: replace this "
        "test with one asserting the new blocking behaviour. The second is a "
        "regression somewhere else -- check the graph before touching this test."
    )
    assert state.security.verdict == "pass"
    assert state.security.blocking == []


def test_scanners_required_with_no_binaries_blocks_the_CLEAN_run_too(
    provenance, monkeypatch
):
    """THE CONFIGURATION TRAP, pinned from the outside.

    BLACK BOX. The promotion itself -- SCANNERS_REQUIRED turning ABSENT into a
    blocking finding -- is already asserted from the inside in
    test_scanner_resilience.py. What is new is the end-to-end CONSEQUENCE: which
    half of the demo the knob breaks, and in what way.

    On a machine with the binaries installed the knob is exactly right. On a
    machine WITHOUT them it converts a fail-open into a fail-everything: the
    clean half of the demo blocks. So the two demo-prep actions are an ORDERED
    PAIR -- install the binaries, THEN set the knob. This test makes that
    ordering a fact in the suite instead of a sentence in a runbook.

    The knob is set with setattr on the module, not setenv: config.py evaluates
    SCANNERS_REQUIRED once at import time, so an env var set here would change
    nothing and this test would silently measure the knob-off path.
    """
    provenance.none_installed()
    monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)

    clean = graph.run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)

    assert clean.status == "blocked", (
        "with the knob on and no binaries installed, the CLEAN run blocks -- "
        "three absent scanners promoted to faults. This is the trap: the knob "
        "is only safe once the binaries are installed."
    )
    assert {f.rule for f in clean.security.blocking} == FAULT_RULES

    # And the poisoned run still blocks -- but on three faults, not on the two
    # AWS findings the demo script narrates. Both halves of the demo are wrong
    # in this configuration, in different ways.
    poisoned = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)
    assert poisoned.status == "blocked"
    assert len(poisoned.security.blocking) == 3, (
        "the narrated line is 'blocking=2, the access key and the secret key'; "
        "in this configuration it is 3 scanner errors instead"
    )
