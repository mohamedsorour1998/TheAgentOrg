"""What DEGRADES when the model runs on your own compute. F2 and F6.

THIS MODULE IS THE DELIVERABLE, NOT THE BASE URL. Pointing `LLM_BASE_URL` at a
local gateway is one environment variable and proves nothing a judge should
accept. The question is what gets worse, and by how much, expressed as numbers
somebody can re-measure.

WHICH NUMBERS, AND WHY THESE. Each column is a fact the pipeline already records
for its own reasons, so none is instrumented specially for this table -- a
measurement built only to be reported is one nobody can check against a run:

  `source`        `llm.last_source()`. THE FIRST COLUMN TO READ. `fixture` means
                  the model did not answer and every other number on the row
                  describes a fixture, not a model. A parity table whose rows are
                  fixture rows compares this repository against itself.
  `revisions`     how many developer->reviewer passes ran. THE SHARPEST SIGNAL
                  and the reason this lane's answer is not simply "it works":
                  CLAUDE.md records a clean run ending `status=failed`, exit 4,
                  because a real reviewer would not approve what the developer
                  kept producing. A weaker model does that MORE, and the count is
                  the measurement.
  `status`        the run's own ending. `promoted` / `blocked` / `failed`.
  `verdict`       the security verdict. MUST NOT MOVE. It comes from
                  `compute_security_verdict`, five lines of Python with no model
                  in them, so a local model that changes it would mean the block
                  rule is not what this project claims.
  `provenance`    `scan_provenance`. Says whether the scanners ran at all.
  `wall_clock_s`  seconds. Reported as a RANGE over repeats, never a point --
                  CLAUDE.md's own instance of this trap: 116.88s / 149.68s /
                  102.83s for one unchanged snapshot, load-dependent.

WHAT THIS TABLE CANNOT SAY. A row is one run of one model against one ticket.
Two runs of the SAME model differ (temperature, and the revision loop's length
depends on a reviewer's prose), so a single-run delta is not a measurement of the
model -- it is a sample. `compare` therefore reports `samples` per side and
`render_parity_table` prints it, because a delta over n=1 read as a property of
the model is the same over-claim as a scanner count read as proof of a scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The one column whose movement would be a defect rather than a degradation.
#: Named here so the renderer and the tests share one spelling.
INVARIANT_COLUMN = "verdict"


@dataclass(frozen=True)
class RunObservation:
    """One run's measured outcome. Frozen: an observation is history.

    EVERY FIELD IS READ OFF A RUN, none computed here. `source` and `provenance`
    in particular are the pipeline's own provenance fields -- this module does not
    decide whether the model answered, it reports what the run recorded.
    """

    label: str                    # "bedrock nova-2-lite" / "ollama qwen2.5-coder:7b"
    model: str = ""               # the model id that answered
    source: str = ""              # "model" | "fixture" | "" (nobody recorded)
    status: str = ""              # promoted | blocked | failed | rejected | running
    verdict: str = ""             # pass | block | "" (security never ran)
    provenance: str = ""          # scanners | fixture-fallback | fixture-stub | ""
    revisions: int = 0            # developer->reviewer passes
    wall_clock_s: float = 0.0
    #: Anything the run makes plain that a column cannot hold -- a reviewer's
    #: actual objection, a timeout, a refusal. Rendered under the table.
    notes: str = ""

    def is_model_run(self) -> bool:
        """True only when the MODEL answered.

        The check is `== "model"`, never `!= "fixture"`: `source == ""` means
        nobody recorded, and reading that as a model run is how an unmeasured run
        becomes evidence.
        """
        return self.source == "model"


@dataclass
class ParityRow:
    """One measured column, both sides, and whether the difference is allowed."""

    column: str
    baseline: str
    local: str
    #: True when the two sides differ. Plain inequality on the RENDERED strings,
    #: so a float that differs in the tenth decimal does not read as a change.
    differs: bool = False
    #: True when a difference in this column would be a DEFECT rather than a
    #: degradation -- `verdict` is the only one today.
    invariant: bool = False


@dataclass
class ParitySet:
    """A group of observations for one side, so a delta can carry its sample size."""

    label: str
    runs: list[RunObservation] = field(default_factory=list)

    @property
    def samples(self) -> int:
        return len(self.runs)

    def _range(self, values: list[float]) -> str:
        """A range, never a point. See the module docstring's measured instance."""
        if not values:
            return "-"
        low, high = min(values), max(values)
        if len(values) == 1:
            return f"{low:.1f}"
        if abs(high - low) < 0.05:
            return f"{low:.1f}"
        return f"{low:.1f}-{high:.1f}"

    def wall_clock(self) -> str:
        return self._range([r.wall_clock_s for r in self.runs])

    def revisions(self) -> str:
        counts = [r.revisions for r in self.runs]
        if not counts:
            return "-"
        if min(counts) == max(counts):
            return str(min(counts))
        return f"{min(counts)}-{max(counts)}"

    def _agreed(self, attr: str) -> str:
        """One value when every run agrees, else every value seen.

        DISAGREEMENT IS NOT AVERAGED AWAY. Three runs ending `promoted, promoted,
        failed` is the most interesting result this harness can produce -- it is
        the non-determinism a model introduces -- so it renders as
        `promoted|failed` rather than as a majority.
        """
        seen = [getattr(r, attr) or "-" for r in self.runs]
        if not seen:
            return "-"
        unique = sorted(set(seen))
        return unique[0] if len(unique) == 1 else "|".join(unique)

    def status(self) -> str:
        return self._agreed("status")

    def verdict(self) -> str:
        return self._agreed(INVARIANT_COLUMN)

    def provenance(self) -> str:
        return self._agreed("provenance")

    def source(self) -> str:
        return self._agreed("source")

    def model(self) -> str:
        return self._agreed("model")


def compare(baseline: ParitySet, local: ParitySet) -> list[ParityRow]:
    """Build the parity table. Pure: no I/O, no clock, no model.

    `verdict` is flagged `invariant`, and that is the assertion the whole table
    exists to support: the security decision is computed by five lines of Python
    with no model in them, so it must read identically on both sides. If it does
    not, the finding is not "the local model is worse" -- it is that the block
    rule is not model-independent, which would falsify this project's thesis.
    """
    columns = [
        ("source", baseline.source(), local.source(), False),
        ("model", baseline.model(), local.model(), False),
        ("status", baseline.status(), local.status(), False),
        (INVARIANT_COLUMN, baseline.verdict(), local.verdict(), True),
        ("provenance", baseline.provenance(), local.provenance(), False),
        ("revisions", baseline.revisions(), local.revisions(), False),
        ("wall_clock_s", baseline.wall_clock(), local.wall_clock(), False),
        ("samples", str(baseline.samples), str(local.samples), False),
    ]
    return [
        ParityRow(column=name, baseline=left, local=right,
                  differs=left != right, invariant=invariant)
        for name, left, right, invariant in columns
    ]


def render_parity_table(baseline: ParitySet, local: ParitySet) -> list[str]:
    """The table as lines. Lines, not a blob -- every renderer here joins once.

    THE UNMEASURED CASE IS STATED, NOT OMITTED. A side with zero runs renders as
    `NOT MEASURED` on every column with a sentence saying so, because a table with
    an empty column reads as a table somebody forgot to finish rather than as an
    honest gap -- and a reader fills a gap with an assumption.

    A `fixture` source is called out ABOVE the table rather than left as one cell,
    because it invalidates every other row and nobody reads a table from the top
    left when the numbers are further down.
    """
    lines: list[str] = []
    rows = compare(baseline, local)
    width = max(len(r.column) for r in rows)

    for side in (baseline, local):
        if not side.samples:
            lines.append(f"!! {side.label}: NOT MEASURED -- no run was recorded for "
                         f"this side, so every difference below is unknown rather "
                         f"than zero.")
        elif side.source() != "model":
            lines.append(f"!! {side.label}: source={side.source()} -- the model did "
                         f"not answer, so this side's numbers describe a fixture "
                         f"and the comparison is invalid.")

    lines.append(f"{'':<{width}}  {baseline.label:<28}  {local.label}")
    lines.append(f"{'-' * width}  {'-' * 28}  {'-' * 28}")
    for row in rows:
        mark = ""
        if row.invariant:
            mark = "  <- MUST NOT DIFFER" if row.differs else "  <- invariant, held"
        elif row.differs:
            mark = "  <- differs"
        lines.append(f"{row.column:<{width}}  {row.baseline:<28}  "
                     f"{row.local:<28}{mark}")

    for side in (baseline, local):
        for run in side.runs:
            if run.notes:
                lines.append(f"note [{side.label}]: {run.notes}")
    return lines
