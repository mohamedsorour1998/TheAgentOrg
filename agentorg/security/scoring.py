"""ONE scoring policy for all three scanners, and the audit trail it produces.

OWNER: Lane C (final phase). Spec: docs/final/01-specification.md §8.

WHY THIS FILE EXISTS -- A JUDGE DOUBTED THE CENTRAL CLAIM, AND WAS RIGHT TO

    The claim is "a fixed severity threshold decides -- that decision is
    arithmetic". Measured on the pre-final baseline, that sentence was exactly
    true for two scanners and VACUOUSLY true for the third:

      * trivy   mapped trivy's own severity through a private table, failing
                closed on an unrecognised value.
      * semgrep mapped semgrep's own severity through a second private table.
                Its table once defaulted to "low", so a rule semgrep marked
                CRITICAL scored 0 against a cutoff of 2 and COULD NOT BLOCK A
                CHANGE. No test read the function.
      * gitleaks hardcoded `severity="critical"` at the Finding constructor.

    Three tables in three files, one of which was not a table at all. The
    threshold never discriminates a gitleaks finding, because every gitleaks
    finding is already at the top of the scale. That is DEFENSIBLE -- a leaked
    credential is not a "medium" -- but it is not what the claim sounds like, and
    a reader of the code notices. Papering over it would cost more than the
    finding.

    So the constant STAYS and this file makes it a POLICY the code states rather
    than a literal the code merely performs. `POLICY["gitleaks"]` carries the
    rule, the rationale, and the fact that gitleaks emits no severity of its own
    -- and `gitleaks_tool.py` now asks for it by name instead of typing
    "critical" into a constructor.

    NO RUN'S VERDICT CHANGES BECAUSE OF THIS FILE. Every mapping below is
    byte-for-byte the behaviour the two private tables already had, and the
    existing suite -- tests/test_scanner_correctness.py drives both
    `_map_severity` functions directly, 9 call sites -- is the regression net
    that proves it.

THIS MODULE NEVER COMPARES A SEVERITY TO A THRESHOLD, AND THAT IS THE DESIGN

    `score_findings` reaches every `blocking` flag through
    `state.compute_security_verdict`, the same five lines the pipeline's verdict
    comes from. It does not read `SEVERITY_ORDER` and it does not write `>=`.

    The alternative -- comparing here, since the comparison is three characters
    -- would create a SECOND decision path whose whole job is to agree with the
    first. The audit table would then be evidence about itself rather than about
    the verdict, and the two could drift while every test in this file stayed
    green, because both halves would be reading the same table. An audit artifact
    that can disagree with the decision it describes is worse than none: it reads
    as proof.

    Pinned over the AST by tests/test_scoring.py, not over the source text --
    a paragraph like this one satisfies a substring check, and this repository has
    already shipped two tests that were satisfied by the comment explaining the
    thing they were checking.
"""

import types
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import get_args

from ..common import config
from ..state import (
    SEVERITY_ORDER,
    Finding,
    ScoreRow,
    Severity,
    compute_security_verdict,
)
from ._run import ScannerTool

# WHAT AN UNRECOGNISED SEVERITY BECOMES. One value, shared by every scanner.
#
# `high` is the DEFAULT block threshold, so an unrecognised severity blocks.
# That direction is the whole point: an unknown value means a scanner said
# something no table here has seen, and a name this project does not recognise is
# not evidence of safety. Defaulting low means a future severity name silently
# stops blocking; defaulting high means it blocks loudly and somebody fixes the
# table. Only one of those is safe to be wrong about, and this project's
# signature defect is the other one.
#
# DELIBERATELY NOT `critical`, for the reason `_run.error_finding` gives about
# its own severity: `critical` is what a committed credential reports, and an
# unrecognised severity must not impersonate a discovered secret in a list a
# human is reading.
#
# ONE CONSTANT, NOT ONE PER SCANNER. A per-scanner default is a per-scanner
# opportunity to drift downwards, and the drift would be invisible -- the two
# private tables that preceded this file held the same `or "low"` default and
# only ONE of them was ever measured.
FAIL_CLOSED_SEVERITY: Severity = "high"

# `ScoreRow.native` for a scanner that emits NO severity of its own. gitleaks is
# the only one, and `""` is what state.ScoreRow's docstring reserves for it.
NATIVE_NONE = ""

# `ScoreRow.native` for a scanner that DOES emit a severity which this row could
# not recover. Distinct from NATIVE_NONE on purpose: "the scanner has nothing to
# say about severity" and "the scanner said something and we lost it" are two
# different facts, and giving them one spelling would make a gap in the artifact
# read as data about gitleaks.
#
# WHY A ROW LIKE THAT EXISTS AT ALL -- A REAL CONTRACT GAP, NAMED RATHER THAN
# HIDDEN. `state.Finding` carries our MAPPED severity and not the scanner's own
# word, and `security/__init__.run_all_scanners` concatenates, sorts and
# memoises `list[Finding]`. So a row built downstream of the fan-out -- which is
# where `agents/security.py` builds one -- cannot know that trivy said `HIGH`
# rather than semgrep saying `ERROR`; both arrive as `high`. The mapping is
# many-to-one and NOT invertible, so there is nothing to recover.
#
# The honest options were: fabricate a plausible native (rejected -- a fabricated
# field in an artifact whose whole purpose is auditability is the worst thing in
# this file's problem space), omit the row (rejected -- the finding still fed the
# verdict, so the audit would be short a line), or say so. This says so, and it
# renders as a gap.
#
# CLOSING IT IS A CONTRACT CHANGE, NOT A LANE C CHANGE: `Finding` would need an
# optional `native_severity: str = ""`, which is `state.py` and therefore the
# integrator's, batched the way the four previous optional fields were. Until
# then `score_findings(natives=...)` accepts the map from any caller that HAS it.
#
# The angle brackets follow `_run._SCANNER_PSEUDO_FILE`'s `<{tool} scanner>`
# convention, and they are what makes the sentinel uncollidable: scanner
# severities are bare uppercase words. `test_the_unrecorded_sentinel_cannot_be
# _mistaken_for_a_scanner_word` checks that against the tables rather than
# trusting it.
NATIVE_UNRECORDED = "<not recorded>"


@dataclass(frozen=True)
class ScannerScoring:
    """How ONE scanner's severity becomes one of ours. A table OR a constant.

    EXACTLY ONE of `table` and `constant` is set, and `__post_init__` refuses
    anything else. That exclusivity is the honest shape of the problem: two of
    the three scanners emit a severity and are MAPPED; one emits none and is
    ASSIGNED one by rule. A single field holding both cases would let a future
    editor give gitleaks a table while leaving the constant in place, at which
    point two answers exist and nothing says which one the verdict used.

    `constant is None` is therefore the same fact as "this scanner emits a
    severity of its own", which is why `emits_native_severity` is a property
    derived from it and not a third field agreeing with the other two.

    `rationale` is on the object rather than in a comment because it is rendered:
    the scoring table a judge reads gets its gitleaks line from here. A policy
    whose justification lives only in a comment is a policy nobody outside this
    file can quote.
    """

    tool: ScannerTool
    rationale: str
    table: Mapping[str, Severity] = field(default_factory=dict)
    constant: Severity | None = None
    # Whether this scanner is what stands between a credential and `main`.
    #
    # This is what `THRESHOLD_FLOOR` is derived from, and it is an explicit flag
    # rather than an inference from `constant is not None`. "It has a constant,
    # so it must be the secret scanner" is true today and is not a property of
    # anything -- a future scanner could be assigned a constant `medium` for an
    # unrelated reason and would silently become the thing the floor protects.
    protects_core_guarantee: bool = False

    def __post_init__(self) -> None:
        if (self.constant is None) == (not self.table):
            raise ValueError(
                f"{self.tool}: exactly one of `table` and `constant` must be set. "
                f"A scanner either emits a severity this project maps, or emits "
                f"none and is assigned one by policy. Both set means two answers "
                f"exist and nothing records which one a verdict used."
            )
        for native, mapped in self.table.items():
            if mapped not in SEVERITY_ORDER:
                raise ValueError(
                    f"{self.tool}: {native!r} maps to {mapped!r}, which is not a "
                    f"severity; expected one of {', '.join(SEVERITY_ORDER)}"
                )
        if self.constant is not None and self.constant not in SEVERITY_ORDER:
            raise ValueError(
                f"{self.tool}: the policy severity {self.constant!r} is not a "
                f"severity; expected one of {', '.join(SEVERITY_ORDER)}"
            )
        if self.protects_core_guarantee and self.constant is None:
            raise ValueError(
                f"{self.tool}: a scanner that protects the core guarantee must "
                f"carry a policy `constant`. The floor in `THRESHOLD_FLOOR` is "
                f"derived from that value, and a mapped scanner has no single "
                f"severity to derive it from."
            )

    @property
    def emits_native_severity(self) -> bool:
        """True when the scanner has a severity of its own. Derived, not stored."""
        return self.constant is None


# THE ONE TABLE. Every scanner, native -> ours.
#
# MappingProxyType, not dict: this is read by the renderer, by the wrappers and
# by tests, and the fan-out's `_copy` exists because one caller mutating a shared
# object is a fail-open with no scanner involved. A table a caller can edit in
# place is the same hazard one level up.
POLICY: Mapping[ScannerTool, ScannerScoring] = types.MappingProxyType({
    # semgrep emits BOTH vocabularies depending on how a rule declares its
    # metadata, which is why seven keys map to four severities. Not symmetric
    # with trivy's and it must not be "harmonised" into one table: semgrep's
    # ERROR is its top level and means `high`, while trivy spells its top level
    # CRITICAL.
    "semgrep": ScannerScoring(
        tool="semgrep",
        rationale=(
            "semgrep's own severity, mapped. It emits INFO/WARNING/ERROR from "
            "classic rules and LOW/MEDIUM/HIGH/CRITICAL from new-style rule "
            "metadata, so both vocabularies are listed."
        ),
        table=types.MappingProxyType({
            "INFO": "low",
            "LOW": "low",
            "WARNING": "medium",
            "MEDIUM": "medium",
            "ERROR": "high",
            "HIGH": "high",
            "CRITICAL": "critical",
        }),
    ),
    # trivy's vocabulary is complete here -- UNKNOWN/LOW/MEDIUM/HIGH/CRITICAL is
    # all of it -- so the fail-closed default is LATENT for this scanner rather
    # than live. It is still the default, because "the vocabulary is complete" is
    # a claim about the version installed today.
    #
    # trivy's own UNKNOWN is a MAPPED KEY, not a fall-through, and the difference
    # matters: trivy uses UNKNOWN for a CVE whose severity its data sources do
    # not carry, which is a real answer about a real finding and stays `low`. The
    # fail-closed default is for a value that is not in trivy's vocabulary at all
    # -- a new severity name, or an absent field arriving as "". Those are not
    # trivy saying "unknown"; they are this table not recognising what trivy said.
    "trivy": ScannerScoring(
        tool="trivy",
        rationale=(
            "trivy's own severity, mapped. Its UNKNOWN is a real answer about a "
            "real CVE whose data sources carry no severity, so it is a mapped key "
            "and not a fall-through."
        ),
        table=types.MappingProxyType({
            "UNKNOWN": "low",
            "LOW": "low",
            "MEDIUM": "medium",
            "HIGH": "high",
            "CRITICAL": "critical",
        }),
    ),
    # THE CONSTANT, STATED AS POLICY. gitleaks reports no severity field at all:
    # its JSON carries RuleID, File, StartLine, Description and an entropy score,
    # and nothing that ranks the hit. So there is nothing to map, and a severity
    # has to come from somewhere. This project's answer is a rule, not a
    # measurement: ANY finding from a secret scanner is `critical`.
    #
    # WHY THAT IS RIGHT AND NOT LAZY. A committed credential has no lesser
    # grade -- it is either in the change or it is not, and if it is, rotating it
    # is the only remedy. The alternatives were considered and rejected: ranking
    # by entropy scores a short high-entropy token below a long structured one;
    # ranking by rule id means maintaining a severity per gitleaks rule and
    # answering "which credentials are we willing to merge?"; and gitleaks'
    # `--redact`-era verification status is not in this report shape.
    #
    # THE HONEST CONSEQUENCE, SAID OUT LOUD: the block threshold does not
    # DISCRIMINATE among gitleaks findings. All of them are at the top of the
    # scale, so the arithmetic for gitleaks is `critical >= threshold`, which is
    # true for every threshold this project accepts. The threshold still runs --
    # it is the same comparison, over the same rule -- it simply has one input to
    # compare. Anyone told "a fixed threshold decides" should also be told that,
    # which is what `rationale` is for.
    "gitleaks": ScannerScoring(
        tool="gitleaks",
        rationale=(
            "POLICY, not a mapping: gitleaks reports no severity field, and any "
            "finding from a secret scanner is critical by rule. A committed "
            "credential has no lesser grade, so the threshold has one input to "
            "compare rather than four -- it still runs, it does not discriminate."
        ),
        constant="critical",
        protects_core_guarantee=True,
    ),
})


def _missing_policies() -> list[str]:
    """Which `ScannerTool` members have no entry above. Derived from the Literal.

    `ScannerTool` already exists to make a mistyped tool an authoring-time error,
    so it is the tool list -- the same reasoning `security/__init__._fault_rules`
    records for building its set off `get_args(ScannerTool)` rather than a tuple
    written next to it. A fourth scanner added to that Literal and forgotten here
    would otherwise reach `map_severity`'s KeyError at scan time.

    `get_args`, not `.__args__`, for the same reason that module uses it: the
    dunder is an implementation detail of `typing` and the public function is what
    a `Literal` is documented to answer.
    """
    return [tool for tool in get_args(ScannerTool) if tool not in POLICY]


if _missing_policies():
    raise ValueError(
        f"no scoring policy for {', '.join(_missing_policies())}. Every scanner "
        f"in `_run.ScannerTool` needs one: a scanner with no policy has no "
        f"severity, and a finding with no severity cannot reach the block rule."
    )

# THE FLOOR ON A CONFIGURED THRESHOLD -- see `resolve_threshold`.
#
# DERIVED from the policy that protects the core guarantee, never written down.
# A literal `"critical"` here would be a second declaration of gitleaks' policy
# severity, and the two copies would keep agreeing while one of them moved --
# which is how the semgrep table's default survived being wrong.
THRESHOLD_FLOOR: Severity = min(
    (policy.constant for policy in POLICY.values() if policy.protects_core_guarantee),
    key=lambda severity: SEVERITY_ORDER[severity],
)

# The fail-closed default has to actually FAIL CLOSED at the shipped threshold,
# and that is a relationship between two constants in two files -- so it is
# checked at import, next to them, rather than assumed. Raise `FAIL_CLOSED_
# SEVERITY` to `critical` or lower `SECURITY_BLOCK_THRESHOLD`'s default and this
# stays quiet; drop the default to `medium` and it refuses at import instead of
# letting an unrecognised severity through at scan time.
if SEVERITY_ORDER[FAIL_CLOSED_SEVERITY] < SEVERITY_ORDER["high"]:
    raise ValueError(
        f"FAIL_CLOSED_SEVERITY={FAIL_CLOSED_SEVERITY!r} is below `high`, the "
        f"default block threshold, so an unrecognised scanner severity would NOT "
        f"block. That is the exact defect measured in semgrep's table on "
        f"2026-08-22: rules marked CRITICAL scored 0 against a cutoff of 2."
    )


def map_severity(tool: str, native: str | None) -> Severity:
    """A scanner's own severity word -> ours. FAILS CLOSED on the unrecognised.

    The one entry point for every scanner. `gitleaks` ignores its argument and
    answers with its policy constant, because gitleaks emits no severity -- see
    `POLICY["gitleaks"]`. The argument is still accepted so callers do not need
    to know which kind of scanner they hold.

    Case-insensitive, and `.upper()` is load-bearing rather than tidiness:
    semgrep 1.x sends `error` as well as `ERROR` from new-style rule metadata,
    and a table keyed on upper-case names with no normalisation sends every one of
    those to the fail-closed default. The symptom would be the CLEAN demo run
    blocking on our own INFO-severity rule -- the safe direction, and still a
    broken demo.

    `None` and `""` take the same route as an unknown name, deliberately. The
    wrappers read this field through `report_text(..., "")`, so a report whose
    `extra` omits `severity` -- the shape of a truncated report -- arrives here as
    the empty string. A report that does not say how bad a finding is has not
    said it is harmless.
    """
    policy = POLICY.get(tool)
    if policy is None:
        raise KeyError(
            f"no scoring policy for tool {tool!r}; known scanners are "
            f"{', '.join(sorted(POLICY))}"
        )
    if policy.constant is not None:
        return policy.constant
    return policy.table.get((native or "").upper(), FAIL_CLOSED_SEVERITY)


def policy_severity(tool: str) -> Severity:
    """The severity a scanner that emits NONE is assigned by rule.

    A separate name from `map_severity` on purpose, and `gitleaks_tool.py` calls
    THIS one. Both return `critical` for gitleaks, so the distinction buys
    nothing at runtime -- it buys the reader the difference between "we mapped
    what the scanner told us" and "the scanner told us nothing and this project
    has a rule". Where the constructor used to read `severity="critical"`, which
    states neither, it now names the rule.

    Raises for a scanner that DOES emit a severity, rather than answering with
    the fail-closed default: a caller reaching for a policy constant that does
    not exist has misunderstood which kind of scanner it holds, and quietly
    handing back `high` would discard the scanner's own answer on every finding.
    """
    policy = POLICY.get(tool)
    if policy is None:
        raise KeyError(
            f"no scoring policy for tool {tool!r}; known scanners are "
            f"{', '.join(sorted(POLICY))}"
        )
    if policy.constant is None:
        raise ValueError(
            f"{tool} emits its own severity, so it has no policy constant -- map "
            f"the scanner's value with `map_severity({tool!r}, native)` instead. "
            f"Answering with a default here would discard what the scanner said."
        )
    return policy.constant


def resolve_threshold(requested: str | None = None) -> Severity:
    """The threshold in force, REFUSING one that would let a secret through.

    `None` or `""` means "nobody asked", so `config.SECURITY_BLOCK_THRESHOLD`
    decides -- the same `None`-means-nobody-said convention `developer.run`'s
    `poisoned` argument uses, and for the same reason: `""` from an empty
    per-project column must not read as a deliberate choice.

    TWO REFUSALS, AND THE SECOND IS THE POINT.

    A value outside the vocabulary raises `ValueError` HERE rather than
    `KeyError` inside the block rule. That failure is measured, not hypothetical:
    `compute_security_verdict([], threshold="HIGH")` used to raise `KeyError:
    'HIGH'` from inside the security agent -- the one stage whose whole purpose is
    to produce a verdict, dying while producing one, with a traceback naming a
    dict lookup rather than a misconfigured knob. `config` closed that for the
    ENVIRONMENT variable at import; per-project thresholds arrive at RUN time
    from a database column, where an import-time check cannot see them, so the
    same refusal has to exist on this path.

    A value ABOVE `THRESHOLD_FLOOR` is refused because it would stop secrets
    blocking, and a knob that can disable the product's core guarantee is a
    defect rather than a feature.

    THE FLOOR DOES NOT BIND TODAY, AND SAYING SO IS THE HONEST PART. It is
    derived from gitleaks' policy severity, which is `critical`, the top of the
    scale -- so all four legal thresholds pass, and a reader who assumed this
    check refuses something today would be wrong. It is not decoration either: it
    binds the moment gitleaks' constant is lowered, which is the realistic way
    this guarantee gets lost. `test_the_threshold_floor_binds_when_the_secret_
    policy_is_lowered` proves that by lowering the constant and watching a
    previously legal threshold be refused -- a floor that cannot be observed to
    refuse anything is exactly the vacuous check this file exists to stop
    shipping.

    REFUSES, NEVER CLAMPS. Clamping runs the gate at a threshold the operator did
    not ask for and reports success, which is the same shape as `STATE_BACKEND`
    falling back to `local` on a typo: the run looks configured and is not.
    """
    threshold = requested or config.SECURITY_BLOCK_THRESHOLD
    if threshold not in SEVERITY_ORDER:
        raise ValueError(
            f"threshold {threshold!r} is not a severity; expected one of "
            f"{', '.join(SEVERITY_ORDER)}. Refused here rather than raising "
            f"KeyError from inside the block rule, which is where an unvalidated "
            f"value landed before: the security stage died while producing a "
            f"verdict and the traceback named a dict lookup."
        )
    if SEVERITY_ORDER[threshold] > SEVERITY_ORDER[THRESHOLD_FLOOR]:
        raise ValueError(
            f"threshold {threshold!r} is above {THRESHOLD_FLOOR!r}, the severity "
            f"this project assigns every secret-scanner finding, so a committed "
            f"credential would no longer block. A configurable threshold may not "
            f"be set high enough to disable the guarantee the product is for."
        )
    return threshold


def score_findings(
    findings: list[Finding],
    threshold: str | None = None,
    natives: Mapping[str, str] | None = None,
) -> list[ScoreRow]:
    """One audit row per finding: native severity, ours, the cutoff, the outcome.

    THE JUDGES' QUESTION, ANSWERED AS DATA. Asked at the pre-final: "gitleaks and
    trivy -- how do we score the response so we know it is go or no-go, as you
    claimed it is deterministic". The verdict was already deterministic; what was
    missing was the ability to SHOW the arithmetic.

    `blocking` COMES FROM `compute_security_verdict`, NOT FROM A COMPARISON HERE.
    One call per finding against the same five lines the pipeline's verdict comes
    from, so the rows cannot disagree with the decision they describe. See the
    module docstring for why a local `>=` would be worse than the extra calls: an
    audit artifact derived from a second implementation is evidence about that
    implementation.

    `natives` maps a finding's `rule` to the scanner's own severity word, for the
    callers that have it. Absent, a mapped scanner's row records
    `NATIVE_UNRECORDED` -- `state.Finding` does not carry the native word and the
    mapping is many-to-one, so there is nothing to recover and nothing is
    invented. A scanner that emits no severity records `NATIVE_NONE`, which is a
    different fact and reads differently in the rendered table.

    Keyed on `rule` rather than on the whole finding because that is the field a
    caller holding a raw scanner report can key by, and because `Finding` is not
    hashable. Two findings of the same rule share a native severity in every
    report shape the three scanners produce.
    """
    cutoff = resolve_threshold(threshold)
    rows: list[ScoreRow] = []
    for finding in findings:
        policy = POLICY.get(finding.tool)
        if policy is None:
            raise KeyError(
                f"finding from tool {finding.tool!r} has no scoring policy; known "
                f"scanners are {', '.join(sorted(POLICY))}"
            )
        if policy.emits_native_severity:
            native = (natives or {}).get(finding.rule, NATIVE_UNRECORDED)
        else:
            native = NATIVE_NONE
        # THE ONE COMPARISON, borrowed rather than repeated. A single-finding call
        # asks the shipped rule whether THIS severity clears THIS cutoff.
        verdict, _ = compute_security_verdict([finding], threshold=cutoff)
        rows.append(
            ScoreRow(
                tool=finding.tool,
                rule=finding.rule,
                native=native,
                mapped=finding.severity,
                threshold=cutoff,
                blocking=verdict == "block",
            )
        )
    return rows


def render_scoring_table(rows: list[ScoreRow]) -> list[str]:
    """The scoring rows as markdown lines, for the PR comment and the UI.

    Returns LINES rather than one string, because every renderer in
    `github_ops.py` composes a list and joins once -- handing back a blob would
    make this the one section a caller has to splice differently.

    An EMPTY rows list renders the sentence rather than an empty table, and that
    is not cosmetic: `compute_security_verdict([]) == ("pass", [])`, so "nothing
    was found" and "nothing was scored" produce the same silence, and a bare
    heading over no rows reads as a scoring step that did not run. The threshold
    cannot be stated in that case -- there is no row to read it off -- so the line
    says what happened and claims nothing else.

    The gitleaks rationale is printed under the table when a gitleaks row is
    present. That is the sentence a reader needs to not be misled by a column of
    `critical`s: without it, "the threshold decided" and "every value in this
    column is the maximum" sit next to each other unexplained.
    """
    if not rows:
        return ["_no findings were scored, so there is no arithmetic to show._"]

    lines = [
        # PARENTHESISED, and not because ruff asked. ISC004 fires on an
        # unparenthesized implicit concatenation inside a collection, and its own
        # hint names the other reading: "Did you forget a comma?". That ambiguity
        # is the point -- one missing comma turns a heading into two table rows,
        # which is a rendered defect no assertion about the table's CONTENT would
        # see. The parentheses say "one line" out loud.
        (
            f"**Scoring** — threshold `{rows[0].threshold}`, "
            f"{sum(1 for row in rows if row.blocking)} of {len(rows)} at or above it"
        ),
        "",
        "| tool | rule | native | mapped | blocking |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        native = row.native if row.native else "—"
        lines.append(
            f"| `{row.tool}` | `{row.rule}` | {native} | "
            f"`{row.mapped}` | {'**yes**' if row.blocking else 'no'} |"
        )

    tools_present = {row.tool for row in rows}
    for tool in sorted(tools_present):
        policy = POLICY[tool]
        if not policy.emits_native_severity:
            lines += ["", f"_`{tool}`: {policy.rationale}_"]
    return lines
