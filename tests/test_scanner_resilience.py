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
from pydantic import ValidationError

from agentorg import fixtures_loader
from agentorg.common import config
from agentorg.security import trivy_tool
from agentorg.security._run import (
    classify_failure,
    error_finding,
    run_scanner,
    safe_run,
)
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


# ==========================================================================
# Task 2, round 2 -- what review found the first round had left on prose
# ==========================================================================


def test_safe_run_survives_malformed_argv_so_the_broad_clause_cannot_be_deleted():
    """The broad `except Exception` clause must be PINNED, not just argued for.

    WHY THIS TEST EXISTS, AND WHAT IT REPLACES
        `_run.py` argues at length that its final broad clause is load-bearing
        and correctly notes that ruff will not catch its removal -- a narrowed
        `except` with no logging is BLE001-clean, so lint blesses the more
        dangerous option. Review measured what that argument was worth: with
        the clause deleted the whole suite still reported `9 passed, 1 skipped`.
        Every other property in the module was pinned; this one rested on a
        comment.

    THE MEASURED CONSEQUENCE OF DELETING IT
        `safe_run([])` raises IndexError and `safe_run([None])` raises
        TypeError, where the shipped code returns None for both. Neither is an
        OSError and neither is a SubprocessError, so they slip past every
        specific clause. A raise escaping safe_run reaches the wrapper, and per
        the plan's ruling a wrapper is supposed to answer a failure with a
        BLOCKING error_finding -- it cannot do that for an exception it never
        sees. The failure mode is the pipeline crashing at the gate that exists
        to stop bad code, which on a projector is a stack trace where
        `status=blocked` should be.

    Malformed argv is not a hypothetical: a wrapper building `[binary, *flags]`
    from a config value that came back empty or None produces exactly these two
    shapes, and Task 3 builds argv in three new places.
    """
    for cmd, expected_exc in (([], "IndexError"), ([None], "TypeError")):
        result = safe_run(cmd, timeout=config.SCANNER_TIMEOUT_SECONDS)
        assert result is None, (
            f"safe_run({cmd!r}) must return None, not raise. Unpatched this "
            f"raises {expected_exc}, which is neither an OSError nor a "
            f"SubprocessError -- so if this test is red, the broad final "
            f"`except Exception` clause has been narrowed or removed and the "
            f"pipeline can now crash at the security gate. Got {result!r}"
        )


def test_run_scanner_tells_a_wrapper_absent_from_broken():
    """The absent-vs-fault call Task 3 must make, and the trap in making it.

    THE RULING THIS SERVES
        A binary that is merely ABSENT is a development and CI affordance that
        keeps the fixture-fallback path -- that is what lets CI's `test` job run
        with no scanners and still see the fixture's two AWS-key findings. A
        binary that is present and BROKEN is a fault that must block. Both come
        back from `safe_run` as None, so something has to distinguish them, and
        under SCANNERS_REQUIRED=true getting it wrong in the "absent" direction
        fails OPEN -- the exact inversion of what the knob is for.

    WHY NEITHER OBVIOUS DISCRIMINATOR WORKS -- both halves MEASURED
        `shutil.which` alone calls a file whose +x bit is gone "absent", and
        calls a directory "absent", because `which` requires the executable bit
        it is being asked about. Those are the two cases the plan names by name.
        The exception type alone calls a scanner that IS on PATH but has an
        unresolvable shebang "absent", because errno 2 there names the missing
        INTERPRETER rather than the scanner.

        So the answer is the conjunction of both signals, and this test pins
        each row of it. The +x case is built rather than mocked: chmod is the
        real mechanism, and a mock of `which` would pin the test to the
        implementation it is supposed to be checking.
    """
    scratch = Path(tempfile.mkdtemp(prefix="agentorg-faultprobe-"))

    noexec = scratch / "scanner-without-x-bit"
    noexec.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    noexec.chmod(0o644)

    bad_shebang = scratch / "scanner-with-unresolvable-shebang"
    bad_shebang.write_text(
        "#!/nonexistent/interpreter\necho hi\n", encoding="utf-8"
    )
    bad_shebang.chmod(0o755)

    cases = (
        (
            ["agentorg-no-such-scanner-binary-7b3e2a"],
            "absent",
            (
                "a binary that is simply not installed is the CI/dev "
                "affordance, and must stay on the fixture-fallback path"
            ),
        ),
        (
            [str(noexec)],
            "fault",
            (
                "a real file whose +x bit is gone is INSTALLED BUT BROKEN -- "
                "the plan names this case. shutil.which() reports it absent, "
                "which is why the exception type is needed as well"
            ),
        ),
        (
            [str(scratch)],
            "fault",
            "a directory is present and unrunnable, not absent",
        ),
        (
            [str(bad_shebang)],
            "fault",
            (
                "a scanner ON PATH whose interpreter is missing raises "
                "FileNotFoundError, so the exception type alone would call "
                "this absent -- this is the row shutil.which is needed for"
            ),
        ),
        (
            [],
            "fault",
            "malformed argv is a defect in the caller, not a missing tool",
        ),
    )

    for cmd, expected_kind, why in cases:
        result, kind = run_scanner(cmd, timeout=5)

        assert result is None, (
            f"{cmd!r} cannot produce a result; got {result!r}"
        )
        assert kind == expected_kind, (
            f"run_scanner({cmd!r}) classified this {kind!r}, expected "
            f"{expected_kind!r}. {why}. Getting a 'fault' wrong as 'absent' is "
            f"the dangerous direction: under SCANNERS_REQUIRED=true it takes "
            f"the fixture-fallback path and fails OPEN."
        )


def test_run_scanner_reports_no_failure_kind_when_the_command_ran():
    """A run that happened is never classified as a failure, at any exit code.

    The negative control for the test above. A classifier that answered "fault"
    unconditionally would satisfy four of its five rows, and would then make
    every wrapper in Task 3 emit a blocking error_finding on every run --
    including the clean fixture, which takes the demo's promote path down.

    The non-zero case is the one that matters most: gitleaks exits 1 when it
    finds secrets and semgrep exits 1 when it finds matches, so on the poisoned
    demo diff two of the three scanners exit non-zero on the happy path.
    """
    ran, kind = run_scanner(
        [sys.executable, "-c", "print('ok')"],
        timeout=config.SCANNER_TIMEOUT_SECONDS,
    )
    assert isinstance(ran, subprocess.CompletedProcess), f"got {type(ran)}"
    assert kind is None, (
        f"a successful run has no failure kind; got {kind!r}"
    )

    noisy, kind = run_scanner(
        [sys.executable, "-c", "import sys; sys.exit(1)"],
        timeout=config.SCANNER_TIMEOUT_SECONDS,
    )
    assert noisy is not None and noisy.returncode == 1, (
        f"exit 1 is gitleaks and semgrep REPORTING FINDINGS, not failing to "
        f"run; got {noisy!r}"
    )
    assert kind is None, (
        f"a non-zero exit must not be classified as a failure to run -- doing "
        f"so would swap the poisoned demo's two real criticals for a "
        f"scanner-error. Got kind={kind!r}"
    )


def test_a_timeout_classifies_as_a_fault_and_never_as_absent():
    """A hung scanner is installed by definition, so it can only be a fault.

    Separated from the table above because it is the one fault mode with no
    filesystem signal at all: the binary exists, `which` finds it, and it is
    perfectly executable -- it simply never returns. If a timeout were ever
    classified "absent", a scanner hanging on the demo machine would take the
    fixture-fallback path and the run would be promoted on fixture findings
    while the real scanner was still wedged.
    """
    result, kind = run_scanner(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1
    )
    assert result is None, f"a timeout produces no result; got {result!r}"
    assert kind == "fault", (
        f"a timeout is a present-but-broken scanner and must classify as "
        f"'fault'; got {kind!r}. 'absent' here would fail OPEN under "
        f"SCANNERS_REQUIRED=true."
    )


def test_error_finding_rejects_a_tool_name_the_finding_model_will_not_accept():
    """A mistyped tool name must fail at authoring time, not on the fault path.

    `Finding.tool` is a Literal of exactly three names. `error_finding` is
    called only when a scanner has already failed, so a typo there raises
    pydantic's ValidationError at the precise moment the pipeline is trying to
    report that failure -- converting "the scanner broke, here is a blocking
    finding" into "the pipeline crashed". Typing the parameter moves the error
    to where a type checker sees it; this test pins that the runtime rejection
    is still real, so nobody swaps the Literal for a bare `str` and assumes
    pydantic will keep covering it.
    """
    with pytest.raises(ValidationError):
        error_finding("gitleeks", "typo in the tool name")

    for tool in ("gitleaks", "semgrep", "trivy"):
        assert error_finding(tool, "ok").tool == tool, (
            f"{tool!r} is one of Finding.tool's three accepted names and must "
            f"still work"
        )


def test_classify_failure_without_a_hint_degrades_exactly_where_documented():
    """The hintless path, pinned row by row -- including where it is UNSAFE.

    WHY A TEST FOR A DEGRADED PATH, RATHER THAN JUST A WARNING NOT TO USE IT
        `classify_failure` is public and `kind_hint` is optional, so calling it
        bare compiles, returns a plausible answer, and is the natural thing to
        reach for from a wrapper that already has a `None` from `safe_run`. What
        makes that dangerous is WHICH rows it gets wrong.

    THE ROWS THAT LEAK ARE THE TWO THE PLAN'S RULING NAMES BY NAME
        Hintless there is no exception type to consult, so the answer is whatever
        `shutil.which` says -- and `which` requires the executable bit it is
        being asked about. So a scanner whose `+x` bit is gone reads "absent",
        and an argv0 that resolves to a directory reads "absent", when both are
        FAULTS. Classified absent, they take the fixture-fallback path and fail
        OPEN under SCANNERS_REQUIRED=true, which inverts the knob's purpose.

    AND THE ROW THAT DOES *NOT* LEAK IS THE TRAP
        The broken-shebang case comes out CORRECT hintless, because `which`
        resolves it. An earlier version of the module's own docstrings named that
        row as the hintless hazard -- exactly backwards. The danger in getting
        this backwards is not academic: a reader who believes the shebang row is
        the problem concludes "we don't invoke scanners through shebangs, so the
        hintless path is fine here", and ships the two rows that actually leak.
        Pinning all five rows is what stops the prose and the behaviour drifting
        apart again.

    This is also the only direct coverage `classify_failure` has -- every other
    test reaches it through `run_scanner`. Both entry points matter, because the
    one being documented as unsafe is the one still callable.
    """
    scratch = Path(tempfile.mkdtemp(prefix="agentorg-hintless-"))

    noexec = scratch / "scanner-without-x-bit"
    noexec.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    noexec.chmod(0o644)

    bad_shebang = scratch / "scanner-with-unresolvable-shebang"
    bad_shebang.write_text(
        "#!/nonexistent/interpreter\necho hi\n", encoding="utf-8"
    )
    bad_shebang.chmod(0o755)

    # (cmd, truth, what a hintless call actually answers, why)
    rows = (
        (
            ["agentorg-no-such-scanner-binary-9e4c1b"],
            "absent",
            "absent",
            (
                "nothing installed and nothing on PATH: the one row where the "
                "filesystem alone is sufficient"
            ),
        ),
        (
            [str(noexec)],
            "fault",
            "absent",
            (
                "DOCUMENTED LEAK: shutil.which needs the +x bit it is being "
                "asked about, so a real file without it reads as absent"
            ),
        ),
        (
            [str(scratch)],
            "fault",
            "absent",
            (
                "DOCUMENTED LEAK: a directory is not on PATH, so which reports "
                "nothing and the fault reads as absent"
            ),
        ),
        (
            [str(bad_shebang)],
            "fault",
            "fault",
            (
                "NOT a leak, and this is the trap: which resolves the file, so "
                "the hintless answer is already correct here. Reasoning from "
                "this row to 'the hintless path is safe' ships the two above"
            ),
        ),
        (
            [],
            "fault",
            "fault",
            "malformed argv has no binary to look up, and the default is fault",
        ),
    )

    for cmd, truth, hintless_answer, why in rows:
        got = classify_failure(cmd)

        assert got == hintless_answer, (
            f"classify_failure({cmd!r}) with no hint answered {got!r}; the "
            f"documented hintless behaviour is {hintless_answer!r}. {why}. If "
            f"this is red the degradation has MOVED, and both this test and "
            f"classify_failure's docstring table have to be re-measured "
            f"together -- a silent change here is what makes the prose lie."
        )

        # The half that says why the hint exists at all. Where these two differ
        # is exactly the fail-open exposure of calling this bare.
        if hintless_answer != truth:
            assert got == "absent" and truth == "fault", (
                f"a documented leak must be in the absent-for-a-fault "
                f"direction; {cmd!r} gave {got!r} against truth {truth!r}"
            )

        # ...and passing the hint must FIX every leaking row. This is the
        # assertion that makes the conjunction's value concrete rather than
        # asserted: same command, same classifier, correct answer once the
        # exception type is supplied.
        _, hinted = run_scanner(cmd, timeout=5)
        assert hinted == truth, (
            f"run_scanner({cmd!r}) supplies the hint and must therefore answer "
            f"{truth!r}, the truth; got {hinted!r}. If the hintless and hinted "
            f"answers agree on every row, the hint is buying nothing and the "
            f"conjunction has been broken."
        )
