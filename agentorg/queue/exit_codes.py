"""Exit code -> job status. A7, and the one place the demo's meaning is preserved.

OWNER: Lane A, task A7.

WHY THIS IS A SEPARATE MODULE AND NOT FOUR LINES IN THE WORKER
==============================================================
Because the mapping is the whole of A7, and `scripts/worker.py` is a loop. A
mapping buried in a loop gets read as plumbing; this one is a contract with the
projector.

`scripts/run_stage.py:139-178` spends forty lines explaining why there are five
exit codes and not one, and the load-bearing sentence is this:

    But 1 is what an uncaught exception already exits with, so a block sharing
    that code would make the poisoned demo run indistinguishable from a broken
    workflow on the projector.

THE QUEUE MUST NOT UNDO THAT. A queue that recorded every non-zero exit as
`failed` would take five carefully separated facts and flatten them into one, on
the surface an operator reads -- and the poisoned run, which is the pipeline
WORKING, would appear beside a crashed worker with the same word next to it. So the
codes survive into the queue as distinct statuses, and this table is the only place
the translation happens.

THE CODES ARE IMPORTED FROM `run_stage.py`, NOT RESTATED
=======================================================
Read `EXIT_BLOCKED` and its four siblings off the file that produces them, through
`importlib`, rather than writing `3` here. `scripts/` is not a package, so this is
the same `spec_from_file_location` load that five test files already use.

A hardcoded `3` would be a second declaration of the fact this whole lane must not
break, and it would drift SILENTLY: if `EXIT_BLOCKED` ever moved, a hardcoded table
would map the new code through `_UNKNOWN` to `failed`, and the poisoned demo run
would report as a crash while every test that checked "3 means blocked" kept
passing against a constant nobody produces any more. CLAUDE.md records three
mutations that survived 793 tests for exactly this reason -- `run_stage.py`
inheriting `graph.py`'s comment about a hazard without inheriting its test.

WHAT HAPPENS TO A CODE THIS TABLE DOES NOT KNOW
==============================================
It becomes `failed`, and `unclassified_exit` says so. It is NOT guessed into the
nearest neighbour. `agent_client.py`'s failure classifier makes the same choice and
states the reason: "a classifier that guesses is worse than one admitting it did
not recognise the error, because the guess is what makes a caller wait out a
condition that will never clear."
"""

from __future__ import annotations

import importlib.util
import pathlib

from . import JobStatus

# `scripts/run_stage.py`, from this file. Resolved rather than assumed: three
# directories up from `agentorg/queue/exit_codes.py` is the repository root, which
# is the same anchoring `fixtures_loader` and `gates._STATE_DIR` use.
_RUN_STAGE = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "run_stage.py"


def _load_run_stage():
    """`scripts/run_stage.py` as a module, without making `scripts/` a package.

    The same `spec_from_file_location` load `tests/test_run_pipeline_workflow.py`,
    `tests/test_promote_guard.py` and three other test files already use. A
    distinct module name so this import cannot collide with theirs in one process.
    """
    spec = importlib.util.spec_from_file_location("run_stage_for_queue", _RUN_STAGE)
    if spec is None or spec.loader is None:
        raise ImportError(
            f"cannot load {_RUN_STAGE}, which is where this queue's exit codes "
            f"are DEFINED. They are read rather than restated so the two cannot "
            f"drift -- see this module's docstring. If that file has been deleted "
            f"(the plan schedules it for Phase 3), its exit codes must move into "
            f"`agentorg/` and this import must follow them; a hardcoded 3 here "
            f"would let the poisoned run report as a crash with every test green."
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_table() -> dict[int, JobStatus]:
    """The code -> status map, built from `run_stage.py`'s own constants.

    Five entries, one per fact `run_stage.py` distinguishes:

      EXIT_OK             0  the stage completed and the run advances
      EXIT_BLOCKED        3  THE DETERMINISTIC BLOCK RULE STOPPED THE RUN. The
                             demo's whole point, and the reason this table exists:
                             it must not read as a crash.
      EXIT_REJECTED       4  a human refused a gate, the revision cap was spent,
                             or SRE said no_go
      EXIT_ALREADY_FINAL  5  a recorder was asked to overwrite a finished run and
                             declined
      EXIT_NOT_PROMOTABLE 6  promote refused a run that had not earned it

    `1` IS DELIBERATELY ABSENT. It is what an uncaught exception exits with, so it
    has no entry and falls through to `failed` via `unclassified_exit` -- which is
    the correct reading and is what keeps 3 and 1 apart.

    6 maps to `failed` rather than to a sixth status: the queue's vocabulary
    distinguishes ENDINGS a human acts on differently, and "this run had not earned
    a promotion" and "this stage crashed" both mean the run stopped and needs a
    person. The raw code is stored on the job either way (`Job.exit_code`), so the
    distinction is not lost -- it is just not a separate status.
    """
    stage = _load_run_stage()
    return {
        stage.EXIT_OK: "done",
        stage.EXIT_BLOCKED: "blocked",
        stage.EXIT_REJECTED: "rejected",
        stage.EXIT_ALREADY_FINAL: "already_final",
        stage.EXIT_NOT_PROMOTABLE: "failed",
    }


_TABLE: dict[int, JobStatus] | None = None


def table() -> dict[int, JobStatus]:
    """The mapping, loaded once. A copy, so a caller cannot edit the table."""
    global _TABLE
    if _TABLE is None:
        _TABLE = _build_table()
    return dict(_TABLE)


def status_for(exit_code: int) -> JobStatus:
    """The job status this exit code means. Unknown codes become `failed`.

    `1` arrives here and becomes `failed`, which is right -- and `3` arrives here
    and becomes `blocked`, which is the point. Use `unclassified_exit` to tell the
    two apart when reporting, because both answer `failed`-shaped questions with a
    different truth behind them.
    """
    return table().get(exit_code, "failed")


def unclassified_exit(exit_code: int) -> bool:
    """True when this code has no meaning in `run_stage.py`'s vocabulary.

    Exists so the worker can SAY SO rather than reporting a crash as though the
    code had been understood. `1` is unclassified and is a crash; a code of `7`
    added by a future stage without a table entry is also unclassified and is a
    bug in this table. Both deserve to be named, and neither should be silently
    absorbed into the same word as the deterministic block.
    """
    return exit_code not in table()


def code_for(status: JobStatus) -> int:
    """The exit code that means `status`. The table, read backwards.

    Needed by `scripts/worker.py` in the one place it records an outcome for which
    NO stage process ran: a reclaimed job whose work is already on the record. That
    job still needs an `exit_code`, and writing `5` there would be a second
    declaration of `EXIT_ALREADY_FINAL` inside a file that already imports the
    first -- exactly the drift this module exists to prevent, one layer up.

    `failed` IS REFUSED, AND THE REFUSAL IS EXPLICIT RATHER THAN EMERGENT. That
    distinction is worth recording, because the first version of this function tried
    to let the inversion refuse it on its own and MEASURED the opposite:

        >>> code_for("failed")
        6

    The table has exactly one entry mapping to `failed` (`EXIT_NOT_PROMOTABLE`), so
    inverting it produces a confident, single, WRONG answer. The ambiguity is not in
    the table -- it is in `status_for`, which sends every UNRECOGNISED code to
    `failed` as well, `1` (a crash) included. So `failed` is a status reachable from
    many codes and this function has no business guessing which, and the guard says
    so by name instead of relying on a collision that does not happen.

    A caller recording a failure has a real code to record; if it does not, `1` is
    the honest literal and it should say so at its own call site.
    """
    if status == "failed":
        raise ValueError(
            "no single exit code means 'failed': `status_for` maps "
            "EXIT_NOT_PROMOTABLE (6) AND every unrecognised code -- including 1, "
            "which is what an uncaught exception exits with -- to it. Returning "
            "one of them would put 'this run had not earned a promotion' on a job "
            "that crashed. Pass the real exit code, or 1 for a crash."
        )
    inverted = {value: key for key, value in table().items()}
    if status not in inverted:
        raise ValueError(
            f"no exit code means {status!r}. Statuses reachable from one code: "
            f"{', '.join(sorted(inverted))}."
        )
    return inverted[status]
