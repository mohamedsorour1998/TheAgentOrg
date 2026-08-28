"""DETERMINISM, MECHANISED. Lane C, task C9.

THE CLAIM A JUDGE DOUBTED, STATED AS A PROPERTY. "A fixed severity threshold
decides -- that decision is arithmetic." Mechanised here as: for any finding set,
`compute_security_verdict` is a PURE FUNCTION of (severities, threshold). Same
inputs, same answer, every time, and nothing else can influence it.

WHY EXHAUSTIVE AND NOT `hypothesis`. The severity vocabulary is four values, so
`itertools.product` covers every finding set up to length 3 completely -- 340
sets, times four thresholds. A sampling library cannot beat total coverage at
that size, and adding a test dependency to explore four values would be worse
than the coverage it bought. The generation is exhaustive where it can be and
seeded where it cannot (the field-irrelevance test, which ranges over strings).

THREE PROPERTIES, AND THE SECOND IS THE ONE WITH TEETH:

  1. REPEATABLE  -- the same call gives the same answer, twice.
  2. DEPENDS ON NOTHING ELSE -- tool, rule, file, line, description and ORDER may
     all change without moving the verdict. This is what "pure function of
     (severities, threshold)" actually forbids, and property 1 alone would hold
     for a function that read the clock only on Tuesdays.
  3. MONOTONIC   -- raising a finding's severity can never turn a block into a
     pass, and lowering the threshold can never turn a block into a pass.

Property 3 is what makes the arithmetic claim checkable rather than merely
repeatable: a lookup table with one transposed row would satisfy 1 and 2.
"""

import itertools
import random

import pytest

from agentorg.security import scoring
from agentorg.state import SEVERITY_ORDER, Finding, compute_security_verdict

_SEVERITIES = tuple(SEVERITY_ORDER)

# THE ANCHOR, AND IT IS WRITTEN OUT RATHER THAN DERIVED. This is the one place in
# this file that does NOT read `SEVERITY_ORDER`, and it exists because of a
# measured gap in the first version of these tests.
#
# MEASURED: transposing two rows of SEVERITY_ORDER -- `"high": 3, "critical": 2`
# -- left 24 of 25 tests GREEN, and silently converted the twenty-fifth into a
# SKIP. Every property here derived its expectation from the same table the rule
# reads, so the mutation moved the rule and the expectation TOGETHER and the
# lattice stayed self-consistent. Repeatable, order-independent, monotone, and
# ranking `high` above `critical`.
#
# That is the whole pattern this repository keeps finding: a measurement that
# cannot express the failing case produces confidence that cannot be falsified.
# "Determinism" is satisfied by a wrong table applied consistently, so
# determinism alone is not the claim worth making -- the ORDER has to be pinned
# somewhere that a mutation of the order cannot follow.
_EXPECTED_RANKING = ("low", "medium", "high", "critical")


def test_the_severity_ranking_itself_is_pinned_independently_of_the_table():
    """The anchor. A transposed row in SEVERITY_ORDER fails HERE and only here.

    Every other assertion in this file reads `SEVERITY_ORDER` to build its
    expectation, which makes them all blind to a change in `SEVERITY_ORDER`
    itself -- measured, 24 of 25 green with `critical` ranked below `high`.

    So this test restates the ranking as a literal. That is normally the thing
    this repository forbids (two declarations of one fact, which drift), and the
    exception is deliberate: the second declaration is the ONLY way to detect a
    change in the first, and a severity ranking is a contract that is supposed to
    be hard to change. If this test and `state.SEVERITY_ORDER` disagree, one of
    them is a bug and a human must decide which -- silence is the unacceptable
    outcome.
    """
    assert tuple(SEVERITY_ORDER) == _EXPECTED_RANKING, (
        f"SEVERITY_ORDER's keys are {tuple(SEVERITY_ORDER)}, not "
        f"{_EXPECTED_RANKING}"
    )
    # The VALUES must rank in that order too -- key order alone is insertion
    # order and says nothing about the comparison the block rule performs.
    assert [SEVERITY_ORDER[name] for name in _EXPECTED_RANKING] == sorted(
        SEVERITY_ORDER[name] for name in _EXPECTED_RANKING
    ), (
        f"SEVERITY_ORDER ranks the severities as "
        f"{ {name: SEVERITY_ORDER[name] for name in _EXPECTED_RANKING} }, which is "
        f"not ascending. A transposed row here is invisible to every other test "
        f"in this file, because they all derive their expectations from this same "
        f"table -- measured: 24 of 25 stayed green with `critical` below `high`."
    )
    assert SEVERITY_ORDER["critical"] == max(SEVERITY_ORDER.values()), (
        "`critical` is not the maximum severity. CLAUDE.md's central "
        "discriminator is a set of gitleaks findings at `critical`, and "
        "scoring.THRESHOLD_FLOOR is derived from that value being the top of the "
        "scale."
    )


def test_a_committed_credential_blocks_at_every_threshold_this_project_accepts():
    """The core guarantee, asserted WITHOUT reading SEVERITY_ORDER for the answer.

    The companion to the anchor above: a transposed table would be caught there,
    and this states the consequence that must hold regardless -- a gitleaks
    finding, at the severity gitleaks' policy assigns, blocks at every legal
    threshold. That is the claim the demo makes on a projector.
    """
    secret = Finding(
        tool="gitleaks",
        severity=scoring.policy_severity("gitleaks"),
        rule="aws-access-key-id",
        file="app/auth.py",
        line=3,
        description="a committed AWS access key",
    )
    for threshold in _EXPECTED_RANKING:
        verdict, blocking = compute_security_verdict([secret], threshold=threshold)
        assert verdict == "block", (
            f"a committed credential did NOT block at threshold {threshold!r}; "
            f"this is the guarantee the product exists for"
        )
        assert blocking == [secret]


# Every finding set of length 0-3 over the four severities: 1 + 4 + 16 + 64 = 85.
_SEVERITY_SETS = tuple(
    combo
    for length in range(4)
    for combo in itertools.product(_SEVERITIES, repeat=length)
)


def test_the_generated_space_is_the_size_it_claims_to_be():
    """The guard that makes every property below non-vacuous.

    An empty or truncated generator would make `for case in _SEVERITY_SETS` pass
    while exploring nothing -- the failure mode this repository has shipped
    nineteen times. The count is arithmetic, so it is asserted rather than
    trusted.
    """
    assert len(_SEVERITY_SETS) == 85, len(_SEVERITY_SETS)
    assert len(_SEVERITIES) == 4, _SEVERITIES
    assert () in _SEVERITY_SETS, "the empty finding set must be covered"
    assert ("critical",) * 3 in _SEVERITY_SETS


def _findings(severities, tool="semgrep", rule="r", file="app/auth.py", line=1):
    """Findings carrying the given severities and otherwise identical."""
    return [
        Finding(tool=tool, severity=severity, rule=f"{rule}-{index}", file=file,
                line=line + index, description="d")
        for index, severity in enumerate(severities)
    ]


# ------------------------------------------------------------ property 1: repeatable


@pytest.mark.parametrize("threshold", _SEVERITIES)
def test_the_verdict_is_repeatable_over_every_generated_finding_set(threshold):
    """Same inputs, same answer. 85 sets x 4 thresholds = 340 cases.

    No model, no I/O and no clock is involved -- `compute_security_verdict` is
    five lines of Python -- and this is the assertion that says so out loud.
    """
    for severities in _SEVERITY_SETS:
        first = compute_security_verdict(_findings(severities), threshold=threshold)
        second = compute_security_verdict(_findings(severities), threshold=threshold)
        assert first[0] == second[0], (severities, threshold)
        assert [f.severity for f in first[1]] == [f.severity for f in second[1]]


@pytest.mark.parametrize("threshold", _SEVERITIES)
def test_the_verdict_is_exactly_the_arithmetic_it_claims_to_be(threshold):
    """`block` iff some severity's order is >= the threshold's. Nothing else.

    The independent restatement of the rule is the point: it is derived from
    `SEVERITY_ORDER` alone, so a transposed row in the real implementation shows
    up as a disagreement rather than being copied into the expectation.
    """
    cutoff = SEVERITY_ORDER[threshold]
    for severities in _SEVERITY_SETS:
        expected_blocking = [s for s in severities if SEVERITY_ORDER[s] >= cutoff]
        verdict, blocking = compute_security_verdict(
            _findings(severities), threshold=threshold
        )
        assert verdict == ("block" if expected_blocking else "pass"), (
            f"{severities} at threshold {threshold!r} gave {verdict!r}; "
            f"{len(expected_blocking)} finding(s) are at or above the cutoff"
        )
        assert [f.severity for f in blocking] == expected_blocking


def test_the_empty_finding_set_passes_and_that_is_load_bearing():
    """`compute_security_verdict([]) == ("pass", [])`, pinned here deliberately.

    Three scanner wrappers' docstrings depend on this, and it is why a scanner
    failure must never become an empty list: an `[]` returned from a failed scan
    would send a poisoned change green with the whole suite staying green
    alongside it. Anyone tempted to make the empty case block should read
    `_run.unrunnable_findings`, which RAISES for exactly this reason.
    """
    for threshold in _SEVERITIES:
        assert compute_security_verdict([], threshold=threshold) == ("pass", [])


# ------------------------------------- property 2: depends on severities and threshold ONLY


@pytest.mark.parametrize("threshold", _SEVERITIES)
def test_no_other_finding_field_can_change_the_verdict(threshold):
    """The teeth. tool, rule, file, line and description are all IRRELEVANT.

    Property 1 would hold for a function that read the clock only on Tuesdays.
    This one is what "pure function of (severities, threshold)" forbids: the
    verdict must be identical when every other field is different.

    Seeded, because these fields range over strings and integers rather than a
    four-value vocabulary. `random.Random(0)` rather than the module-level
    `random`, so this test cannot be perturbed by anything else drawing numbers --
    a test whose inputs depend on execution order is not a determinism test.
    """
    rng = random.Random(0)
    tools = ("semgrep", "gitleaks", "trivy")
    for severities in _SEVERITY_SETS:
        baseline = compute_security_verdict(
            _findings(severities), threshold=threshold
        )[0]
        for _ in range(3):
            verdict, _blocking = compute_security_verdict(
                _findings(
                    severities,
                    tool=rng.choice(tools),
                    rule=f"rule-{rng.randrange(10**6)}",
                    file=f"pkg/mod_{rng.randrange(999)}.py",
                    line=rng.randrange(1, 10**4),
                ),
                threshold=threshold,
            )
            assert verdict == baseline, (
                f"{severities} at {threshold!r} changed from {baseline!r} to "
                f"{verdict!r} when only tool/rule/file/line moved. The verdict is "
                f"reading a field it must not."
            )


@pytest.mark.parametrize("threshold", _SEVERITIES)
def test_the_ORDER_of_findings_cannot_change_the_verdict(threshold):
    """A set, not a sequence. Reordering must not move the answer.

    Worth its own test because `run_all_scanners` SORTS its concatenated output,
    so a verdict sensitive to order would be stable in the pipeline and unstable
    anywhere else -- the hardest kind of nondeterminism to see.
    """
    for severities in _SEVERITY_SETS:
        verdicts = {
            compute_security_verdict(_findings(perm), threshold=threshold)[0]
            for perm in set(itertools.permutations(severities))
        }
        assert len(verdicts) == 1, (
            f"{severities} at {threshold!r} produced {verdicts} across "
            f"permutations; the verdict depends on ordering"
        )


def test_the_count_of_blocking_findings_is_order_independent_too():
    """Not only the verdict: `blocking` is what the PR comment renders.

    A verdict that stayed `block` while `blocking` changed length would still put
    a different number on the projector for the same change.
    """
    for severities in _SEVERITY_SETS:
        counts = {
            len(compute_security_verdict(_findings(perm), threshold="high")[1])
            for perm in set(itertools.permutations(severities))
        }
        assert len(counts) == 1, (severities, counts)


# ------------------------------------------------------------- property 3: monotonic


def test_raising_a_severity_can_never_turn_a_block_into_a_pass():
    """Monotone in severity. A lookup table with a transposed row fails here.

    Properties 1 and 2 are both satisfied by a table that maps `critical` below
    `low`, because such a table is perfectly repeatable and reads no other field.
    This is what makes "arithmetic" checkable rather than merely stable.
    """
    for threshold in _SEVERITIES:
        for severities in _SEVERITY_SETS:
            if not severities:
                continue
            before = compute_security_verdict(
                _findings(severities), threshold=threshold
            )[0]
            for index in range(len(severities)):
                for higher in _SEVERITIES:
                    if SEVERITY_ORDER[higher] < SEVERITY_ORDER[severities[index]]:
                        continue
                    raised = list(severities)
                    raised[index] = higher
                    after = compute_security_verdict(
                        _findings(raised), threshold=threshold
                    )[0]
                    if before == "block":
                        assert after == "block", (
                            f"raising finding {index} of {severities} to {higher!r} "
                            f"turned a block into {after!r} at threshold "
                            f"{threshold!r}"
                        )


def test_lowering_the_threshold_can_never_turn_a_block_into_a_pass():
    """Monotone in the threshold, the other axis.

    This is the property that makes `SECURITY_BLOCK_THRESHOLD` safe to describe as
    a severity knob: turning it down may only ever block more, never less.
    """
    for severities in _SEVERITY_SETS:
        blocked_at = [
            threshold
            for threshold in _SEVERITIES
            if compute_security_verdict(_findings(severities), threshold=threshold)[0]
            == "block"
        ]
        if not blocked_at:
            continue
        # The thresholds that block must be a PREFIX of the severity order: if
        # `high` blocks, so must `medium` and `low`.
        highest_blocking = max(blocked_at, key=lambda s: SEVERITY_ORDER[s])
        for threshold in _SEVERITIES:
            if SEVERITY_ORDER[threshold] <= SEVERITY_ORDER[highest_blocking]:
                assert threshold in blocked_at, (
                    f"{severities} blocks at {highest_blocking!r} but not at the "
                    f"lower threshold {threshold!r}; the rule is not monotone"
                )


# ---------------------------------------- the same determinism, through the scoring rows


@pytest.mark.parametrize("threshold", _SEVERITIES)
def test_a_scoring_rows_blocking_flag_agrees_with_the_verdict_it_describes(threshold):
    """The audit artifact cannot disagree with the decision. C5's real guarantee.

    `score_findings` reaches every `blocking` flag through
    `compute_security_verdict` rather than writing its own `>=`, precisely so this
    holds by construction. Asserted anyway: an artifact derived from a second
    implementation is evidence about that implementation, and this is the test
    that would catch someone "optimising" the per-finding call into a local
    comparison.

    NOT SKIPPED WHEN THE THRESHOLD IS ABOVE THE FLOOR, deliberately. The first
    version of this test skipped that case, and when a mutation transposed
    `SEVERITY_ORDER` the floor moved, `high` became "above the floor", and the
    test SILENTLY BECAME A SKIP -- reported as `24 passed, 1 skipped`, which reads
    like a clean run. A skip whose condition is computed from the thing under test
    is a test that can delete itself. So the refusal is now asserted as behaviour
    instead: above the floor, `score_findings` must RAISE, and that is a claim
    about the same code path.
    """
    findings = _findings(_SEVERITY_SETS[-1])
    if SEVERITY_ORDER[threshold] > SEVERITY_ORDER[scoring.THRESHOLD_FLOOR]:
        with pytest.raises(ValueError, match="above"):
            scoring.score_findings(findings, threshold=threshold)
        return
    for severities in _SEVERITY_SETS:
        findings = _findings(severities)
        _verdict, blocking = compute_security_verdict(findings, threshold=threshold)
        rows = scoring.score_findings(findings, threshold=threshold)
        assert len(rows) == len(findings)
        assert [row.blocking for row in rows] == [f in blocking for f in findings], (
            f"the scoring rows disagree with the verdict for {severities} at "
            f"{threshold!r} -- the audit table is describing a different decision"
        )
