"""A7: exit-code parity with the workflow. block=3, refusal=4, already-final=5.

OWNER: Lane A, task A7.

WHY A WHOLE FILE FOR A FIVE-ENTRY TABLE
=======================================
`scripts/run_stage.py:139-178` spends forty lines explaining why there are five exit
codes and not one, and the load-bearing sentence is:

    But 1 is what an uncaught exception already exits with, so a block sharing that
    code would make the poisoned demo run indistinguishable from a broken workflow
    on the projector.

The queue must not undo that. A queue recording every non-zero exit as `failed`
would flatten five carefully separated facts into one on the surface an operator
reads -- and the poisoned run, which is the pipeline WORKING, would sit beside a
crashed worker with the same word next to it.

THE CODES ARE READ OFF `run_stage.py`, NEVER RESTATED, and the tests below assert
that they are. A hardcoded `3` would be a second declaration of the fact this lane
must not break, and it would drift SILENTLY: if `EXIT_BLOCKED` moved, a hardcoded
table would map the new code through to `failed` and the poisoned run would report
as a crash while every test that checked "3 means blocked" kept passing against a
constant nobody produces any more. CLAUDE.md records three mutations that survived
793 tests for exactly this reason -- `run_stage.py` inheriting `graph.py`'s COMMENT
about a hazard without inheriting its TEST.
"""

import importlib.util
import pathlib

import pytest

from agentorg.queue import exit_codes

RUN_STAGE = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_stage.py"


def _run_stage_module():
    """`scripts/run_stage.py` as a module -- the same load five other test files use."""
    spec = importlib.util.spec_from_file_location("run_stage_for_exit_test", RUN_STAGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGE = _run_stage_module()

# GUARD AGAINST A VACUOUS FILE, and a real one: if this import ever stopped
# producing the constants, every assertion below would compare None to None.
assert exit_codes.table(), "the exit-code table is empty; this file would pin nothing"
for _name in ("EXIT_OK", "EXIT_BLOCKED", "EXIT_REJECTED", "EXIT_ALREADY_FINAL",
              "EXIT_NOT_PROMOTABLE"):
    assert hasattr(STAGE, _name), f"run_stage.py no longer defines {_name}"


# ── A7: the three the brief names, by value ──────────────────────────────────

def test_the_three_codes_the_brief_names_have_the_values_it_names():
    """block=3, refusal=4, already-final=5. Asserted as LITERALS here on purpose.

    Everywhere else in this package the codes are read off `run_stage.py` so the two
    cannot drift. Here the literal IS the requirement -- A7 says "exit-code parity
    with today", and a test that read both sides from the same source would pass
    even if all five codes changed together. So this file pins the numbers and the
    tests below pin that the package does not restate them.
    """
    assert STAGE.EXIT_BLOCKED == 3
    assert STAGE.EXIT_REJECTED == 4
    assert STAGE.EXIT_ALREADY_FINAL == 5


def test_three_is_not_one_and_that_is_the_whole_point():
    """The demo's meaning depends on it. A block sharing 1 with an uncaught
    exception would make the poisoned run indistinguishable from a broken workflow
    on a projector."""
    assert STAGE.EXIT_BLOCKED != 1
    assert exit_codes.status_for(3) == "blocked"
    assert exit_codes.status_for(1) == "failed"
    assert exit_codes.status_for(3) != exit_codes.status_for(1)


def test_the_deterministic_block_maps_to_its_own_status():
    assert exit_codes.status_for(STAGE.EXIT_BLOCKED) == "blocked"


def test_a_human_refusal_maps_to_its_own_status():
    assert exit_codes.status_for(STAGE.EXIT_REJECTED) == "rejected"


def test_already_final_maps_to_its_own_status():
    assert exit_codes.status_for(STAGE.EXIT_ALREADY_FINAL) == "already_final"


def test_zero_means_the_run_advances():
    assert exit_codes.status_for(STAGE.EXIT_OK) == "done"


def test_the_four_terminal_endings_are_not_collapsed_into_one():
    """"The run was blocked", "a human refused it", "a recorder declined to
    overwrite a finished run" and "this crashed" are four different facts, and the
    demo's whole point is that the first is a WORKING pipeline reporting a real
    verdict."""
    statuses = {exit_codes.status_for(code)
                for code in (STAGE.EXIT_BLOCKED, STAGE.EXIT_REJECTED,
                             STAGE.EXIT_ALREADY_FINAL, 1)}
    assert statuses == {"blocked", "rejected", "already_final", "failed"}
    assert len(statuses) == 4, "two endings were flattened into one status"


# ── the codes are READ, not restated ─────────────────────────────────────────

def test_the_table_is_built_from_run_stages_own_constants():
    """Every key in the table is a constant `run_stage.py` defines.

    This is what makes the no-drift claim testable rather than a comment. If
    `EXIT_BLOCKED` moved to 7, this table's key would move with it -- and the test
    below is what notices if somebody replaces the import with a literal.
    """
    declared = {STAGE.EXIT_OK, STAGE.EXIT_BLOCKED, STAGE.EXIT_REJECTED,
                STAGE.EXIT_ALREADY_FINAL, STAGE.EXIT_NOT_PROMOTABLE}
    assert set(exit_codes.table()) == declared


def test_exit_codes_does_not_carry_a_hardcoded_copy_of_the_numbers():
    """Asserted over the AST, not over the source text, and the difference is the
    whole point.

    CLAUDE.md records this pattern found TWICE in one lane: a test satisfied by the
    COMMENT explaining the thing it was checking. This module's docstring says "read
    `EXIT_BLOCKED` and its four siblings off the file that produces them ... A
    hardcoded `3` would be a second declaration" -- so `assert "3" not in source`
    would fail on the prose, and `assert "EXIT_BLOCKED" in source` would be
    satisfied BY the prose while the import was gone.

    So the check walks the AST for integer literals in the module's own code. The
    only numbers that legitimately appear are in `_load_run_stage`'s path
    arithmetic, which is why the walk looks at `_build_table` specifically.
    """
    import ast

    source = pathlib.Path(exit_codes.__file__).read_text()
    tree = ast.parse(source)

    builders = [node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "_build_table"]
    assert len(builders) == 1, (
        "_build_table is gone or duplicated; this test would pin nothing"
    )

    literals = [node.value for node in ast.walk(builders[0])
                if isinstance(node, ast.Constant) and isinstance(node.value, int)
                and not isinstance(node.value, bool)]
    assert literals == [], (
        f"_build_table contains integer literals {literals}: the exit codes are "
        f"being restated rather than read off run_stage.py, so a code that moved "
        f"would map through to `failed` and the poisoned run would report as a "
        f"crash with every test still green"
    )

    attributes = {node.attr for node in ast.walk(builders[0])
                  if isinstance(node, ast.Attribute)}
    assert {"EXIT_OK", "EXIT_BLOCKED", "EXIT_REJECTED", "EXIT_ALREADY_FINAL",
            "EXIT_NOT_PROMOTABLE"} <= attributes, (
        "the table no longer reads the constants off the loaded module"
    )


# ── an unrecognised code is NAMED, not guessed ───────────────────────────────

def test_one_is_unclassified_and_a_future_code_is_too():
    """`1` is what an uncaught exception exits with, so it has NO table entry and
    falls through to `failed` -- which is the correct reading and is what keeps 3 and
    1 apart. A code of `7` added by a future stage is also unclassified and is a bug
    in the table. Both deserve to be named."""
    assert exit_codes.unclassified_exit(1) is True
    assert exit_codes.unclassified_exit(7) is True
    assert exit_codes.unclassified_exit(3) is False


def test_an_unknown_code_becomes_failed_rather_than_the_nearest_neighbour():
    """`agent_client`'s classifier makes the same choice: "a classifier that guesses
    is worse than one admitting it did not recognise the error, because the guess is
    what makes a caller wait out a condition that will never clear.\""""
    assert exit_codes.status_for(99) == "failed"
    assert exit_codes.status_for(-1) == "failed"


def test_not_promotable_keeps_its_raw_code_even_though_it_maps_to_failed():
    """6 maps to `failed` rather than a sixth status, because "this run had not
    earned a promotion" and "this stage crashed" both mean the run stopped and needs
    a person. The raw code is stored on the job either way, so the distinction is
    not lost -- it is just not a separate status."""
    assert exit_codes.status_for(STAGE.EXIT_NOT_PROMOTABLE) == "failed"
    assert exit_codes.unclassified_exit(STAGE.EXIT_NOT_PROMOTABLE) is False, (
        "6 IS in the table, so it must not read as unrecognised -- otherwise the "
        "worker would log it as a bug in the table"
    )


# ── the inverse, and the guard that stops it guessing ────────────────────────

def test_code_for_refuses_failed_by_name_rather_than_by_a_collision():
    """MEASURED. The first version let the inversion refuse this on its own:

        >>> code_for("failed")
        6

    The table has exactly one entry mapping to `failed` (`EXIT_NOT_PROMOTABLE`), so
    inverting it produces a confident, single, WRONG answer. The ambiguity is in
    `status_for`, which sends every unrecognised code -- `1` included -- to `failed`
    as well. So the guard says so by name instead of relying on a collision that
    does not happen.
    """
    with pytest.raises(ValueError, match="no single exit code means 'failed'"):
        exit_codes.code_for("failed")


def test_code_for_inverts_the_table_for_the_statuses_that_have_one_code():
    assert exit_codes.code_for("done") == STAGE.EXIT_OK
    assert exit_codes.code_for("blocked") == STAGE.EXIT_BLOCKED
    assert exit_codes.code_for("rejected") == STAGE.EXIT_REJECTED
    assert exit_codes.code_for("already_final") == STAGE.EXIT_ALREADY_FINAL


def test_code_for_refuses_a_status_no_code_produces():
    with pytest.raises(ValueError, match="no exit code means"):
        exit_codes.code_for("paused")


def test_the_table_handed_out_is_a_copy():
    """A caller that mutated the table would change every later reading of it, and
    the poisoned run's meaning is in that dict."""
    exit_codes.table()[3] = "done"
    assert exit_codes.status_for(3) == "blocked", "the table was mutated by a caller"
