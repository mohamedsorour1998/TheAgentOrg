"""Contract tests: every agent result matches the frozen state.py schema and is
sane, and malformed data is rejected. Owner: Reem.

These assert on the frozen contract + fixtures, never on internals, so they keep
passing as each lane's real code lands. Run:

    pytest -q tests/test_functional_contract.py

WHY THE ASSERTIONS LOOK LIKE THIS
    `fixtures_loader` already calls `model_validate`, so it raises before a test
    body ever runs. That makes `isinstance(plan, PlanResult)` and
    `review.verdict in ("approve", "changes_requested")` free passes -- the
    Literal enforced the second one and the loader enforced the first. Neither
    can go red, so neither pins anything.

    So every check below is on a VALUE or a CROSS-FIELD AGREEMENT that would
    actually differ if a lane drifted: files_changed measured against the diff
    that supposedly changed them, the stored security verdict recomputed from
    the findings via compute_security_verdict, slo_checks read against the
    verdict they are meant to justify. Each of these has been confirmed to fail
    when the thing it targets is broken.
"""

import re

import pytest
from pydantic import ValidationError

from agentorg import fixtures_loader
from agentorg.state import (
    SEVERITY_ORDER,
    Finding,
    ReviewResult,
    compute_security_verdict,
)

# Mirrors the two rules in agentorg/security/gitleaks.toml. Matched as patterns
# rather than written out as literals so this file carries no credential-shaped
# string of its own, and so it pins the shape the scanner keys on.
_AWS_KEY_ID = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_AWS_SECRET = re.compile(r"""AWS_SECRET_ACCESS_KEY\s*=\s*["'][A-Za-z0-9/+=]{40}["']""")


def _added_lines(diff: str) -> list[str]:
    """The lines a diff ADDS, without the leading '+'.

    Added lines only, never the whole diff text: a removed line and a `+++`
    header both contain their file's text, so scanning the raw diff would report
    a credential that the change actually deletes.
    """
    return [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def test_plan_result_carries_usable_tasks_and_criteria():
    plan = fixtures_loader.plan()
    # list[str] admits [] and [""], so emptiness is what is worth asserting.
    assert plan.tasks, "planner must emit at least one task"
    assert plan.acceptance_criteria, "planner must emit acceptance criteria"
    assert plan.target_files, "planner must name the files to change"
    assert all(t.strip() for t in plan.tasks), "no task may be blank"
    assert all(c.strip() for c in plan.acceptance_criteria), "no criterion may be blank"
    assert all(f.strip() for f in plan.target_files), "no target file may be blank"


def test_clean_dev_result_is_a_real_unified_diff():
    dev = fixtures_loader.dev(poisoned=False)
    assert dev.branch.strip(), "dev result must name a branch"
    assert dev.summary.strip(), "dev result must explain what it did"
    assert dev.files_changed, "dev result must list changed files"

    # Shape, not just non-emptiness: github_ops applies this string with git.
    assert "--- a/" in dev.diff, "diff must carry a unified-diff source header"
    assert "+++ b/" in dev.diff, "diff must carry a unified-diff target header"
    assert "@@" in dev.diff, "diff must carry at least one hunk header"
    assert _added_lines(dev.diff), "a dev diff that adds nothing changes nothing"

    # The claim with teeth: files_changed must describe THIS diff. A stale
    # files_changed sends the reviewer and the scanners at the wrong file.
    for path in dev.files_changed:
        assert f"+++ b/{path}" in dev.diff, f"{path} is listed but not in the diff"


def test_the_dev_fixtures_differ_by_exactly_the_planted_credentials():
    """The whole block demo rests on this difference. Nothing else pins it.

    If the poisoned fixture ever loses its credentials, every downstream
    scanner assertion still passes -- on a diff with nothing to find. That is
    the one drift that leaves CI green and takes the demo down.
    """
    poisoned = _added_lines(fixtures_loader.dev(poisoned=True).diff)
    clean = _added_lines(fixtures_loader.dev(poisoned=False).diff)

    assert any(_AWS_KEY_ID.search(line) for line in poisoned), (
        "the poisoned diff must ADD an AWS access key id for gitleaks to find"
    )
    assert any(_AWS_SECRET.search(line) for line in poisoned), (
        "the poisoned diff must ADD an AWS secret access key"
    )
    # The negative control. Without it a fixture that planted the key in BOTH
    # diffs would pass the two assertions above and destroy the contrast.
    assert not any(_AWS_KEY_ID.search(line) for line in clean), (
        "the clean diff must not add an AWS access key id"
    )
    assert not any(_AWS_SECRET.search(line) for line in clean), (
        "the clean diff must not add an AWS secret access key"
    )


def test_review_result_is_internally_coherent():
    review = fixtures_loader.review()
    # The Literal already rejects anything else, so assert the VALUE this
    # fixture is supposed to carry -- that can go red, the membership cannot.
    assert review.verdict == "approve"
    # Cross-field: state.py cannot express "approve implies nothing to fix",
    # but a reviewer emitting both has contradicted itself.
    assert not review.must_fix, "an approving review cannot also demand fixes"
    for comment in review.comments:
        assert comment.line > 0, "a review comment must point at a real line"
        assert comment.file.strip(), "a review comment must name a file"
        assert comment.note.strip(), "a review comment must say something"


def test_security_block_fixture_agrees_with_the_deterministic_rule():
    """Recompute the verdict from the findings and compare. This is the test
    that would catch a fixture whose stored verdict drifted from its findings.
    """
    sec = fixtures_loader.security(block=True)
    verdict, blocking = compute_security_verdict(sec.findings, threshold="high")

    assert verdict == sec.verdict == "block"
    assert len(blocking) == len(sec.blocking) == 2
    assert blocking == sec.blocking, "stored blocking must be what the rule computes"
    assert {f.rule for f in sec.blocking} == {
        "aws-access-key-id",
        "aws-secret-access-key",
    }
    assert all(f.severity == "critical" for f in sec.blocking)
    # blocking is a filter of findings, so it cannot contain anything new.
    assert all(f in sec.findings for f in sec.blocking)


def test_security_pass_fixture_has_findings_below_the_threshold():
    """A pass fixture with NO findings would pass trivially and prove nothing
    about the threshold. This asserts it has findings that are merely too low.
    """
    sec = fixtures_loader.security(block=False)
    verdict, blocking = compute_security_verdict(sec.findings, threshold="high")

    assert verdict == sec.verdict == "pass"
    assert blocking == sec.blocking == []
    assert sec.findings, "the pass fixture must carry findings, or it tests nothing"
    assert all(
        SEVERITY_ORDER[f.severity] < SEVERITY_ORDER["high"] for f in sec.findings
    ), "a passing result cannot hold a finding at or above the block threshold"


def test_sre_result_slo_checks_agree_with_the_verdict():
    sre = fixtures_loader.sre()
    assert sre.verdict == "go"
    assert sre.ci_status == "passing"
    assert sre.slo_checks, "SRE result must carry at least one SLO check"
    assert all(c.name.strip() for c in sre.slo_checks), "every SLO check needs a name"
    # Cross-field again: "go" on top of a failed check is the incoherent state.
    assert all(c.passed for c in sre.slo_checks), "a 'go' cannot sit on a failed check"


def test_malformed_review_verdict_is_rejected():
    # 'approved' is NOT a ReviewResult verdict (approve / changes_requested).
    # This proves the contract test catches drift rather than rubber-stamping.
    with pytest.raises(ValidationError):
        ReviewResult.model_validate({"verdict": "approved"})


def test_malformed_finding_missing_line_is_rejected():
    # Finding.line is required; dropping it must fail validation.
    with pytest.raises(ValidationError):
        Finding.model_validate(
            {
                "tool": "gitleaks",
                "severity": "critical",
                "rule": "aws-access-key-id",
                "file": "app/auth.py",
                "description": "missing the required line field",
            }
        )
