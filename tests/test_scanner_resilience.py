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

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from agentorg import fixtures_loader
from agentorg.common import config
from agentorg.security import trivy_tool
from agentorg.security._run import error_finding, safe_run
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
    #
    # WHY THE FIXTURES YIELD ZERO -- read this before "fixing" a red here.
    # TWO separate mechanisms produce it, and only one of them is obvious:
    #
    #   1. No dependency manifest. Neither fixture adds a requirements.txt, so
    #      trivy's VULN scanner has nothing to resolve CVEs against.
    #   2. The wrapper reads only `Results[].Vulnerabilities`. `trivy fs`
    #      defaults to `--scanners vuln,secret` (verified on 0.74.0: the flag's
    #      default is `[vuln,secret]`), so the SECRET scanner is active on every
    #      call the pipeline makes -- and the poisoned fixture is a file full of
    #      AWS credentials. It reports nothing today only because
    #      AKIAIOSFODNN7EXAMPLE and its partner are AWS's own documentation
    #      example keys, which trivy allowlists. Measured on 0.74.0: the same
    #      two lines with a fake non-example key of identical shape make the RAW
    #      report emit `Class=secret` with two CRITICAL secret findings -- and
    #      `trivy_tool.scan` STILL returns zero Finding objects, because
    #      `Secrets` is not `Vulnerabilities` and the parser never looks at it.
    #
    # So the zero does NOT rest on the fixtures being structurally invisible to
    # trivy, which is what an earlier version of this comment claimed. It rests
    # on mechanism 2: the wrapper's parse. Mechanism 1 plus the allowlist is
    # what keeps the raw report empty, and both of those are mutable -- a
    # fixture refresh that swaps in a different credential placeholder is a far
    # more plausible trigger than the CVE database drifting.
    #
    # That parser-level insulation is itself UNASSERTED anywhere in this repo.
    # If secret findings should ever start reaching the verdict, this assertion
    # is the one that goes red first, and the fix is a decision about
    # trivy_tool's parse -- not a number to relax here. gitleaks already covers
    # the credentials on the poisoned fixture (two criticals, pinned exactly by
    # scripts/scan_gate.py), so nothing is being missed meanwhile.
    for poisoned in (False, True):
        fixture_name = "poisoned" if poisoned else "clean"
        findings = trivy_tool.scan(fixtures_loader.dev(poisoned=poisoned))

        # Strictly `== []`, not "nothing at or above the threshold". The strict
        # form is where the discrimination lives: a loosened variant still
        # catches a wrapper returning an unconditional `high`, but PASSES one
        # returning an unconditional `low`, and half 2 is the only assertion in
        # this repo covering trivy's output at all.
        assert findings == [], (
            f"the {fixture_name} demo fixture must yield ZERO trivy findings. "
            f"Got {len(findings)}: {_summarize(findings)}. See the comment "
            f"above for the two mechanisms that produce the zero -- if this is "
            f"a `secret`-class finding, the wrapper's parse changed, not the "
            f"fixture. Without this half, a scan() returning everything "
            f"unconditionally would satisfy the first half of this test, and "
            f"scripts/scan_gate.py's expected-findings pins (gitleaks' two "
            f"criticals on the poisoned diff, nothing blocking on the clean "
            f"one) would go red next."
        )


# ==========================================================================
# Task 2 -- the shared fail-safe subprocess runner (agentorg/security/_run.py)
#
# WHY THESE DO NOT SKIP, UNLIKE THE TEST ABOVE
#     The trivy test above needs a 161 MB binary and a CVE database, so it
#     skips when trivy is absent. Nothing below needs a scanner at all: the
#     fault modes are reproduced with `sys.executable` and a directory path,
#     both of which exist wherever pytest does. That matters because this
#     FILE never runs in CI -- verified against .github/workflows/ci.yml: the
#     `test` job runs pytest with no scanner binaries installed, and the
#     `scan` job installs all three but runs only scripts/scan_gate.py, never
#     pytest. So a skip here would be a second layer of "not actually
#     checked" on top of the first. These run on any machine, in either
#     scanner mode, and are the part of this file a laptop can trust.
# ==========================================================================


def test_error_finding_is_at_the_block_threshold_so_a_dead_scanner_fails_closed():
    """A scanner that could not run must BLOCK, on its own error finding alone.

    THIS IS THE LOAD-BEARING ASSERTION OF THE WHOLE RESILIENCE LANE.

    The failure it exists to prevent has a specific shape, and this project has
    closed it three separate times already: a scanner breaks, its findings list
    comes back empty, `compute_security_verdict([])` returns ("pass", []), and a
    poisoned change is promoted while every test in the suite stays green. That
    is failing OPEN -- the gate reports clean precisely because it did not run.

    `error_finding` is the fix: a fault becomes a FINDING rather than an
    absence. But a finding only blocks if its severity reaches the threshold,
    so the severity is not a cosmetic label -- it IS the fail-closed behaviour.
    Drop it from "high" to "medium" and this lane silently reverts to failing
    open: findings would still be produced, the verdict would still read
    "pass", and nothing but this test would notice.

    Both halves below are deliberate:
      * the literal severity, so a change to it names itself in the failure;
      * the verdict computed through the REAL compute_security_verdict at the
        REAL config threshold, so the assertion tracks the rule the pipeline
        actually runs instead of restating it here. If the two disagree, the
        messages tell you which one moved.
    """
    finding = error_finding("gitleaks", "binary is missing from PATH")

    assert finding.severity == "high", (
        f"error_finding must be 'high': that is the block threshold, so it is "
        f"what makes an unrunnable scanner fail CLOSED. Got "
        f"{finding.severity!r}. Lowering this does not merely weaken a "
        f"warning -- it restores the silent-pass bug this lane exists to "
        f"prevent, because compute_security_verdict would then return 'pass' "
        f"for a scanner that never ran."
    )

    threshold = config.SECURITY_BLOCK_THRESHOLD
    assert SEVERITY_ORDER[finding.severity] >= SEVERITY_ORDER[threshold], (
        f"error_finding severity {finding.severity!r} sits BELOW the "
        f"configured block threshold {threshold!r}, so a dead scanner would "
        f"report a finding and still be promoted. If the threshold was raised "
        f"deliberately, error_finding has to be raised with it."
    )

    verdict, blocking = compute_security_verdict([finding], threshold=threshold)
    assert verdict == "block", (
        f"one scanner-error finding must block on its own at threshold "
        f"{threshold!r}; got {verdict!r}. A scanner that cannot run is not a "
        f"clean scan."
    )
    assert blocking == [finding], (
        f"the error finding itself must appear in the blocking list -- it is "
        f"what the PR comment and the projector name as the reason. Got "
        f"{_summarize(blocking)}"
    )


def test_error_finding_names_the_tool_that_failed():
    """The rule id has to identify WHICH scanner died, per tool.

    Task 3 emits one of these per wrapper. If they all rendered the same rule
    string, the block explanation on screen would say a scanner failed without
    saying which, and three simultaneous faults would look like one.
    """
    for tool in ("gitleaks", "semgrep", "trivy"):
        finding = error_finding(tool, "exit code 2: database is locked")

        assert finding.tool == tool, (
            f"error_finding({tool!r}, ...) tagged tool={finding.tool!r}"
        )
        assert finding.rule == f"{tool}-scanner-error", (
            f"Task 3 and the plan both name this rule "
            f"f'{{tool}}-scanner-error'; got {finding.rule!r}"
        )
        assert "database is locked" in finding.description, (
            f"the reason passed in must survive into the description -- it is "
            f"the only place an operator learns why the scanner failed. Got "
            f"{finding.description!r}"
        )


def test_safe_run_returns_none_for_a_missing_binary():
    """A binary that is not on PATH must return None, not raise.

    MEASURED, not assumed: subprocess.run on an absent binary raises
    FileNotFoundError, which is an OSError subclass (checked on CPython
    3.14.6). The point of pinning it from the outside is that `safe_run`'s
    caller must never have to know that, because the answer has changed before
    -- see the list of exception types agents/security.py enumerates in its
    broad except clause.
    """
    result = safe_run(
        ["agentorg-no-such-scanner-binary-4c1d9f"],
        timeout=config.SCANNER_TIMEOUT_SECONDS,
    )
    assert result is None, (
        f"safe_run must answer a missing binary with None so the caller can "
        f"decide between the fixture-fallback path and an error_finding. Got "
        f"{result!r}"
    )


def test_safe_run_returns_none_when_the_command_outruns_its_timeout():
    """A hung scanner must time out to None, not hang and not raise.

    MEASURED, not assumed: this raises subprocess.TimeoutExpired, which is a
    SubprocessError and is NOT an OSError (checked on CPython 3.14.6). So a
    handler catching only OSError -- the obvious guess after the missing-binary
    case -- would let a timeout escape. This test is what stops that guess from
    shipping.

    The child sleeps 30s against a 1s timeout: a margin wide enough that a
    loaded machine cannot make the command finish first, while the test itself
    still costs about a second. `sys.executable` rather than `sleep(1)` so the
    test has no dependency on PATH -- the whole point is that it runs
    everywhere, including the no-binary CI job.
    """
    result = safe_run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=1,
    )
    assert result is None, (
        f"safe_run must convert a timeout into None. Got {result!r}. If this "
        f"raised TimeoutExpired instead, note that it is a SubprocessError and "
        f"not an OSError, so it needs its own except clause."
    )


def test_safe_run_returns_none_for_an_os_error_that_is_not_a_missing_file():
    """Present-but-unrunnable is a distinct fault, and also returns None.

    MEASURED: handing subprocess.run a DIRECTORY raises PermissionError
    ([Errno 13]), a different OSError subclass from the missing-binary case.
    This is the shape of a scanner that is installed but not executable -- a
    lost +x bit after an unzip, a binary on a noexec mount. The plan's central
    ruling turns exactly this case into a blocking error_finding while leaving
    a merely ABSENT binary on the fixture-fallback path, so the two must not
    collapse into one code path by accident.
    """
    result = safe_run(
        [tempfile.gettempdir()], timeout=config.SCANNER_TIMEOUT_SECONDS
    )
    assert result is None, (
        f"safe_run must answer an OSError other than FileNotFoundError with "
        f"None too. Got {result!r}"
    )


def test_safe_run_returns_a_real_completed_process_for_a_command_that_works():
    """The negative control: safe_run must not swallow success.

    Without this, `def safe_run(...): return None` passes every other test in
    this section -- and Task 3 would report all three scanners as faulted on
    every run, which fails closed but blocks the clean fixture too, taking the
    demo's promote path down. Captured output is asserted because the wrappers
    parse stdout and stderr; a CompletedProcess with output discarded is
    useless to them.
    """
    result = safe_run(
        [sys.executable, "-c", "print('scanner output')"],
        timeout=config.SCANNER_TIMEOUT_SECONDS,
    )

    assert isinstance(result, subprocess.CompletedProcess), (
        f"a working command must come back as a real CompletedProcess so the "
        f"wrappers can read returncode/stdout/stderr; got {type(result)}"
    )
    assert result.returncode == 0, f"expected rc 0, got {result.returncode}"
    assert "scanner output" in result.stdout, (
        f"safe_run must CAPTURE output -- the wrappers parse it. stdout was "
        f"{result.stdout!r}"
    )
    assert isinstance(result.stdout, str), (
        "output must be decoded text, not bytes: every wrapper calls "
        ".strip() on stderr and json.loads on report text"
    )


def test_safe_run_reports_a_nonzero_exit_rather_than_hiding_it():
    """A non-zero exit is the scanner SPEAKING, not failing to run.

    gitleaks exits 1 when it finds secrets and semgrep exits 1 when it finds
    matches -- the poisoned demo depends on both. If safe_run treated non-zero
    as "could not run" and returned None, Task 3 would replace two real
    critical findings with a scanner-error and scripts/scan_gate.py's exact
    expected-findings pins would go red. `check=False` is therefore part of the
    contract, not an implementation detail.
    """
    result = safe_run(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        timeout=config.SCANNER_TIMEOUT_SECONDS,
    )

    assert result is not None, (
        "a non-zero exit is a completed run, not a failure to run; safe_run "
        "must hand the caller the returncode so the wrapper can judge it"
    )
    assert result.returncode == 3, (
        f"expected the real exit code 3 to survive, got {result.returncode}"
    )


def test_the_shipped_config_defaults_keep_ci_on_the_fixture_fallback_path():
    """SCANNERS_REQUIRED must default false, or five other tests go red.

    The plan's central ruling distinguishes ABSENT from BROKEN: a missing
    binary is a CI/dev affordance that keeps the existing fixture-fallback
    path, and SCANNERS_REQUIRED=true is what promotes it to a fault for the
    demo machine and production images. If the default flipped, CI's `test`
    job -- which installs no scanners on purpose -- would start producing
    three scanner-error findings instead of the fixture's two AWS-key
    findings, and the five assertions that read `len(blocking) == 2`
    (tests/test_pipeline_smoke.py, test_agent_fallbacks.py x3,
    test_gates_cli.py) would fail without anything naming the cause.

    Loaded from source into a THROWAWAY module with the environment cleared,
    so what is pinned is the shipped default rather than whatever happens to
    be exported on the machine running the suite. A fresh load is safe here:
    config.py imports only `os` and has no side effects. Reloading the real
    module object instead would rebind values under every `from ..common
    import config` in the package, which is why this does not do that.
    """
    spec = importlib.util.spec_from_file_location(
        "_config_defaults_probe", Path(config.__file__)
    )
    probe = importlib.util.module_from_spec(spec)

    with mock.patch.dict(os.environ, {}, clear=True):
        spec.loader.exec_module(probe)

    assert probe.SCANNERS_REQUIRED is False, (
        f"SCANNERS_REQUIRED must ship defaulting to False -- see this test's "
        f"docstring for the five assertions that depend on it. Got "
        f"{probe.SCANNERS_REQUIRED!r}"
    )
    assert isinstance(probe.SCANNER_TIMEOUT_SECONDS, int), (
        f"SCANNER_TIMEOUT_SECONDS is passed straight to subprocess timeout= "
        f"and must be an int, not the raw string os.environ hands back; got "
        f"{type(probe.SCANNER_TIMEOUT_SECONDS)}"
    )
    assert probe.SCANNER_TIMEOUT_SECONDS > 0, (
        f"a non-positive scanner timeout makes every scan time out instantly, "
        f"which under SCANNERS_REQUIRED blocks every change; got "
        f"{probe.SCANNER_TIMEOUT_SECONDS}"
    )


def test_scanners_required_reads_the_environment_like_the_other_boolean_knobs():
    """The knob has to be settable, and settable the way the rest of them are.

    OFFLINE and LLM_DISABLED both parse as `== "true"` case-insensitively, and
    the demo runbook will set SCANNERS_REQUIRED the same way. A knob that
    ignored its variable, or that treated the string "false" as truthy -- what
    plain `bool(os.environ.get(...))` does -- would be worse than no knob:
    the demo machine would believe it had fail-closed scanners and not have
    them.
    """
    spec = importlib.util.spec_from_file_location(
        "_config_env_probe", Path(config.__file__)
    )

    for raw, expected in (
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("false", False),
        ("", False),
        ("0", False),
    ):
        probe = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"SCANNERS_REQUIRED": raw}, clear=True):
            spec.loader.exec_module(probe)
        assert probe.SCANNERS_REQUIRED is expected, (
            f"SCANNERS_REQUIRED={raw!r} must parse to {expected}, got "
            f"{probe.SCANNERS_REQUIRED!r}"
        )
