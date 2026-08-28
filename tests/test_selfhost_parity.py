"""The parity table: it must not flatter the local model, or hide a gap.

Every test here is about a way the table could MISLEAD rather than about
arithmetic. The table is the lane's deliverable, so the failure mode that matters
is a reader drawing a conclusion the runs do not support -- an unmeasured side
read as equal, a fixture row read as a model row, or a disagreement across repeats
averaged into a single reassuring value.
"""

from __future__ import annotations

from agentorg.selfhost import parity


def _run(**kwargs) -> parity.RunObservation:
    """One observation with sensible defaults, so each test names only its point."""
    base = {
        "label": "x",
        "model": "m",
        "source": "model",
        "status": "blocked",
        "verdict": "block",
        "provenance": "scanners",
        "revisions": 1,
        "wall_clock_s": 10.0,
    }
    base.update(kwargs)
    return parity.RunObservation(**base)


def test_only_the_literal_word_model_counts_as_a_model_run():
    """`source == ""` means nobody recorded, and must not read as a model run.

    Written as `== "model"` rather than `!= "fixture"` in the source. The blank
    case is the one that matters: `llm.last_source()` returns None when nothing
    called the model, `run_pipeline` stores that as `""`, and a run that never
    asked must not claim the model answered.
    """
    assert _run(source="model").is_model_run() is True
    assert _run(source="fixture").is_model_run() is False
    assert _run(source="").is_model_run() is False, (
        "a run that recorded no source claimed to be a model run"
    )


def test_an_unmeasured_side_is_named_rather_than_rendered_as_zero():
    """An empty column reads as unfinished, and a reader fills a gap with an
    assumption. The gap must be a sentence, not a dash."""
    measured = parity.ParitySet("bedrock", [_run()])
    unmeasured = parity.ParitySet("ollama", [])
    lines = parity.render_parity_table(measured, unmeasured)
    body = "\n".join(lines)
    assert "NOT MEASURED" in body
    assert "unknown rather than zero" in body, (
        "the unmeasured side did not say its differences are unknown, so a "
        "reader could take the dashes as measured equality"
    )


def test_a_fixture_side_is_called_out_ABOVE_the_table():
    """It invalidates every row, and nobody reads a table from the top left.

    This is the check that would have caught the run this lane actually
    measured: `source=fixture` on the local side, with `status=blocked`,
    `verdict=block` and `provenance=scanners` all looking perfectly healthy
    beside it. Every one of those came from a fixture.
    """
    good = parity.ParitySet("bedrock", [_run(source="model")])
    fell_back = parity.ParitySet("ollama", [_run(source="fixture")])
    lines = parity.render_parity_table(good, fell_back)
    # The warning must precede the header row, not merely appear somewhere.
    header_index = next(i for i, line in enumerate(lines) if "bedrock" in line
                        and "ollama" in line)
    warnings = [i for i, line in enumerate(lines) if line.startswith("!!")]
    assert warnings, "no fixture warning was emitted at all"
    assert min(warnings) < header_index, (
        "the fixture warning appeared below the table it invalidates"
    )
    assert "comparison is invalid" in "\n".join(lines)


def test_the_verdict_is_flagged_INVARIANT_and_says_so_when_it_holds():
    """The security verdict comes from five lines of Python with no model in them.

    Both directions are asserted. A table that only marked a DIFFERENCE would
    leave the held case looking like any other equal row -- and "the verdict did
    not move across two different models" is the single most important thing this
    table reports, so it must be legible rather than inferred from an absence.
    """
    held = parity.compare(
        parity.ParitySet("a", [_run(verdict="block")]),
        parity.ParitySet("b", [_run(verdict="block")]),
    )
    verdict_row = next(r for r in held if r.column == parity.INVARIANT_COLUMN)
    assert verdict_row.invariant is True
    assert verdict_row.differs is False

    moved = parity.compare(
        parity.ParitySet("a", [_run(verdict="block")]),
        parity.ParitySet("b", [_run(verdict="pass")]),
    )
    moved_row = next(r for r in moved if r.column == parity.INVARIANT_COLUMN)
    assert moved_row.differs is True
    rendered = "\n".join(parity.render_parity_table(
        parity.ParitySet("a", [_run(verdict="block")]),
        parity.ParitySet("b", [_run(verdict="pass")]),
    ))
    assert "MUST NOT DIFFER" in rendered, (
        "a moved security verdict rendered as an ordinary difference; it is a "
        "defect in the block rule, not a degradation of the model"
    )


def test_disagreeing_statuses_across_repeats_are_shown_not_averaged():
    """Non-determinism is the most interesting thing this harness can find.

    Three runs ending promoted/promoted/failed is a REAL finding about the model,
    and a majority vote would report `promoted` and delete it.
    """
    flaky = parity.ParitySet("ollama", [
        _run(status="promoted"), _run(status="promoted"), _run(status="failed"),
    ])
    assert flaky.status() == "failed|promoted", (
        "disagreeing endings were folded to one value; the non-determinism is "
        "the measurement"
    )
    assert flaky.samples == 3


def test_wall_clock_renders_as_a_RANGE_over_repeats():
    """CLAUDE.md's own measured trap: 116.88 / 149.68 / 102.83 for one snapshot.

    A single number read as a property of the model is the same over-claim as a
    scanner count read as proof of a scan. One sample renders as a point, because
    a range of one value would be a fake spread.
    """
    spread = parity.ParitySet("x", [_run(wall_clock_s=10.0), _run(wall_clock_s=42.5)])
    assert spread.wall_clock() == "10.0-42.5"
    single = parity.ParitySet("x", [_run(wall_clock_s=24.6)])
    assert single.wall_clock() == "24.6"
    identical = parity.ParitySet("x", [_run(wall_clock_s=8.0), _run(wall_clock_s=8.0)])
    assert identical.wall_clock() == "8.0", (
        "two identical timings rendered as a range, which implies a spread that "
        "was not measured"
    )


def test_the_revision_count_is_a_range_too_because_it_is_the_sharpest_signal():
    """A weaker model needs more developer->reviewer passes; that IS the number."""
    varied = parity.ParitySet("x", [_run(revisions=1), _run(revisions=3)])
    assert varied.revisions() == "1-3"
    assert parity.ParitySet("x", [_run(revisions=2)]).revisions() == "2"
    assert parity.ParitySet("x", []).revisions() == "-"


def test_the_sample_count_is_a_row_so_a_delta_carries_its_own_n():
    """A one-run delta is a sample, not a property of the model."""
    rows = parity.compare(
        parity.ParitySet("a", [_run()]),
        parity.ParitySet("b", [_run(), _run()]),
    )
    samples = next(r for r in rows if r.column == "samples")
    assert samples.baseline == "1"
    assert samples.local == "2"


def test_a_note_survives_into_the_rendered_table():
    """A reviewer's actual objection is the thing a column cannot hold."""
    lines = parity.render_parity_table(
        parity.ParitySet("a", [_run()]),
        parity.ParitySet("b", [_run(notes="the reply was prose, not JSON")]),
    )
    assert any("prose, not JSON" in line for line in lines), (
        "the note was dropped, so the one human-readable finding on the row "
        "never reached the reader"
    )
