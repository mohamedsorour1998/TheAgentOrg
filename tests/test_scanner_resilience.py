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
import itertools
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, get_args
from unittest import mock

import pytest
from pydantic import ValidationError

from agentorg import fixtures_loader
from agentorg.agents import security as security_agent
from agentorg.common import config
from agentorg.security import gitleaks_tool, run_all_scanners, semgrep_tool, trivy_tool
from agentorg.security._run import (
    ScannerTool,
    classify_failure,
    error_finding,
    run_scanner,
    safe_run,
    unrunnable_findings,
)
from agentorg.state import (
    SEVERITY_ORDER,
    DevResult,
    PlanResult,
    RunState,
    compute_security_verdict,
)

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
    """SCANNERS_REQUIRED must default false, or four pinned assertions go red.

    The plan's central ruling distinguishes ABSENT from BROKEN: a missing
    binary is a CI/dev affordance that keeps the existing fixture-fallback
    path, and SCANNERS_REQUIRED=true is what promotes it to a fault for the
    demo machine and production images. If the default flipped, CI's `test`
    job -- which installs no scanners on purpose -- would start producing
    three scanner-error findings instead of the fixture's two AWS-key
    findings.

    THE COUNT, MEASURED TWO WAYS, because they are different facts. EIGHT literal
    `assert len(...blocking) == 2` statements exist in tests/ (AST count; one is
    a CHAINED comparison at test_functional_contract.py:135 that a
    single-operator search misses). FOUR of them depend on this default --
    test_pipeline_smoke.py:20, test_agent_fallbacks.py:466,
    test_block_determinism.py:13, test_gates_cli.py:383 -- measured by flipping
    the default and seeing which go red. The other four never reach the scanner
    path: three patch run_all_scanners, one reads the fixture file. Earlier
    versions of this docstring, config.py and _run.py said "five" and "six";
    both were wrong and both were copied from the plan.

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
        f"docstring for the four assertions that depend on it. Got "
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


def test_run_scanner_tells_a_wrapper_absent_from_broken(tmp_path):
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
    # tmp_path, not tempfile.mkdtemp: pytest removes it. The first version of
    # this test used mkdtemp with no rmtree, which left one
    # agentorg-faultprobe-* tree in the system temp dir per `pytest` run --
    # measured at 91 of them (plus 91 agentorg-hintless-*) before this fix.
    scratch = tmp_path

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


def test_classify_failure_without_a_hint_degrades_exactly_where_documented(tmp_path):
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
    scratch = tmp_path  # pytest-managed; see the note in the test above

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


# ==========================================================================
# Task 3 -- the three wrappers, wrapped
#
# THE PROPERTY EVERY TEST BELOW IS AFTER, in one sentence: no scanner fault may
# reach `compute_security_verdict` as an empty findings list, because
# `compute_security_verdict([])` returns `("pass", [])`. Four separate fixes in
# this repository have been that same bug.
#
# HOW THE FAULTS ARE BUILT, AND WHY NOT WITH MOCKS
#     Each test below puts a REAL executable named `gitleaks` / `semgrep` /
#     `trivy` on PATH -- a shell script that exits non-zero, or writes garbage,
#     or writes nothing -- and lets the wrapper shell out to it for real. So what
#     is exercised is `subprocess`, PATH resolution, exit codes and report files:
#     the same machinery the demo machine runs.
#
#     Patching `_run.run_scanner` instead would be shorter and would pin much
#     less -- that asserts the wrapper handles a `None` it was HANDED, not that a
#     broken binary produces one. Two of the five fault modes in
#     `_run.classify_failure`'s measured table exist precisely because the real
#     mechanism disagrees with the obvious model of it.
#
# WHY PATH IS SET WITH monkeypatch.setenv
#     It is undone at teardown. Mutating os.environ directly would leak a fake
#     scanner directory into every test that runs after, and those directories
#     are deleted with tmp_path -- so a later test would resolve `gitleaks` to a
#     path that no longer exists and fail somewhere else entirely.
# ==========================================================================

# The wrapper under test, keyed by the binary name it shells out to. Iterated so
# each fault is pinned PER TOOL rather than once for whichever wrapper happens to
# be first: the three files are near-identical today, which is exactly the
# condition under which one of them silently drifts.
WRAPPERS = {
    "gitleaks": gitleaks_tool,
    "semgrep": semgrep_tool,
    "trivy": trivy_tool,
}

# The report filename each wrapper tells its scanner to write. The fake scanners
# below need it to write a report -- or to deliberately not write one -- at the
# path the wrapper will then look for.
REPORT_NAMES = {
    "gitleaks": "gitleaks-report.json",
    "semgrep": "semgrep-report.json",
    "trivy": "trivy-report.json",
}

# A diff with nothing interesting in it. These tests are about fault handling,
# not detection, and a fake scanner ignores the content anyway -- but the
# materialiser must have something to write, or a change to it would surface
# here as a confusing red.
_HARMLESS_DIFF = "--- /dev/null\n+++ b/app/noop.py\n@@ -0,0 +1 @@\n+VALUE = 1\n"


def _dev() -> DevResult:
    return DevResult(
        branch="feat/x",
        diff=_HARMLESS_DIFF,
        summary="s",
        files_changed=["app/noop.py"],
    )


def _fake_scanner(bin_dir: Path, tool: str, script: str, monkeypatch) -> None:
    """Put an executable named `tool` on PATH whose body is `script`.

    PATH is REPLACED, not prepended, so the real binary cannot answer if the fake
    is somehow not found. On a machine with the scanners installed -- CI's `scan`
    job, a demo laptop -- a prepend that failed would silently run the real
    scanner and the test would pass while pinning nothing. It also matters
    directly to
    test_a_scanner_whose_x_bit_is_gone_blocks_rather_than_looking_absent, whose
    whole subject is what PATH resolution does with an unrunnable file.

    SO `script` MAY USE ONLY SHELL BUILTINS AND ABSOLUTE PATHS. Nothing external
    resolves -- there is no `cat`, no `sleep`, no `printf(1)`. This is not a
    style note; it silently faked results here twice, and neither failure looked
    like one:

      * `cat > "$arg" <<EOF` -- the shell performs the REDIRECTION before it
        tries to execute `cat`, so the report file is created EMPTY and then
        `cat: not found`. The wrapper reads an empty file, raises
        JSONDecodeError, and returns a scanner-error -- so three parametrized
        batches asserting "a malformed report blocks" and "a wrong-shaped report
        blocks" passed while every one of them was actually testing the
        empty-file parse error. Caught by
        test_a_working_scanner_is_not_reported_as_a_fault, which is the only test
        here that expects NO finding.
      * `sleep 30` -- not found, so the script exits 127 immediately. The wrapper
        sees an unexpected exit code and returns a scanner-error, so the timeout
        test passed without a timeout ever occurring.

    Both now assert on WHICH fault they got, not merely that they got one.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / tool
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


def _write_report_script(tool: str, body: str) -> str:
    """A /bin/sh scanner that writes `body` to whichever argv is its report path.

    Walks argv for the report filename rather than hardcoding a position, because
    that is what the wrapper actually passed: a wrapper that reordered its flags
    is still honoured, and one that stopped asking for a report at all makes these
    tests fail loudly instead of silently.

    `echo` because it is a shell BUILTIN -- see _fake_scanner on why nothing
    external resolves here, and on the empty report files a `cat` heredoc left
    behind. Every body below is one line and free of single quotes, which is what
    lets the single-quoted form be exact.
    """
    return (
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        f"    *{REPORT_NAMES[tool]}) echo '{body}' > \"$arg\" ;;\n"
        "  esac\n"
        "done\n"
        "exit 0"
    )


def _only_error_findings(findings: list, tool: str) -> None:
    """Assert `findings` is exactly one BLOCKING scanner-error for `tool`."""
    assert findings, (
        f"{tool}: a fault produced an EMPTY findings list. "
        f"compute_security_verdict([]) returns ('pass', []), so this is the "
        f"silent-pass bug -- the gate reports clean precisely because it did "
        f"not run. It must return [error_finding(...)] instead."
    )
    assert len(findings) == 1, (
        f"{tool}: expected exactly one scanner-error finding, got "
        f"{_summarize(findings)}"
    )
    finding = findings[0]
    assert finding.tool == tool, (
        f"the error finding must name the tool that failed; got {finding.tool!r}"
    )
    assert finding.rule == f"{tool}-scanner-error", (
        f"{tool}: expected rule {tool}-scanner-error, got {finding.rule!r}"
    )

    verdict, blocking = compute_security_verdict(
        findings, threshold=config.SECURITY_BLOCK_THRESHOLD
    )
    assert verdict == "block", (
        f"{tool}: a fault must BLOCK at threshold "
        f"{config.SECURITY_BLOCK_THRESHOLD!r}; got {verdict!r} from "
        f"{_summarize(findings)}. This is what makes the fault fail CLOSED -- "
        f"the finding existing is not enough if its severity does not reach the "
        f"threshold."
    )
    assert blocking == findings, (
        f"{tool}: the error finding must be in the blocking list, since that is "
        f"what the PR comment and the projector name as the reason"
    )


@pytest.mark.parametrize("tool", sorted(WRAPPERS))
def test_a_scanner_that_exits_nonzero_blocks_instead_of_raising(
    tool, monkeypatch, tmp_path
):
    """A present binary failing with an unexpected exit code must BLOCK.

    Exit codes 0 and 1 are all three scanners SPEAKING -- gitleaks exits 1 when
    it finds secrets, semgrep exits 1 when it finds matches -- so this uses 2,
    which every wrapper already treated as a failure.

    WHAT CHANGED, AND WHY THE OLD BEHAVIOUR WAS NOT GOOD ENOUGH
        Before Task 3 this raised RuntimeError, agents/security.py caught it and
        returned the FIXTURE verdict. That is right when no binary is installed
        and wrong when one is: the fixture describes the DEMO diff, so a real
        change scanned by a broken scanner would be judged on findings from a
        different diff entirely -- blocked when the fixture blocks, promoted when
        it passes, either way not scanned. Now the fault is a finding about the
        fault itself.
    """
    _fake_scanner(
        tmp_path / "bin",
        tool,
        'echo "boom: the database is locked" >&2\nexit 2',
        monkeypatch,
    )

    findings = WRAPPERS[tool].scan(_dev())
    _only_error_findings(findings, tool)
    assert "2" in findings[0].description, (
        f"the exit code must survive into the description -- it is what tells an "
        f"operator which failure this was. Got {findings[0].description!r}"
    )


@pytest.mark.parametrize("tool", sorted(WRAPPERS))
def test_a_scanner_that_writes_no_report_blocks_instead_of_raising(
    tool, monkeypatch, tmp_path
):
    """Exit 0 and no report file is a fault: the change went UNSCANNED.

    This is the fault mode closest to the silent-pass bug, because it looks like
    success from outside. The binary exists, it ran, it exited 0 -- and there is
    nothing to parse. A wrapper answering `[]` here would report a clean scan for
    a change no scanner ever read.

    NOT THE SAME CASE AS AN ABSENT BINARY, and the plan's brief draws that line
    by name: a missing report is a fault when the binary RAN, and is NOT a fault
    when it never ran. The absent path is pinned by
    test_an_absent_binary_still_raises_so_ci_keeps_the_fixture_fallback.
    """
    _fake_scanner(tmp_path / "bin", tool, "exit 0", monkeypatch)

    findings = WRAPPERS[tool].scan(_dev())
    _only_error_findings(findings, tool)


@pytest.mark.parametrize("tool", sorted(WRAPPERS))
def test_a_scanner_that_writes_malformed_json_blocks_instead_of_raising(
    tool, monkeypatch, tmp_path
):
    """A report that is not JSON at all is a fault, per tool."""
    _fake_scanner(
        tmp_path / "bin",
        tool,
        _write_report_script(tool, "this is not json at all"),
        monkeypatch,
    )

    findings = WRAPPERS[tool].scan(_dev())
    _only_error_findings(findings, tool)
    assert "not valid JSON" in findings[0].description, (
        f"the description must say the report was unparseable, and say it in "
        f"those words -- distinguishing this from the missing-report and "
        f"wrong-shape faults, which are different bugs to chase. Got "
        f"{findings[0].description!r}"
    )


@pytest.mark.parametrize(
    "tool, wrong_shape",
    [
        # gitleaks' report is a LIST of leak objects; semgrep's and trivy's are
        # OBJECTS. Each case below is valid JSON of a shape the wrapper's parse
        # cannot survive: `for leak in "str"` iterates CHARACTERS and `data.get`
        # raises AttributeError on a list -- an exception on the fault path,
        # where a blocking finding belongs.
        ("gitleaks", '{"Results": []}'),
        ("gitleaks", '"a bare string"'),
        ("gitleaks", '["not an object"]'),
        ("semgrep", "[1, 2, 3]"),
        ("semgrep", '{"results": "not a list"}'),
        ("trivy", "[1, 2, 3]"),
        ("trivy", '{"Results": "not a list"}'),
    ],
)
def test_a_report_of_the_wrong_json_shape_blocks_rather_than_crashing(
    tool, wrong_shape, monkeypatch, tmp_path
):
    """Parseable JSON of the wrong SHAPE must block, not raise mid-parse.

    Distinct from the malformed-JSON test above: `json.loads` SUCCEEDS here, so
    the try/except around it never fires and the wrapper walks into its own parse
    loop holding a value of the wrong type. Measured against the pre-Task-3 code:
    gitleaks' loop raised AttributeError on a bare string, and semgrep's and
    trivy's raised AttributeError on a list. Neither is a JSONDecodeError, so
    both escaped as an exception rather than becoming a blocking finding.
    """
    _fake_scanner(
        tmp_path / "bin",
        tool,
        _write_report_script(tool, wrong_shape),
        monkeypatch,
    )

    findings = WRAPPERS[tool].scan(_dev())
    _only_error_findings(findings, tool)

    # The report here PARSES. So a reason naming a parse error or a missing file
    # means the fake never wrote what this test thinks it wrote, and the shape
    # guard was never reached -- which is exactly how an earlier version of this
    # harness passed while testing nothing. See _fake_scanner.
    assert "not valid JSON" not in findings[0].description, (
        f"this report is VALID JSON of the wrong shape, so a parse error means "
        f"the fake scanner wrote an empty file and the shape guard was never "
        f"exercised. Got {findings[0].description!r}"
    )
    assert "no report" not in findings[0].description, (
        f"the fake scanner did not write its report where the wrapper asked, so "
        f"this test is not pinning the shape guard. Got "
        f"{findings[0].description!r}"
    )


@pytest.mark.parametrize("tool", sorted(WRAPPERS))
def test_a_scanner_that_hangs_blocks_instead_of_waiting_forever(
    tool, monkeypatch, tmp_path
):
    """A hung scanner is a fault, and the wrapper must not wait for it.

    The timeout is patched to 1s against a 30s sleep -- a margin no loaded
    machine can close -- so the test costs about a second. `config` is patched
    through the MODULE attribute because that is how each wrapper reads it; a
    wrapper written `from ..common.config import SCANNER_TIMEOUT_SECONDS` would
    bind the value at import and silently ignore this, the same trap
    tests/conftest.py documents for LLM_DISABLED.

    On a projector this is the fault that matters most: with no timeout the
    pipeline waits at the gate forever, produces no verdict at all, and looks
    like a freeze rather than a block.
    """
    monkeypatch.setattr(config, "SCANNER_TIMEOUT_SECONDS", 1)
    # /bin/sleep by ABSOLUTE path: PATH is replaced with the fake's directory, so
    # a bare `sleep` is not found and the script exits 127 instantly -- which is
    # also a fault, so this test passed without ever timing out. See
    # _fake_scanner.
    _fake_scanner(tmp_path / "bin", tool, "/bin/sleep 30", monkeypatch)

    findings = WRAPPERS[tool].scan(_dev())
    _only_error_findings(findings, tool)

    # WHICH fault, not merely that there was one. An exit code in the reason
    # means the command RAN and returned -- so it did not hang, and this test
    # would be pinning something else entirely.
    assert "exit code" not in findings[0].description, (
        f"a timeout must not be reported as an exit code -- if it is, the fake "
        f"scanner returned instead of hanging and this test proves nothing. Got "
        f"{findings[0].description!r}"
    )
    assert "timeout" in findings[0].description, (
        f"the reason must name the timeout, since that is what an operator has "
        f"to act on; got {findings[0].description!r}"
    )


@pytest.mark.parametrize("tool", sorted(WRAPPERS))
def test_a_scanner_whose_x_bit_is_gone_blocks_rather_than_looking_absent(
    tool, monkeypatch, tmp_path
):
    """Installed-but-unrunnable is a FAULT, and this is the row that fails open.

    `shutil.which` reports this file as absent, because `which` requires the
    executable bit it is being asked about -- so a wrapper discriminating on the
    filesystem alone would take the fixture-fallback path for a scanner that IS
    installed and IS broken. Under SCANNERS_REQUIRED=true that inverts the knob's
    entire purpose. The plan's ruling names this case ("a lost +x bit, a noexec
    mount") and `_run.classify_failure`'s measured table lists it as one of the
    two documented leaks of the hintless path.

    Built with chmod rather than by mocking `which`, because the real mechanism
    is the thing the two candidate discriminators disagree about.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    path = bin_dir / tool
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o644)
    monkeypatch.setenv("PATH", str(bin_dir))

    findings = WRAPPERS[tool].scan(_dev())
    _only_error_findings(findings, tool)


@pytest.mark.parametrize("tool", sorted(WRAPPERS))
def test_an_absent_binary_still_raises_so_ci_keeps_the_fixture_fallback(
    tool, monkeypatch, tmp_path
):
    """The RULING's other half: absent is an affordance, not a fault.

    A binary that is simply not installed must keep the shipped behaviour --
    raise, so agents/security.py falls back to the fixture verdict. That is what
    lets CI's `test` job run with no scanners and still see the poisoned diff's
    two AWS-key findings. Four assertions across the suite depend on that, of
    eight that read `len(blocking) == 2` -- both counts measured; see
    config.SCANNERS_REQUIRED.

    THE RAISE IS THE ASSERTION, and it is not a stylistic choice: the
    alternative -- returning `[]` -- is the silent-pass shape. A wrapper that
    returned `[]` for an absent binary would make the fan-out report a clean scan
    on a machine with no scanners at all. Pinned again from the fan-out's side by
    test_absent_never_yields_an_empty_findings_list_from_a_wrapper.

    PATH is emptied rather than left alone, so this test means the same thing in
    both scanner modes. Without that it would pass vacuously in CI's `test` job
    (nothing installed) and fail on the `scan` job and any demo laptop.
    """
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    with pytest.raises(FileNotFoundError) as excinfo:
        WRAPPERS[tool].scan(_dev())

    assert tool in str(excinfo.value), (
        f"the raise must name the scanner that is missing, so the WARNING line "
        f"in agents/security.py says which one; got {str(excinfo.value)!r}"
    )
    assert "SCANNERS_REQUIRED" in str(excinfo.value), (
        "the message must name the knob that turns this into a blocking "
        "finding, since that is the whole remedy an operator has"
    )


@pytest.mark.parametrize("tool", sorted(WRAPPERS))
def test_scanners_required_promotes_an_absent_binary_to_a_blocking_fault(
    tool, monkeypatch, tmp_path
):
    """With the knob set, a missing scanner blocks loudly instead of borrowing a fixture.

    This is the demo-machine and production configuration. Without the knob the
    fan-out quietly reports the fixture's verdict for a change no scanner read;
    with it, "trivy is not installed" becomes a blocking finding that says so.

    Patched with monkeypatch.setattr on the config MODULE, which is both how the
    suite flips OFFLINE and LLM_DISABLED and the only thing that works here: the
    module-level `os.environ.get` in config.py has already run by the time any
    test starts, so `setenv("SCANNERS_REQUIRED", "true")` would do nothing.
    """
    monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    findings = WRAPPERS[tool].scan(_dev())
    _only_error_findings(findings, tool)
    assert "SCANNERS_REQUIRED" in findings[0].description, (
        f"the description must say the knob is what made this a fault, so the "
        f"finding is not mistaken for a broken install; got "
        f"{findings[0].description!r}"
    )


def test_a_working_scanner_is_not_reported_as_a_fault(monkeypatch, tmp_path):
    """The negative control, and it is load-bearing for the demo's promote path.

    Without it every test above passes on a wrapper that returns a scanner-error
    unconditionally -- which fails closed, blocks the CLEAN fixture too, and
    takes the promote half of the demo down.

    Each fake writes an EMPTY but well-formed report in its own tool's shape, so
    the wrapper parses it and finds nothing. Zero findings is the correct answer
    here and is what a clean scan looks like; the point is that it is reached by
    parsing rather than by failing.
    """
    empty_reports = {
        "gitleaks": "[]",
        "semgrep": '{"results": []}',
        "trivy": '{"Results": []}',
    }

    for tool, module in sorted(WRAPPERS.items()):
        _fake_scanner(
            tmp_path / f"bin-{tool}",
            tool,
            _write_report_script(tool, empty_reports[tool]),
            monkeypatch,
        )

        findings = module.scan(_dev())
        assert findings == [], (
            f"{tool}: a scanner that ran and reported nothing must yield NO "
            f"findings, not a scanner-error. Got {_summarize(findings)} -- a "
            f"wrapper that faults on success blocks the clean fixture and takes "
            f"the demo's promote path down."
        )


def test_absent_never_yields_an_empty_findings_list_from_a_wrapper(
    monkeypatch, tmp_path
):
    """The fan-out's own view of the ruling, both ways round the knob.

    Pinned from `run_all_scanners` rather than from a single wrapper, because
    that is the frozen seam agents/security.py calls and the level at which "the
    scanners found nothing" is indistinguishable from "the scanners did not run".
    The two outcomes below are the only two allowed with no binaries on PATH, and
    `[]` is neither of them:

      * knob off -> RAISES. security.run catches it and uses the fixture
        verdict, which still blocks a diff carrying an AWS key.
      * knob on  -> three blocking findings, one per tool, and a `block` verdict.

    THE LAST ASSERTION IS THE ONE THAT MATTERS: with the knob on, all THREE tools
    must be named. A fan-out that stopped at the first fault would report one,
    and "gitleaks is missing" on a machine where all three are missing
    understates the problem -- it would hide the second and third faults behind
    the first fix.
    """
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    with pytest.raises(FileNotFoundError):
        run_all_scanners(_dev())

    monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)
    findings = run_all_scanners(_dev())

    assert findings != [], (
        "run_all_scanners returned [] with SCANNERS_REQUIRED set and no "
        "binaries installed. compute_security_verdict([]) returns ('pass', []), "
        "so this is the silent pass: a change promoted by a gate that never ran."
    )
    verdict, blocking = compute_security_verdict(
        findings, threshold=config.SECURITY_BLOCK_THRESHOLD
    )
    assert verdict == "block", (
        f"three unrunnable scanners must block; got {verdict!r} from "
        f"{_summarize(findings)}"
    )
    assert {f.tool for f in blocking} == {"gitleaks", "semgrep", "trivy"}, (
        f"every failed scanner must be named, not just the first: a fan-out that "
        f"short-circuits hides the second and third faults behind the first fix. "
        f"Got {_summarize(blocking)}"
    )


def test_the_security_agent_blocks_on_a_faulting_scanner_without_the_fixture(
    monkeypatch, tmp_path
):
    """End to end through the agent: a fault blocks on ITS OWN finding.

    Everything above tests wrappers. This tests the consequence, and it is the
    claim the demo makes: `security.run` on a change scanned by broken scanners
    reports `block` with scanner-errors in `blocking` -- NOT the fixture's two
    AWS-key findings, which describe a different diff.

    THE DIFF HERE IS DELIBERATELY CLEAN, and that is what makes the assertion
    sharp. The fixture fallback for a clean diff is `pass`, so a fault that
    reached it would be indistinguishable from a successful scan. Measured
    against the pre-Task-3 code, this exact scenario: verdict `pass`, blocking
    `[]` -- a change promoted by three scanners that all failed.
    """
    for tool in WRAPPERS:
        _fake_scanner(
            tmp_path / "bin",
            tool,
            'echo "internal error" >&2\nexit 2',
            monkeypatch,
        )

    state = RunState(
        ticket_id="CLEAN-1",
        ticket_text="add a harmless constant",
        plan=PlanResult(tasks=["t"], acceptance_criteria=["a"], target_files=["x"]),
        dev=_dev(),
    )

    result = security_agent.run(state)

    assert result.verdict == "block", (
        f"a clean diff scanned by three broken scanners must BLOCK -- the gate "
        f"did not run, so it cannot report clean. Got {result.verdict!r} with "
        f"{_summarize(result.findings)}. A 'pass' here is the silent-pass bug "
        f"exactly: the fixture fallback for a clean diff is 'pass'."
    )
    assert {f.rule for f in result.blocking} == {
        "gitleaks-scanner-error",
        "semgrep-scanner-error",
        "trivy-scanner-error",
    }, (
        f"the block must be on the scanner faults themselves, not on fixture "
        f"findings about the demo diff. Got {_summarize(result.blocking)}"
    )
    assert result.explanation, "a blocked run must always explain itself"


def test_semgrep_treats_a_missing_rules_file_as_a_fault_not_an_absent_scanner(
    monkeypatch, tmp_path
):
    """semgrep's own rules file is packaged with it, so its absence is a FAULT.

    The distinction is easy to get backwards, and getting it backwards fails
    OPEN. `semgrep_rules.yml` ships inside `agentorg/security/`, so if it is gone
    the install or the build is broken -- semgrep itself may be perfectly present
    and healthy. Before Task 3 this raised FileNotFoundError, which is the SAME
    exception the no-binary case raises, so agents/security.py could not tell
    them apart and answered both with the fixture verdict. On a machine that HAS
    semgrep, that means the gate reports the demo diff's verdict for whatever
    change is actually being scanned.

    Only this one wrapper has a config file to lose, which is why it gets a test
    the other two do not.

    A working fake semgrep is put on PATH -- one that writes an empty report and
    exits 0 -- so this cannot pass by accident on a machine with no binaries: the
    fault has to come from the rules check and nothing else.
    """
    _fake_scanner(
        tmp_path / "bin",
        "semgrep",
        _write_report_script("semgrep", '{"results": []}'),
        monkeypatch,
    )
    # Patched on the Path class the wrapper imported, narrowed to the rules file
    # by name so the report's own .exists() check still answers truthfully --
    # otherwise this would pin the missing-report fault instead.
    real_exists = Path.exists
    monkeypatch.setattr(
        semgrep_tool.Path,
        "exists",
        lambda self, *a, **kw: (
            False if self.name == "semgrep_rules.yml" else real_exists(self, *a, **kw)
        ),
    )

    findings = semgrep_tool.scan(_dev())
    _only_error_findings(findings, "semgrep")
    assert "rules file" in findings[0].description, (
        f"the description must name the rules file, because a broken package is "
        f"a different fix from a broken binary; got {findings[0].description!r}"
    )


def test_unrunnable_findings_has_no_third_outcome(monkeypatch):
    """The shared helper: never `[]`, on any input. Pinned directly.

    All three wrappers delegate the ruling here, so this is the one place the
    absent-vs-fault fork exists -- and the one place a regression changes all
    three wrappers at once. `[]` is unreachable by construction, because the
    absent branch raises; this asserts the construction rather than the prose
    claiming it.

    `kind=None` is included because that is what `run_scanner` returns when the
    command RAN. A wrapper reaching here with it has confused a bad exit code for
    a failure to launch, and "fault" is the safe reading -- the other direction
    fails open.
    """
    for kind in ("fault", None):
        findings = unrunnable_findings("trivy", kind, "reason")
        assert len(findings) == 1 and findings[0].severity == "high", (
            f"kind={kind!r} must yield exactly one high finding; got "
            f"{_summarize(findings)}"
        )

    with pytest.raises(FileNotFoundError):
        unrunnable_findings("trivy", "absent", "reason")

    monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)
    promoted = unrunnable_findings("trivy", "absent", "reason")
    assert len(promoted) == 1 and promoted[0].severity == "high", (
        f"SCANNERS_REQUIRED must promote absent to a blocking finding; got "
        f"{_summarize(promoted)}"
    )


def test_the_wrappers_shell_out_through_the_subprocess_module_attribute():
    """scripts/scan_gate.py's binary spy must keep working. Pinned here, in pytest.

    The gate proves all three scanners actually executed by REPLACING
    `subprocess.run` on the module object and recording every argv[0]. That works
    only while the call goes through the attribute at call time. A refactor to
    `from subprocess import run` -- in `_run.py` now, since that is where the
    single call site lives -- would bind the function at import and make the spy
    blind: the gate would report `binaries executed: []`.

    Asserted here because the gate runs only in CI's `scan` job with all three
    binaries installed, so a laptop and CI's `test` job would otherwise learn
    about this at the worst possible moment. This test needs no binaries: it
    patches the attribute and checks the patch is SEEN.
    """
    seen: list[str] = []
    real_run = subprocess.run

    def spy(args, *rest, **kwargs):
        if isinstance(args, list | tuple) and args:
            seen.append(str(args[0]))
        return real_run(args, *rest, **kwargs)

    subprocess.run = spy
    try:
        # Through safe_run, the one place any wrapper shells out from.
        safe_run([sys.executable, "-c", "pass"], timeout=5)
    finally:
        subprocess.run = real_run

    assert seen == [sys.executable], (
        f"replacing subprocess.run on the module object must be visible to "
        f"_run.safe_run, because that is how scripts/scan_gate.py proves all "
        f"three binaries executed. Saw {seen!r}. If this is empty, something now "
        f"holds a direct reference to the function (`from subprocess import "
        f"run`) and the gate's spy is blind."
    )


# ==========================================================================
# Review round 2 -- what the top-level shape guards could not see
# ==========================================================================


@pytest.mark.parametrize(
    "tool, report, what_it_used_to_do",
    [
        # Every one of these PASSES the top-level guard the first review round
        # added, and then crashed on a dereference inside the parse loop.
        # MEASURED against that code -- these are the exact exceptions and the
        # exact lines:
        (
            "gitleaks",
            '[{"RuleID": "r", "File": "f", "StartLine": "not-an-int"}]',
            "ValueError from int() at gitleaks_tool.py:184",
        ),
        (
            "gitleaks",
            '[{"RuleID": "r", "File": {"nested": 1}, "StartLine": 1}]',
            "TypeError from os.path.relpath at gitleaks_tool.py:48",
        ),
        (
            "gitleaks",
            '[{"RuleID": ["a", "list"], "File": "f", "StartLine": 1}]',
            "pydantic ValidationError at gitleaks_tool.py:176",
        ),
        (
            "gitleaks",
            '[{"RuleID": "r", "File": "f", "StartLine": 1, "Description": {"o": 1}}]',
            "pydantic ValidationError at gitleaks_tool.py:176",
        ),
        # These two exist because MUTATION found them, not because reading the
        # code suggested them. `report_int`'s final fall-through raise -- the one
        # for a type that is neither int, bool, nor str -- was reached by no test:
        # replacing it with `return default` left the whole suite green at 175
        # passed. Every other case routes through an earlier branch. An unpinned
        # fall-through in an integer reader means a report carrying an object
        # where a line number belongs would silently become line 0.
        (
            "gitleaks",
            '[{"RuleID": "r", "File": "f", "StartLine": {"nested": 1}}]',
            "TypeError from int() on a dict",
        ),
        (
            "semgrep",
            (
                '{"results": [{"check_id": "c", "path": "p", "extra": {}, '
                '"start": {"line": [1, 2]}}]}'
            ),
            "TypeError from int() on a list",
        ),
        (
            "semgrep",
            (
                '{"results": [{"check_id": "c", "path": "p", "extra": "nope", '
                '"start": {"line": 1}}]}'
            ),
            "AttributeError from extra.get at semgrep_tool.py:178",
        ),
        (
            "semgrep",
            (
                '{"results": [{"check_id": "c", "path": "p", "extra": {}, '
                '"start": "nope"}]}'
            ),
            "AttributeError from start.get at semgrep_tool.py:189",
        ),
        (
            "semgrep",
            (
                '{"results": [{"check_id": "c", "path": "p", "extra": {}, '
                '"start": {"line": "x"}}]}'
            ),
            "ValueError from int() at semgrep_tool.py:188",
        ),
        (
            "trivy",
            '{"Results": [{"Target": "t", "Vulnerabilities": ["not-an-object"]}]}',
            "AttributeError from vulnerability.get at trivy_tool.py:160",
        ),
        (
            "trivy",
            (
                '{"Results": [{"Target": "t", "Vulnerabilities": '
                '[{"Severity": {"o": 1}, "VulnerabilityID": "v"}]}]}'
            ),
            "AttributeError from .upper() in _map_severity at trivy_tool.py:56",
        ),
    ],
)
def test_wrong_typed_inner_fields_block_rather_than_crashing(
    tool, report, what_it_used_to_do, monkeypatch, tmp_path
):
    """The fail-open the TOP-LEVEL shape guards do not reach. One level deeper.

    WHY THIS EXISTS AS A SEPARATE TEST FROM THE WRONG-SHAPE ONE ABOVE
        `test_a_report_of_the_wrong_json_shape_blocks_rather_than_crashing`
        checks that a report which is a list-where-an-object-belongs (or the
        reverse) is rejected. Every report below SATISFIES that guard: gitleaks'
        is a list of objects, semgrep's and trivy's are objects whose results key
        is a list of objects. The wrong type is on a FIELD the loop dereferences,
        so the guard passes and the crash happens later, inside a Finding
        construction. MEASURED on the code the first review round approved: 9 of
        9 of these raised, at the locations named in each parameter.

    THE END-TO-END CONSEQUENCE IS A PROMOTED CHANGE, which is why this is not a
        tidiness test. The exception escapes the wrapper, agents/security.py
        catches it, and falls back to the fixture verdict -- and the fixture
        verdict for a CLEAN diff is "pass". Measured with all three scanners
        installed and all three emitting wrong-typed inner fields on a clean
        diff: `verdict=pass, blocking=0`. A change promoted although no scanner
        output was ever read. Pinned end to end by
        test_the_security_agent_blocks_when_every_report_has_wrong_typed_fields.

    WHY IT NEEDS A MISBEHAVING SCANNER, and why that is still in scope. Real
        gitleaks 8.21.2 and semgrep 1.172.0 reports are well-typed at every level
        (measured: StartLine int, File str, extra dict, start.line int, no result
        missing `extra`). So none of these arise from a healthy binary -- but a
        misbehaving scanner is the entire premise of this lane, and "the scanner
        is broken in a way we did not anticipate" is what fail-closed means.
    """
    _fake_scanner(
        tmp_path / "bin", tool, _write_report_script(tool, report), monkeypatch
    )

    findings = WRAPPERS[tool].scan(_dev())
    _only_error_findings(findings, tool)

    # WHICH fault. The report here is valid JSON AND passes the top-level shape
    # guard, so a reason naming either of those means the fake wrote something
    # other than intended and this case is pinning a guard that already existed.
    description = findings[0].description
    assert "not valid JSON" not in description, (
        f"this report IS valid JSON, so a parse error means the fake scanner "
        f"wrote an empty file and the field readers were never reached -- the "
        f"exact way the first version of this harness faked results. Got "
        f"{description!r}"
    )
    assert "not the expected JSON" not in description, (
        f"this report passes the top-level shape guard, so a top-level "
        f"complaint means this case duplicates the wrong-shape test instead of "
        f"covering the field readers. Got {description!r}"
    )
    assert "unusable report" in description, (
        f"the reason must identify this as a field-level rejection, since that "
        f"is a different fix from a mangled top-level report ({what_it_used_to_do} "
        f"is what it used to do). Got {description!r}"
    )


def test_the_security_agent_blocks_when_every_report_has_wrong_typed_fields(
    monkeypatch, tmp_path
):
    """The fail-open from Finding 1, end to end, on the shape that hides it.

    The diff is CLEAN and all three scanners are installed. That combination is
    what made the old behaviour invisible: the wrappers crashed, the fixture
    fallback ran, and the fixture verdict for a clean diff is `pass` -- so the
    pipeline reported a successful scan of a change no scanner output was read
    for. MEASURED on the code the first review round approved: `verdict=pass,
    blocking=0`.

    This is the same assertion shape as
    test_the_security_agent_blocks_on_a_faulting_scanner_without_the_fixture, but
    driven by a fault that test cannot produce: there the scanners exit non-zero,
    here they exit 0 and write a report that looks healthy until a field is read.
    """
    reports = {
        "gitleaks": '[{"RuleID": "r", "File": "f", "StartLine": "not-an-int"}]',
        "semgrep": '{"results": [{"check_id": "c", "path": "p", "extra": "nope"}]}',
        "trivy": '{"Results": [{"Target": "t", "Vulnerabilities": ["bare-string"]}]}',
    }
    for tool, report in reports.items():
        _fake_scanner(
            tmp_path / "bin", tool, _write_report_script(tool, report), monkeypatch
        )

    state = RunState(
        ticket_id="CLEAN-2",
        ticket_text="add a harmless constant",
        plan=PlanResult(tasks=["t"], acceptance_criteria=["a"], target_files=["x"]),
        dev=_dev(),
    )

    result = security_agent.run(state)

    assert result.verdict == "block", (
        f"three scanners emitting unusable reports on a CLEAN diff must BLOCK. "
        f"Got {result.verdict!r} with {_summarize(result.findings)}. A 'pass' "
        f"here is the fail-open: the crash reaches the fixture fallback, and the "
        f"fixture verdict for a clean diff is 'pass', so a change is promoted "
        f"although no scanner output was read."
    )
    assert {f.rule for f in result.blocking} == {
        "gitleaks-scanner-error",
        "semgrep-scanner-error",
        "trivy-scanner-error",
    }, f"every unusable report must be named. Got {_summarize(result.blocking)}"


def test_a_well_typed_report_still_parses_into_real_findings(monkeypatch, tmp_path):
    """The negative control for the field readers, per tool.

    Without it, readers that rejected EVERYTHING would satisfy every case above
    -- and that fails closed on the clean fixture too, which takes the demo's
    promote path down. The reports below are the well-typed shapes real gitleaks,
    semgrep and trivy emit, and each must map to a Finding with the field values
    intact rather than to a scanner-error.

    It also pins the two type coercions the readers deliberately KEEP: a
    `StartLine` arriving as the string "7" is accepted as 7, because gitleaks has
    shipped both across versions and the wrapper did `int(...)` before.
    """
    cases = {
        "gitleaks": (
            (
                '[{"RuleID": "aws-access-key-id", "File": "app/auth.py", '
                '"StartLine": "7", "Description": "AWS key found"}]'
            ),
            "aws-access-key-id",
            7,
        ),
        "semgrep": (
            (
                '{"results": [{"check_id": "rule.id", "path": "app/auth.py", '
                '"extra": {"severity": "ERROR", "message": "bad"}, '
                '"start": {"line": 12}}]}'
            ),
            "rule.id",
            12,
        ),
        "trivy": (
            (
                '{"Results": [{"Target": "requirements.txt", "Vulnerabilities": '
                '[{"Severity": "HIGH", "VulnerabilityID": "CVE-2020-1", '
                '"Title": "vuln"}]}]}'
            ),
            "CVE-2020-1",
            0,
        ),
    }

    for tool, (report, expected_rule, expected_line) in cases.items():
        _fake_scanner(
            tmp_path / f"bin-{tool}",
            tool,
            _write_report_script(tool, report),
            monkeypatch,
        )

        findings = WRAPPERS[tool].scan(_dev())

        assert len(findings) == 1, (
            f"{tool}: a well-typed report must map to exactly one finding, not "
            f"be rejected. Got {_summarize(findings)} -- readers that reject "
            f"everything fail closed on the CLEAN fixture too."
        )
        assert findings[0].rule == expected_rule, (
            f"{tool}: expected rule {expected_rule!r}, got {findings[0].rule!r}"
        )
        assert findings[0].line == expected_line, (
            f"{tool}: expected line {expected_line}, got {findings[0].line}. For "
            f"gitleaks this also pins that a numeric STRING is still coerced, "
            f"which the wrapper did before these readers existed."
        )


def test_a_boolean_is_not_quietly_read_as_a_line_number(monkeypatch, tmp_path):
    """`"StartLine": true` must be rejected, not silently become line 1.

    `bool` is an `int` subclass in Python, so `isinstance(True, int)` is True and
    the obvious integer reader accepts it. A report carrying a boolean where a
    line number belongs is a wrong-typed field, and a finding pointing at line 1
    of the file is worse than a rejection: it is a plausible-looking wrong answer
    on a PR comment. Split out from the table above because it is the one case
    the natural implementation gets wrong rather than crashing on.
    """
    _fake_scanner(
        tmp_path / "bin",
        "gitleaks",
        _write_report_script(
            "gitleaks", '[{"RuleID": "r", "File": "f", "StartLine": true}]'
        ),
        monkeypatch,
    )

    findings = gitleaks_tool.scan(_dev())
    _only_error_findings(findings, "gitleaks")
    assert "boolean" in findings[0].description, (
        f"the reason must say the field was a boolean, since 'expected an "
        f"integer' alone reads as a type the reader could have coerced; got "
        f"{findings[0].description!r}"
    )


def test_exception_signalled_absence_hides_real_faults_when_the_knob_is_off(
    monkeypatch, tmp_path
):
    """A KNOWN LIMIT, ACCEPTED BY RULING, pinned here so it cannot be forgotten.

    THIS TEST ASSERTS A FAIL-OPEN. It is not describing correct behaviour. It
    records the exact boundary of the plan's central ruling, decided deliberately
    rather than discovered later, so the limit lives in the suite instead of in a
    review thread.

    THE CAUSE IS EXCEPTION-SIGNALLING, NOT THE SEQUENTIAL LOOP. That distinction
        is the whole finding and it is easy to state backwards. `run_all_scanners`
        does iterate the three scanners in order -- but a sequential loop is
        harmless on its own. The abort happens because the knob-off ABSENT path
        signals absence by RAISING (`_run.unrunnable_findings`), and one raise
        ends the loop. Change nothing about the loop and the limit persists;
        change how absence is signalled and it disappears, which is exactly what
        the second half of this test measures. (Independently falsified in review:
        disabling only the absent `raise`, loop untouched, moved the knob-off
        outcome from `pass`/0 to `block`/3.)

        THIS TEST WAS ONCE NAMED `test_the_fan_out_stops_at_the_first_absent_
        scanner_and_hides_real_faults`, which named the loop as the cause -- the
        error this paragraph exists to correct. A docstring cannot outrun its own
        function name for a reader skimming the file, so the name was changed to
        say the mechanism instead. If you find yourself renaming it back towards
        the loop, re-read the falsification above first.

    WHAT IT DEMONSTRATES. With semgrep absent but gitleaks and trivy INSTALLED
        AND BROKEN, on a clean diff, measured: `verdict=pass, blocking=0`. Two
        genuine faults go unreported -- semgrep's raise ends the fan-out before
        they run, and the fixture fallback for a clean diff is `pass`.

    IT IS PRE-EXISTING: measured identical on 40f2e56, before any of this task's
        changes. It follows from raise-on-absent, which is the ruling's design.

    THE KNOB DISSOLVES IT, and this was MEASURED rather than assumed -- the
        measurement is why this docstring reads the way it does. The first version
        of this test asserted that `SCANNERS_REQUIRED=true` leaves the abort in
        place, on the reasoning that the knob changes only what the first failure
        SAYS. That assertion went RED. With the knob on, the absent branch RETURNS
        a finding instead of raising, so nothing aborts and all three scanners
        report -- the absent one and both real faults. The second half below pins
        that.

    WHY IT IS NOT FIXED -- RULED ON, not left open. Keep the raise. The two
        candidate fixes (the fan-out collecting per-scanner outcomes, or absence
        ceasing to be exception-signalled) both change the frozen
        `run_all_scanners` seam's behaviour and the meaning of the four
        fallback-dependent `len(blocking) == 2` assertions, days before a judged
        demo, on the seam that produces the demo's central claim.

        What the exposure actually is, once the knob is accounted for: a machine
        with SOME scanners installed and OTHERS installed-but-broken, with the
        knob OFF. That is not CI -- no binaries at all there, which is the plain
        absent path and the designed behaviour. It is not the demo machine or any
        production image -- all three installed, knob on, so no raise occurs and
        this limit does not exist. It is a HALF-PROVISIONED LAPTOP.

        ACCEPTED COST: such a machine under-reports faults until someone sets the
        knob. This test is what makes that acceptable rather than unknown.

    WHAT WOULD MAKE THE FIRST HALF GO RED: fixing the limit. That is a good
        outcome, not a regression -- when it happens, replace the first half with
        one asserting both real faults ARE reported. Do not silence it.
    """
    bin_dir = tmp_path / "bin"
    for tool in ("gitleaks", "trivy"):
        _fake_scanner(bin_dir, tool, 'echo "internal error" >&2\nexit 2', monkeypatch)
    # semgrep is deliberately NOT created here, and the fan-out runs semgrep
    # FIRST -- see the tuple in agentorg/security/__init__.py.
    assert not (bin_dir / "semgrep").exists(), "semgrep must be the absent one"

    state = RunState(
        ticket_id="CLEAN-3",
        ticket_text="add a harmless constant",
        plan=PlanResult(tasks=["t"], acceptance_criteria=["a"], target_files=["x"]),
        dev=_dev(),
    )

    result = security_agent.run(state)

    assert result.verdict == "pass", (
        f"THE KNOWN LIMIT: expected the documented fail-open (semgrep's absent "
        f"raise aborts the fan-out, the fixture fallback for a clean diff is "
        f"'pass'), got {result.verdict!r}. If this is red because both real "
        f"faults are now reported, the limit has been FIXED -- delete this test "
        f"and assert the fix instead. See this test's docstring."
    )
    assert result.blocking == [], (
        f"the two real faults are expected to be unreported here; got "
        f"{_summarize(result.blocking)}"
    )

    # THE SECOND HALF IS THE REMEDY, and it is a real one: with the knob on, the
    # absent branch RETURNS a finding instead of raising, so nothing aborts the
    # loop and all three scanners are reported -- the absent one and both real
    # faults. This is the assertion that identifies the abort as a property of
    # signalling absence by exception, not of the loop being sequential.
    monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)
    required = security_agent.run(state)

    assert required.verdict == "block", (
        f"SCANNERS_REQUIRED must make the absent scanner block; got "
        f"{required.verdict!r}"
    )
    assert {f.rule for f in required.blocking} == {
        "semgrep-scanner-error",
        "gitleaks-scanner-error",
        "trivy-scanner-error",
    }, (
        f"with the knob on, nothing raises, so the fan-out completes and every "
        f"scanner reports -- the absent one AND the two real faults the knob-off "
        f"path above loses. Got {_summarize(required.blocking)}. If this names "
        f"only semgrep, the absent branch has started raising again even under "
        f"the knob, which reintroduces the abort in the configuration the demo "
        f"machine runs."
    )


# ==========================================================================
# Task 4 -- the fan-out memoises by diff hash
#
# WHY THESE TESTS CANNOT USE A FAKE SCANNER ON PATH, WHICH EVERY FAULT TEST
# ABOVE DOES USE. Measured over the whole suite at 1171470, before any cache
# existed: 121 run_all_scanners calls, and NOT ONE of them is cacheable. 117
# RAISE FileNotFoundError (semgrep is first in the fan-out and no binary is
# installed), and the 4 that return, return only `*-scanner-error` faults --
# which this task's central rule says must never be memoised. A cache that is
# working therefore leaves the shipped suite's wrapper count UNCHANGED at 129.
#
# So the case under test -- a repeated scan that is genuinely skipped -- does
# not exist anywhere in the shipped suite and cannot be reached with a fake
# binary on PATH either, because every fake binary above is deliberately broken.
# These tests rebind the three module-level wrapper names instead, so a wrapper
# can return CLEAN findings. That is a weaker seam than a real subprocess and it
# is the right one here: the subject is whether the fan-out is RE-ENTERED, not
# what a broken binary does.
# ==========================================================================

# The lane module itself, so a test can rebind the three names run_all_scanners
# reads out of its own globals at call time. Fetched with import_module rather
# than a second `import` statement because this file's imports are at the top
# and a module-level import down here is E402. It also re-exports `Finding`
# (see agentorg/security/__init__.py), which is how the stubs below build clean
# findings without a new top-level import.
_LANE = importlib.import_module("agentorg.security")

# The names the fan-out iterates, in fan-out order. semgrep FIRST matters to
# test_a_raising_fan_out_..., which counts how far the loop got.
_WRAPPER_NAMES = ("_semgrep", "_gitleaks", "_trivy")


def _stub_wrappers(monkeypatch, answer) -> list[str]:
    """Replace the three wrapper names; return the live invocation log.

    The returned list gets one tool name appended per wrapper invocation, so a
    test reads it as the COST counter: `[]` after a second call means the
    fan-out was skipped. `answer(tool, dev)` supplies that wrapper's findings,
    so a stub can be clean, faulting, or raising.
    """
    calls: list[str] = []

    def make(tool: str):
        def stub(dev):
            calls.append(tool)
            return answer(tool, dev)

        return stub

    for name in _WRAPPER_NAMES:
        monkeypatch.setattr(_LANE, name, make(name.removeprefix("_")))
    return calls


def _clean_finding(tool: str, dev) -> list:
    """One harmless `low` finding -- a CLEAN scan that is NOT an empty list.

    Deliberately not `[]`. Every assertion below would also pass against a
    cache that stored nothing and returned nothing, because `[] == []`. A
    non-empty clean answer makes "the cache returned the right thing" and "the
    cache returned nothing" different observations.
    """
    return [
        _LANE.Finding(
            tool=tool,
            severity="low",
            rule=f"{tool}-noop",
            file="app/noop.py",
            line=1,
            description="a clean scan that found something harmless",
        )
    ]


_CLEAN_RULES = {"semgrep-noop", "gitleaks-noop", "trivy-noop"}

# The fault rules, DERIVED the same way the implementation derives them, rather
# than the three literals that used to sit here.
#
# Two reasons the literals were wrong. First, the same identifier also exists in
# agentorg/security/__init__.py, so one name meant "what the cache refuses to
# store" there and "what this file types out by hand" here -- and only one of them
# moves when `error_finding` changes. Second and worse, restating them left the
# implementation's derivation pinned by NOTHING: measured in review, replacing the
# module's derived set with this exact literal and dropping its `error_finding`
# import kept all 186 tests green, because today both spellings produce the same
# three strings. The literals agreed with the code for exactly as long as the code
# was right, which is the definition of an assertion that pins nothing.
#
# `test_the_fault_rule_set_is_derived_from_the_tool_type_and_not_restated` is the
# pin that the literals were missing.
_EXPECTED_FAULT_RULES = {
    error_finding(tool, "").rule for tool in get_args(ScannerTool)
}


def _verdict_of(findings: list) -> str:
    return compute_security_verdict(
        findings, threshold=config.SECURITY_BLOCK_THRESHOLD
    )[0]


def test_the_fan_out_scans_a_repeated_diff_only_once(monkeypatch):
    """The cache exists at all: a second ask for the same diff re-enters nothing.

    Both halves are asserted, because either alone is satisfiable by a bug. The
    COST assertion alone passes against a cache that answers everything from one
    entry; the RESULT assertion alone passes against no cache at all.

    The second call passes a FRESH DevResult carrying the same diff text. The
    key is the diff, not the object -- agents/security.py builds its DevResult
    from the graph state and a repeat of the demo hands over a different object
    every time, so an identity-keyed cache would never hit where it matters.
    """
    calls = _stub_wrappers(monkeypatch, _clean_finding)

    first = run_all_scanners(_dev())
    assert calls == ["semgrep", "gitleaks", "trivy"], (
        f"the first call must actually fan out to all three, in order; got "
        f"{calls!r}"
    )

    second = run_all_scanners(_dev())

    assert calls == ["semgrep", "gitleaks", "trivy"], (
        f"the second call re-entered the fan-out: {calls!r}. The whole point of "
        f"the cache is that a repeated diff costs nothing -- the demo scans the "
        f"same two fixtures over and over."
    )
    assert second == first, (
        f"a cache hit must return the same findings the fan-out produced. "
        f"first={_summarize(first)} second={_summarize(second)}"
    )
    assert {f.rule for f in second} == _CLEAN_RULES, (
        f"got {_summarize(second)}; an empty or partial hit would pass a bare "
        f"equality check against an equally empty first result"
    )


def test_a_caller_mutating_the_returned_list_cannot_corrupt_a_later_result(
    monkeypatch,
):
    """The cache hands out COPIES, in both directions, at both levels.

    A cache that returns its own list lets any caller edit what every later
    caller sees, and `findings` is handed straight to compute_security_verdict.
    FOUR distinct defects, and this test's structure is the cross product,
    because each half is fixed by a different line of the implementation:

      * copy on the way IN vs. copy on the way OUT. The first protects the list
        the fan-out built on a MISS; the second protects what a HIT hands over.
        MEASURED that these are independent: replacing `return _copy(cached)`
        with `return cached` left the first half of this test GREEN, because a
        miss-path result was all it had ever mutated. The demo's repeats are all
        hits, so the OUT half is the one that matters on stage.
      * the LIST vs. the ELEMENTS. `del`, `append` and `clear` on the returned
        list must not change the cached contents; and `finding.severity = "low"`
        on a returned Finding must not either. The element half is the
        security-relevant one -- severity is the only field the block rule reads,
        so a shared Finding object is one assignment away from turning a cached
        `critical` into a pass, which is why `_copy` is DEEP.
    """
    _stub_wrappers(monkeypatch, _clean_finding)

    first = run_all_scanners(_dev())
    baseline = [f.model_copy(deep=True) for f in first]
    assert len(baseline) == 3, f"expected one finding per tool, got {len(baseline)}"

    first[0].severity = "critical"
    first[0].rule = "tampered-by-the-caller"
    del first[1:]
    first.append(
        _LANE.Finding(
            tool="trivy",
            severity="critical",
            rule="injected-by-the-caller",
            file="x",
            line=0,
            description="not from any scanner",
        )
    )

    second = run_all_scanners(_dev())

    assert second == baseline, (
        f"the FIRST caller's edits reached the cache. expected "
        f"{_summarize(baseline)}, got {_summarize(second)}. If "
        f"'tampered-by-the-caller' or 'injected-by-the-caller' appears here, "
        f"what went INTO the cache was the very list the fan-out handed the "
        f"caller, so the security gate now reads whatever that caller left "
        f"behind."
    )

    # AND NOW THE SAME ABUSE FROM A CACHE HIT, which is a SEPARATE defect with a
    # separate fix: the paragraph above is protected by copying on the way IN,
    # this one by copying on the way OUT. MEASURED that the distinction is real
    # -- with `return cached` in place of `return _copy(cached)`, everything
    # above still PASSED, because `first` came from a miss. A hit is also the
    # common case in the demo, where every repeat after the first is one.
    hit = run_all_scanners(_dev())
    hit[0].severity = "critical"
    hit[0].rule = "tampered-via-a-cache-hit"
    hit.clear()

    third = run_all_scanners(_dev())

    assert third == baseline, (
        f"a caller that mutated a CACHE HIT corrupted the cache. expected "
        f"{_summarize(baseline)}, got {_summarize(third)}. An empty list here "
        f"is the worst case: compute_security_verdict([]) returns ('pass', []), "
        f"so one caller's `.clear()` turns every later scan of this diff into a "
        f"silent pass."
    )


def test_the_clean_and_poisoned_fixtures_never_share_a_cache_entry(monkeypatch):
    """A collision here promotes the poisoned diff. Both directions are asserted.

    The stub answers by INSPECTING the diff it was handed, so a collision shows
    up as the wrong ANSWER rather than merely a wrong count -- and both answers
    are non-empty, so "poisoned got clean's entry" and "clean got poisoned's
    entry" are each positively detectable rather than one of them being the
    indistinguishable empty list.

    The second pair of calls is issued in the REVERSE order, after both entries
    exist, which is the arrangement a single-slot cache survives least well.
    """

    def answer(tool: str, dev) -> list:
        if "AKIA" in (dev.diff or ""):
            return [
                _LANE.Finding(
                    tool=tool,
                    severity="critical",
                    rule=f"{tool}-secret",
                    file="app/auth.py",
                    line=3,
                    description="a committed credential",
                )
            ]
        return _clean_finding(tool, dev)

    _stub_wrappers(monkeypatch, answer)

    clean = fixtures_loader.dev(poisoned=False)
    poisoned = fixtures_loader.dev(poisoned=True)

    first_clean = run_all_scanners(clean)
    first_poisoned = run_all_scanners(poisoned)

    again_poisoned = run_all_scanners(poisoned)
    again_clean = run_all_scanners(clean)

    assert {f.rule for f in again_poisoned} == {
        "semgrep-secret",
        "gitleaks-secret",
        "trivy-secret",
    }, (
        f"the poisoned diff came back with {_summarize(again_poisoned)}. If "
        f"those are the clean fixture's findings, the two diffs share a cache "
        f"entry and the demo's blocked ticket promotes."
    )
    assert again_poisoned == first_poisoned
    assert _verdict_of(again_poisoned) == "block", (
        f"a cached poisoned scan must still BLOCK at threshold "
        f"{config.SECURITY_BLOCK_THRESHOLD!r}; got {_summarize(again_poisoned)}"
    )

    assert {f.rule for f in again_clean} == _CLEAN_RULES, (
        f"the clean diff came back with {_summarize(again_clean)}. If those are "
        f"the poisoned fixture's findings, the cache blocks the demo's promote "
        f"half."
    )
    assert again_clean == first_clean
    assert _verdict_of(again_clean) == "pass"


def test_a_scanner_fault_is_never_memoised_so_one_timeout_cannot_stick(
    monkeypatch,
):
    """The rule this task exists for: a fault is not a result worth remembering.

    One transient timeout, memoised, would answer every later scan of that diff
    in the process with the same blocking scanner-error -- including the demo's
    next repeat, on a machine where the scanner is now perfectly healthy. The
    fault is still returned; it is simply not REMEMBERED.
    """
    healthy = {"yes": False}

    def answer(tool: str, dev) -> list:
        if healthy["yes"]:
            return _clean_finding(tool, dev)
        return [error_finding(tool, "connection reset while downloading the db")]

    calls = _stub_wrappers(monkeypatch, answer)

    faulted = run_all_scanners(_dev())
    assert {f.rule for f in faulted} == _EXPECTED_FAULT_RULES, (
        f"expected a fault from every tool, got {_summarize(faulted)}"
    )
    assert _verdict_of(faulted) == "block", "a fault must still fail CLOSED"

    healthy["yes"] = True
    recovered = run_all_scanners(_dev())

    assert len(calls) == 6, (
        f"the fan-out ran {len(calls)} wrapper(s) over two calls ({calls!r}); "
        f"six is what a genuine retry costs. Fewer means the fault was served "
        f"from the cache."
    )
    assert {f.rule for f in recovered} == _CLEAN_RULES, (
        f"a failing scan followed by a working one must produce the WORKING "
        f"result; got {_summarize(recovered)}. A memoised fault poisons every "
        f"later run of this diff in this process."
    )
    assert _verdict_of(recovered) == "pass"


def test_a_result_that_is_only_partly_a_fault_is_not_memoised_either(monkeypatch):
    """One dead scanner among three still makes the whole result unrememberable.

    The realistic shape, and the one an `all()` test for faultiness gets wrong:
    semgrep and trivy parse their reports fine, gitleaks times out. Two thirds
    of that answer is worth keeping and the third is a transient fault -- so the
    answer as a whole must not be. Caching it would pin gitleaks' timeout to
    this diff for the life of the process while looking, in the findings list,
    mostly correct.
    """
    healthy = {"yes": False}

    def answer(tool: str, dev) -> list:
        if tool == "gitleaks" and not healthy["yes"]:
            return [error_finding(tool, "timed out")]
        return _clean_finding(tool, dev)

    calls = _stub_wrappers(monkeypatch, answer)

    mixed = run_all_scanners(_dev())
    assert {f.rule for f in mixed} == {
        "semgrep-noop",
        "gitleaks-scanner-error",
        "trivy-noop",
    }, f"expected two clean and one fault, got {_summarize(mixed)}"
    assert _verdict_of(mixed) == "block"

    healthy["yes"] = True
    recovered = run_all_scanners(_dev())

    assert len(calls) == 6, (
        f"a partly-faulted result was memoised: {len(calls)} wrapper call(s) "
        f"over two calls ({calls!r}), where a retry costs six"
    )
    assert {f.rule for f in recovered} == _CLEAN_RULES, (
        f"got {_summarize(recovered)}; gitleaks' transient timeout is still "
        f"pinned to this diff"
    )
    assert _verdict_of(recovered) == "pass"


def test_a_raising_fan_out_is_not_memoised_and_the_next_call_really_retries(
    monkeypatch,
):
    """A raise is not a result, AND it is not a stored exception to replay.

    117 of the 121 shipped run_all_scanners calls RAISE FileNotFoundError -- the
    absent-scanner path, which agents/security.py catches and answers from the
    fixture. So this is not an edge case in this repository; it is the ordinary
    path, and getting it wrong is the difference between a cache that does
    nothing and a cache that breaks CI.

    THE MIDDLE ASSERTION IS THE WHOLE TEST. Asserting only that the second call
    raises is satisfied by an implementation that stored the exception object
    and re-raised it -- which would be a memoised fault by another name, and
    would keep re-raising "semgrep is not installed" after semgrep was
    installed. Counting wrapper invocations is what separates a genuine retry
    from a replay.
    """
    mode = {"raise": True}

    def answer(tool: str, dev) -> list:
        if mode["raise"]:
            raise FileNotFoundError(f"{tool} is not installed")
        return _clean_finding(tool, dev)

    calls = _stub_wrappers(monkeypatch, answer)

    with pytest.raises(FileNotFoundError):
        run_all_scanners(_dev())
    assert calls == ["semgrep"], (
        f"semgrep is first in the fan-out and it raised, so the loop must have "
        f"ended there; got {calls!r}"
    )

    with pytest.raises(FileNotFoundError):
        run_all_scanners(_dev())
    assert calls == ["semgrep", "semgrep"], (
        f"the second call did not re-enter the fan-out ({calls!r}). Either the "
        f"raise was stored and replayed, or a partial result was cached -- and "
        f"if that result was the empty list, compute_security_verdict([]) "
        f"returns ('pass', []) and this is the silent pass."
    )

    mode["raise"] = False
    findings = run_all_scanners(_dev())

    assert {f.rule for f in findings} == _CLEAN_RULES, (
        f"once the scanners work, the fan-out must produce their real answer; "
        f"got {_summarize(findings)}"
    )


def test_dev_is_none_stays_uncached_and_is_not_confused_with_an_empty_diff(
    monkeypatch,
):
    """`dev is None` has no diff to hash, so it gets no cache entry.

    Two reasons, and the second is the dangerous one:

      * There is nothing to memoise. The None path scans nothing, so a hit saves
        no work -- it only adds a shared mutable list for callers to edit.
      * `None` and a DevResult whose diff is `""` are DIFFERENT QUESTIONS. Give
        the None path the empty-diff key and they collide, and an empty diff is
        one that must still be SCANNED. That is the same defect as clean and
        poisoned colliding, in miniature, and it fails open: `[]` makes
        compute_security_verdict return ('pass', []).
    """
    calls = _stub_wrappers(monkeypatch, _clean_finding)

    first = run_all_scanners(None)
    second = run_all_scanners(None)

    assert first == [] and second == [], f"got {first!r} and {second!r}"
    assert first is not second, (
        "each None call must get its OWN list. A shared cached list is mutable "
        "and every caller holds the same one."
    )
    assert calls == [], f"the None path must scan nothing; it ran {calls!r}"

    empty_diff = DevResult(
        branch="feat/empty", diff="", summary="s", files_changed=[]
    )
    findings = run_all_scanners(empty_diff)

    assert calls == ["semgrep", "gitleaks", "trivy"], (
        f"a DevResult carrying an empty diff must be SCANNED, not answered from "
        f"the None path's entry; wrappers ran {calls!r}"
    )
    assert {f.rule for f in findings} == _CLEAN_RULES, (
        f"got {_summarize(findings)} -- an empty diff that comes back empty "
        f"because it collided with `dev is None` is a scan that never happened"
    )


def test_a_repeat_scan_returns_the_same_findings_in_well_under_a_second(
    monkeypatch,
):
    """The brief's timing clause, with the instrument proved against the slow case.

    A timing assertion that only ever sees the fast path cannot tell a working
    cache from a stopped clock. So the FIRST assertion here is that the same
    timer reports the UNCACHED call as slow: each stub sleeps, so a real fan-out
    cannot come in under the sleep budget. Only then is the second call's number
    worth anything.

    `time.sleep` in a rebound wrapper, not a shell script on PATH -- the fake
    scanners above cannot sleep, because nothing external resolves under the
    replaced PATH and `sleep` is not a shell builtin. That trap already made one
    timeout test pass without a timeout; see _fake_scanner.
    """
    import time

    per_wrapper_seconds = 0.05
    budget = per_wrapper_seconds * len(_WRAPPER_NAMES)

    def answer(tool: str, dev) -> list:
        time.sleep(per_wrapper_seconds)
        return _clean_finding(tool, dev)

    _stub_wrappers(monkeypatch, answer)

    started = time.perf_counter()
    first = run_all_scanners(_dev())
    uncached = time.perf_counter() - started

    assert uncached >= budget * 0.8, (
        f"the uncached fan-out took {uncached:.4f}s, under the {budget:.2f}s "
        f"this test's stubs sleep for. The timer or the stubs are not measuring "
        f"the real call, so the cached number below would mean nothing."
    )

    started = time.perf_counter()
    second = run_all_scanners(_dev())
    cached = time.perf_counter() - started

    assert second == first, (
        f"a repeat must return IDENTICAL findings, not merely fast ones. "
        f"first={_summarize(first)} second={_summarize(second)}"
    )
    assert cached < 1.0, (
        f"the repeat took {cached:.4f}s; the brief's bar is well under a second"
    )
    assert cached < uncached / 5, (
        f"the repeat took {cached:.4f}s against an uncached {uncached:.4f}s. "
        f"That is not a cache hit -- it re-ran the scanners."
    )


def test_the_poisoned_loop_still_blocks_ten_out_of_ten_with_the_cache_in_place(
    monkeypatch,
):
    """The demo's central claim, ten times, through the real security agent.

    No stubs: this is whatever path the machine actually takes. With no scanners
    installed (CI's `test` job, and this worktree) the fan-out RAISES on every
    iteration and the fixture fallback answers -- which is the path the cache
    must leave completely alone, and the one a cache that memoised the raise
    would break in the fail-OPEN direction: `[]` reaches
    compute_security_verdict and returns ('pass', []).

    Ten iterations rather than one because the failure this guards against
    appears only from the SECOND call onward. Iteration 0 passes under every
    broken cache in this file's RED steps.

    The two blocking findings are the same two on either path -- the fixture
    fallback's AWS-key pair, or real gitleaks' pinned pair from
    scripts/scan_gate.py's EXPECTED_BLOCKING -- so this asserts the same number
    the five shipped `len(blocking) == 2` assertions do.
    """
    state = RunState(
        ticket_id="POISON-CACHE",
        ticket_text="add a per-IP login rate limit",
        plan=PlanResult(tasks=["t"], acceptance_criteria=["a"], target_files=["x"]),
        dev=fixtures_loader.dev(poisoned=True),
    )

    for attempt in range(10):
        result = security_agent.run(state)
        assert result.verdict == "block", (
            f"attempt {attempt}: the poisoned diff must block every time, got "
            f"{result.verdict!r}. A cache that remembered a fault, a raise, or "
            f"an empty list breaks exactly this, and only from attempt 1 on."
        )
        assert len(result.blocking) == 2, (
            f"attempt {attempt}: expected 2 blocking findings, got "
            f"{_summarize(result.blocking)}"
        )


# ==========================================================================
# Task 4 -- test isolation for a PROCESS-LIFETIME cache
# ==========================================================================


@pytest.fixture(autouse=True)
def _scanner_cache_is_per_test():
    """Clear the fan-out's memo around every test in this file. NOT optional.

    THIS WAS MEASURED, NOT ANTICIPATED. With the cache landed and this fixture
    absent, FIVE tests in this file failed -- and the way they failed is the
    argument for the fixture:

        E  expected a fault from every tool, got semgrep:semgrep-noop(low);
           gitleaks:gitleaks-noop(low); trivy:trivy-noop(low)
        E  the uncached fan-out took 0.0000s, under the 0.15s this test's stubs
           sleep for
        E  attempt 0: expected 2 blocking findings, got semgrep:semgrep-secret
           (critical); gitleaks:gitleaks-secret(critical); ...

    Every one of those is an earlier test's answer being served to a later test.
    The diffs here are SHARED -- `_dev()`'s harmless diff and the two demo
    fixtures -- so collision is the default, not bad luck, and note the third
    line: a leaked entry reached the real security agent and took a
    `len(blocking) == 2` assertion red. That is the shape of the five shipped
    assertions this task must not break.

    IT CLEARS ON BOTH SIDES. Before, so no test inherits an entry -- from this
    file or from any file that ran earlier. After, so nothing this file stored
    escapes into the rest of the suite. One side alone leaves the other
    direction open, and the leak is silent either way: a stale hit looks exactly
    like a scan.

    WHY A PUBLIC RESET RATHER THAN monkeypatch ON THE DICT. `reset_scanner_cache`
    is the lane's own function, so this fixture pins no private shape; swapping
    in a fresh dict with `monkeypatch.setattr(_LANE, "_CACHE", {})` would work
    today and break the moment the cache stops being a bare module-level dict.
    It also gives a long-running caller the same hook.

    WHY IT DOES NOT WEAKEN THE CONFTEST GUARDS: it touches neither the model
    seam, the GitHub seam, `builtins.input`, nor the workspace redirect, and it
    catches nothing -- so the four autouse raisers in tests/conftest.py, whose
    `pytest.fail` derives from BaseException, are unaffected.

    WHAT IT DOES NOT COVER, STATED RATHER THAN IMPLIED: tests in OTHER files.
    They keep a process-lifetime cache between them. That is currently harmless
    for a measured reason and not a designed one -- of the 121 fan-out calls in
    the shipped suite, 117 raise and the other 4 return only faults, so nothing
    outside this file ever stores an entry to leak. If another lane adds a test
    whose scanners return clean findings, it will need the same clearing, and
    conftest.py is where that belongs.
    """
    _LANE.reset_scanner_cache()
    yield
    _LANE.reset_scanner_cache()


def test_the_fault_rule_set_is_derived_from_the_tool_type_and_not_restated(
    monkeypatch,
):
    """The cache's fault test must DERIVE its rule set, not restate it.

    THIS TEST EXISTS BECAUSE ITS ABSENCE WAS MEASURED. Review replaced the
    implementation's derived set with a byte-identical hardcoded literal --
    `{"semgrep-scanner-error", "gitleaks-scanner-error", "trivy-scanner-error"}`
    -- and dropped the `error_finding` import, and ALL 186 TESTS PASSED. Every
    assertion in this file was satisfied, because today the derivation and the
    literal produce the same three strings. The coupling the implementation's
    docstring defends at length was pinned by nothing at all.

    IT ALSO CLOSES A FAIL-OPEN THAT WAS REAL, NOT HYPOTHETICAL. The first version
    of that code derived the RULE SPELLING from `error_finding` but iterated a
    hardcoded three-tool tuple, under a comment claiming a fourth tool "cannot
    leave a fault silently cacheable". Measured: it could. A fourth tool's
    `bandit-scanner-error` is absent from a set built off three hardcoded names,
    so `_is_fault_free` returns True and that tool's transient timeout is pinned
    to the diff for the life of the process -- the exact defect Task 4 exists to
    prevent, reintroduced by the code that prevents it.

    SO BOTH SOURCES OF TRUTH ARE SUBSTITUTED HERE, because a test that patched
    only one would still pass against a set that hardcoded the other:

      * `ScannerTool` gains a FOURTH tool. If the implementation reads
        `get_args(ScannerTool)`, the new tool's fault rule appears and the fault
        is refused. If it iterates a literal tuple, the rule is missing and the
        fault gets CACHED -- which this test observes as a wrapper count, not as
        a set comparison, because the cache is the behaviour that matters.
      * `error_finding` changes its rule SPELLING. If the implementation calls it,
        the set follows. If it restates the strings, they no longer match anything
        the function produces and every fault becomes cacheable.

    A `Literal` is not patchable in place, so `_run.ScannerTool` is REBOUND to a
    four-member Literal. The implementation must read both names through the
    `_run` module for that to be visible -- which is why it does, and is the same
    reason `unrunnable_findings`'s docstring in _run.py gives for reading
    `config.SCANNERS_REQUIRED` through `config` rather than importing the value
    (that rationale is in the FUNCTION's docstring, not the module's).
    """
    from agentorg.security import _run

    # --- part 1: a FOURTH tool's fault must not be cacheable -------------
    #
    # `Finding.tool` is a frozen three-member Literal, so a real fourth tool
    # cannot be constructed. `error_finding` is therefore also substituted, to
    # spell the fourth tool's rule the way it spells the other three while still
    # returning a Finding the frozen model accepts.
    real_error_finding = _run.error_finding

    def error_finding_for_four(tool, reason):
        finding = real_error_finding("semgrep", reason)
        return finding.model_copy(update={"rule": f"{tool}-scanner-error"})

    monkeypatch.setattr(
        _run, "ScannerTool", Literal["semgrep", "gitleaks", "trivy", "bandit"]
    )
    monkeypatch.setattr(_run, "error_finding", error_finding_for_four)

    def fourth_tool_faults(tool: str, dev) -> list:
        # semgrep and trivy are healthy; the fourth tool is the one that died.
        # Emitted from gitleaks' slot so the fan-out's three-call shape is kept.
        if tool == "gitleaks":
            return [error_finding_for_four("bandit", "the fourth scanner timed out")]
        return _clean_finding(tool, dev)

    calls = _stub_wrappers(monkeypatch, fourth_tool_faults)

    first = run_all_scanners(_dev())
    assert "bandit-scanner-error" in {f.rule for f in first}, (
        f"the fourth tool's fault must be in the result at all; got "
        f"{_summarize(first)} -- this test's own setup is broken if not"
    )

    run_all_scanners(_dev())

    assert len(calls) == 6, (
        f"a FOURTH tool's fault was memoised: {len(calls)} wrapper call(s) over "
        f"two calls ({calls!r}), where a genuine retry costs six. The cache's "
        f"fault test is iterating a hardcoded tool list instead of "
        f"get_args(ScannerTool), so 'bandit-scanner-error' is not in its rule set "
        f"and one transient timeout is now pinned to this diff for the life of "
        f"the process."
    )

    # --- part 2: a changed rule SPELLING must be followed ----------------
    #
    # If the implementation restates the rule strings instead of calling
    # error_finding, a respelled fault matches nothing it refuses and gets cached.
    monkeypatch.setattr(
        _run, "ScannerTool", Literal["semgrep", "gitleaks", "trivy"]
    )

    def respelled(tool, reason):
        finding = real_error_finding(tool, reason)
        return finding.model_copy(update={"rule": f"{tool}-SCAN-FAILED"})

    monkeypatch.setattr(_run, "error_finding", respelled)

    def respelled_fault(tool: str, dev) -> list:
        return [respelled(tool, "the scanner died")]

    respelled_calls = _stub_wrappers(monkeypatch, respelled_fault)

    faulted = run_all_scanners(_dev())
    assert {f.rule for f in faulted} == {
        "semgrep-SCAN-FAILED",
        "gitleaks-SCAN-FAILED",
        "trivy-SCAN-FAILED",
    }, f"this test's own setup is broken; got {_summarize(faulted)}"

    run_all_scanners(_dev())

    assert len(respelled_calls) == 6, (
        f"a RESPELLED fault was memoised: {len(respelled_calls)} wrapper call(s) "
        f"over two calls ({respelled_calls!r}), where a retry costs six. The "
        f"cache is comparing against hardcoded rule strings rather than asking "
        f"error_finding what it produces, so every fault it no longer recognises "
        f"is now cacheable -- and a fault that is cached is the fail-open this "
        f"whole task exists to close."
    )


# ==========================================================================
# Task 5 -- two defects found in review of this lane
#
# 1. semgrep leaks the scratch directory into Finding.file.
# 2. The rendered block explanation is not byte-stable, because no scanner
#    orders its report and the fan-out concatenates in arrival order.
#
# WHY THE OBVIOUS TEST FOR DEFECT 2 IS VACUOUS HERE, MEASURED BEFORE WRITING
# THESE. The brief says "run the poisoned scan repeatedly and assert the
# rendered explanation is byte-identical every time". On this machine that
# passes with the sort deleted. With no scanner binary on PATH the first
# wrapper raises, agents/security.py catches it and returns
# fixtures/security_result_block.json wholesale -- verdict, the two blocking
# findings AND the explanation, which in that file is a FIXED STRING. Measured:
#
#     verdict block, 3 findings, 2 blocking
#     explanation == the fixture's constant  -> True
#
# So a ten-run byte-stability test on the agent's output pins that JSON file
# and nothing else. Worse, the seam most agent tests reach for --
# monkeypatching `security.run_all_scanners` -- REPLACES the function the sort
# lives in, so a sort inside the lane is invisible to it either way.
#
# The tests below therefore stub the three WRAPPER names inside the lane
# module, exactly as the Task 4 tests do, so the real `run_all_scanners` runs
# and the real sort is the only thing that can order the result. Each one
# varies its INPUT order across iterations, so a stable output is evidence of
# the sort rather than evidence of a stable input.
# ==========================================================================


def _permutable_findings() -> list:
    """Findings that span every field the sort key reads.

    Deliberately NOT the demo fixture's two gitleaks hits: those agree on tool,
    file and severity, so a key that only looked at `tool` would order them
    identically and read as working. These differ in tool, file, line, rule and
    severity, so a key that drops any component leaves at least one pair
    input-ordered.
    """
    return [
        _LANE.Finding(tool="semgrep", severity="high", rule="b.rule",
                      file="app/auth.py", line=9, description="d1"),
        _LANE.Finding(tool="gitleaks", severity="critical", rule="a.rule",
                      file="app/auth.py", line=4, description="d2"),
        _LANE.Finding(tool="gitleaks", severity="critical", rule="a.rule",
                      file="app/auth.py", line=3, description="d3"),
        _LANE.Finding(tool="trivy", severity="high", rule="c.rule",
                      file="requirements.txt", line=1, description="d4"),
    ]


def test_the_fan_out_orders_its_findings_the_same_way_whatever_order_they_arrive(
    monkeypatch,
):
    """Every input permutation must leave `run_all_scanners` in ONE order.

    This is the pin for defect 2, and it is written against the real lane
    function on purpose -- see this section's header for why the agent-level
    byte-stability test the brief describes is vacuous on a machine with no
    scanners installed.

    ALL 24 permutations of four findings are driven through, not ten repeats of
    one: repeating a stable input proves only that the input was stable. The
    cache is cleared between permutations, because otherwise permutation 2
    would be answered from permutation 1's entry and this test would pass
    against no sort at all.
    """
    orders = {}
    for permutation in itertools.permutations(_permutable_findings()):
        _LANE.reset_scanner_cache()

        def answer(tool: str, dev, _p=permutation) -> list:
            # The whole permutation comes out of one wrapper slot. The subject is
            # the ORDER the fan-out returns, and splitting the findings across
            # slots would let the fan-out's own fixed iteration order do the
            # sorting -- which is exactly the property under test.
            return [f.model_copy(deep=True) for f in _p] if tool == "gitleaks" else []

        _stub_wrappers(monkeypatch, answer)

        result = run_all_scanners(_dev())
        key = tuple((f.tool, f.file, f.line, f.rule) for f in result)
        orders.setdefault(key, []).append([f.rule for f in permutation])

    assert len(orders) == 1, (
        f"the fan-out returned {len(orders)} DIFFERENT orders across the 24 "
        f"input permutations of the same four findings, so what a reviewer "
        f"reads depends on which order the scanners happened to answer in. "
        f"Orders seen: { {k: v[0] for k, v in orders.items()} }"
    )


def test_the_rendered_block_explanation_is_byte_identical_across_repeated_scans(
    monkeypatch,
):
    """The projector line itself, not just the findings list.

    Defect 2's real consequence: `_default_explanation` joins the blocking
    findings in list order, and `compute_security_verdict` builds `blocking` by
    comprehension over `findings`, so it inherits whatever order the fan-out
    returned. This drives the REAL security agent -- with the wrappers stubbed
    so the fan-out does not raise and the fixture fallback is never reached --
    and asserts the rendered string is byte-identical while the input order
    varies underneath it.

    The model is left disabled by conftest's autouse fixture, so `_explain`
    falls to `_default_explanation`. That is the deterministic prose the demo
    shows when no model answers, and it is the string this defect corrupts.
    """
    explanations = set()
    for permutation in itertools.permutations(_permutable_findings()):
        _LANE.reset_scanner_cache()

        def answer(tool: str, dev, _p=permutation) -> list:
            return [f.model_copy(deep=True) for f in _p] if tool == "gitleaks" else []

        _stub_wrappers(monkeypatch, answer)

        state = RunState(ticket_id="ORDER-1", ticket_text="t")
        state.dev = _dev()
        result = security_agent.run(state)

        assert result.explanation.startswith("Blocked: "), (
            f"this test's own setup is broken: it must reach the "
            f"deterministic explanation, not the fixture fallback or a model. "
            f"Got {result.explanation!r}"
        )
        explanations.add(result.explanation)

    assert len(explanations) == 1, (
        f"the block explanation rendered {len(explanations)} different ways "
        f"across 24 input permutations of the same findings. A repeated demo "
        f"run reads differently each time. Variants: {sorted(explanations)}"
    )


def test_semgrep_reports_a_repo_relative_path_not_the_scratch_directory(
    monkeypatch, tmp_path
):
    """The pin for defect 1: no `/var/folders/...` in `Finding.file`.

    semgrep reports paths under the temp dir the wrapper handed it, and that
    string reaches the PR comment and the projector. gitleaks already strips it
    with `_repo_relative`; this asserts semgrep does too.

    The fake scanner derives the temp directory from its own report argument --
    `${arg%/*}` -- rather than being told it, because the wrapper chooses that
    directory and a hardcoded guess would test a path the wrapper never used.

    Asserted as EQUALITY against `app/auth.py`, not merely "no /var in it": a
    wrapper that returned `unknown`, or a bare basename, or an empty string
    would satisfy a negative assertion while still not naming the file a
    reviewer has to open.
    """
    script = (
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *semgrep-report.json)\n'
        '      d="${arg%/*}"\n'
        '      echo "{\\"results\\":[{\\"check_id\\":\\"py.timeout\\",'
        '\\"path\\":\\"$d/app/auth.py\\",'
        '\\"extra\\":{\\"severity\\":\\"ERROR\\",\\"message\\":\\"m\\"},'
        '\\"start\\":{\\"line\\":9}}]}" > "$arg"\n'
        '      ;;\n'
        '  esac\n'
        'done\n'
        'exit 0'
    )
    _fake_scanner(tmp_path / "bin", "semgrep", script, monkeypatch)

    findings = semgrep_tool.scan(_dev())

    assert len(findings) == 1, (
        f"this test's own setup is broken -- expected one semgrep finding, got "
        f"{_summarize(findings)}"
    )
    assert findings[0].file == "app/auth.py", (
        f"semgrep must report the repo-relative path, like gitleaks does via "
        f"_repo_relative. Got {findings[0].file!r} -- a scratch directory that "
        f"exists only for the duration of the scan, printed on a PR comment and "
        f"on the projector."
    )


def test_a_cached_result_is_in_the_same_order_as_a_fresh_one(monkeypatch):
    """The sort must run BEFORE the store, so the memo holds sorted findings.

    The brief forbids a cached result differing from a fresh one, and ORDER is a
    way to differ that no `len()` or set-based assertion would notice. This is
    the pin for WHERE the sort sits rather than whether it exists: with
    `findings.sort(...)` moved one line down, past `_CACHE[key] = _copy(...)`,
    the first caller gets sorted findings and every later caller gets the
    unsorted list the wrappers actually returned. MEASURED against that
    mutation -- fresh `[('gitleaks','a'), ('trivy','c')]`, cached
    `[('trivy','c'), ('gitleaks','a')]`.

    The wrappers answer in an order the key must actively reverse, so a sort
    that never ran and a sort that ran on both paths give different answers.
    """
    unsorted_first = [
        _LANE.Finding(tool="trivy", severity="low", rule="c.rule",
                      file="requirements.txt", line=1, description="d"),
        _LANE.Finding(tool="gitleaks", severity="low", rule="a.rule",
                      file="app/auth.py", line=2, description="d"),
    ]

    def answer(tool: str, dev) -> list:
        if tool == "gitleaks":
            return [f.model_copy(deep=True) for f in unsorted_first]
        return []

    calls = _stub_wrappers(monkeypatch, answer)

    fresh = run_all_scanners(_dev())
    cached = run_all_scanners(_dev())

    assert len(calls) == 3, (
        f"this test's own setup is broken: the second call must be a cache HIT, "
        f"so the fan-out should have run once (three wrappers). Got {calls!r}"
    )
    assert [(f.tool, f.rule) for f in fresh] == [(f.tool, f.rule) for f in cached], (
        f"a cached result came back in a DIFFERENT order than the fresh one: "
        f"fresh {[(f.tool, f.rule) for f in fresh]} vs cached "
        f"{[(f.tool, f.rule) for f in cached]}. The sort is running after the "
        f"cache store, so the memo holds the wrappers' arrival order and only "
        f"the very first caller sees a sorted list."
    )
    assert [(f.tool, f.rule) for f in fresh] == [
        ("gitleaks", "a.rule"),
        ("trivy", "c.rule"),
    ], (
        f"this test's own setup is broken -- the input order must be one the "
        f"sort actually changes, or it cannot tell a missing sort from a "
        f"working one. Got {[(f.tool, f.rule) for f in fresh]}"
    )
