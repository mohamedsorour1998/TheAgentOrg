"""Running one stage as its own process. A5's per-job isolation, and A6's guard.

OWNER: Lane A.

WHY A SUBPROCESS AND NOT A FUNCTION CALL
========================================
Actions gives each job a fresh runner, and that isolation is not decoration -- it
is why one stage cannot leave state behind for the next. Two pieces of module state
in this repository would be inherited by an in-process call and are exactly the
kind that produces a false measurement:

  * `llm._record` / `llm.last_source()`. `run_stage._stage_plan` calls
    `llm.reset_source()` on its first line and its comment says why: "on a laptop
    the same process can run several stages in a row -- without this a run would
    inherit the previous one's provenance, which is worse than reporting nothing
    because it looks like a measurement."
  * the scanner fan-out memo. `tests/conftest.py`'s fifth guard clears it around
    every test, and its docstring records three tests that failed in the full suite
    while passing alone -- one of them reporting REAL-SCANNER line numbers in a
    test that had just made every binary unreachable. "A stale cache hit looks
    exactly like a scan."

A worker running seven stages in one process would inherit both, all day, with
nothing clearing them. So each stage is `python scripts/run_stage.py <stage>`, which
is the same command the Actions job runs -- the same bytes, verified by the same
argparse -- and the exit code comes back the way `run-pipeline.yml` reads it.

THE SECOND REASON IS THE EXIT CODE ITSELF. `run_stage.py` communicates through
`sys.exit(main())`. Calling `main()` in-process gets a return value; running it as a
process gets a return code AND the guarantee that an `os._exit`, a segfault or a
`SystemExit` raised anywhere in the tree still arrives as a number. A5's contract is
"claim -> run one stage -> record", and "record" means recording what the process
actually did.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

from . import Job

# The repository root, and the script. Anchored off this file rather than off the
# working directory, because a worker is a long-running process and its cwd is
# whatever it was started in.
_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_RUN_STAGE = _ROOT / "scripts" / "run_stage.py"


class StageOutcome:
    """What one stage process did: its code, and the two lines the queue reads.

    `run_id` is parsed out because `plan` is the stage that CREATES a run -- no
    caller can know the id before it runs, and `run-pipeline.yml` solves the same
    problem the same way, by grepping `^run_id=` out of the log. That guard's `||
    true` is what makes it reachable (CLAUDE.md records the reason at length), and
    the equivalent here is that `run_id` is `""` when no line matched rather than
    an exception from a `next()` with no default.
    """

    def __init__(self, *, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.run_id = _read_line(stdout, "run_id")
        self.status = _read_line(stdout, "status")


def _read_line(stdout: str, key: str) -> str:
    """The value of a `key=value` line in a stage's output, or `""`.

    `""` rather than a raise, deliberately: a stage that printed nothing is a real
    condition the caller has to handle (it is what a crashed `plan` looks like),
    and turning it into an exception here would replace a diagnosable outcome with
    a traceback from the wrong layer. `scripts/worker.py` checks for the empty
    value and says what it means.
    """
    for line in stdout.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def run_stage(job: Job, *, timeout: int | None = None,
              env: dict[str, str] | None = None,
              ticket_id: str = "", ticket_text: str = "",
              poisoned: bool = False, trigger: str = "manual",
              approver: str = "") -> StageOutcome:
    """Run one stage as its own process and hand back what it did.

    THE ARGUMENTS ARE THE WORKFLOW'S, one for one. `--poisoned` and `--auto-approve`
    are passed as the STRINGS `"true"`/`"false"` because that is what
    `run_stage.flag` parses and what the REST dispatch API sends -- and `flag`
    RAISES on anything else, deliberately, so `poisoned=yes` is a loud error rather
    than a quiet clean run. Formatting them here as `str(bool).lower()` produces
    exactly the two strings it accepts.

    `--auto-approve false` ALWAYS, and it is not a parameter of this function. On
    the queue there is no Environment holding the job, so a gate stage runs only
    after `queue.resume` released it -- which means a human (or the documented
    override) already decided. `--auto-approve true` would change the `by`
    attribution to `"auto"` and record a decision nobody made. The gates are the
    one thing this lane must not weaken, so the flag that could weaken them is not
    exposed.

    `--approver` IS PASSED ONLY WHEN THERE IS ONE. `run_stage.py` defaults it to
    `""` and then falls back to `github-environment-reviewer`, which is the right
    name on Actions and a false one here -- no Environment held this job. Passing
    an empty `--approver` explicitly would reach the same fallback, so the argument
    is omitted entirely when nobody is named, and the fallback stays the honest
    answer to "the queue did not record who".

    `cwd=_ROOT` because `run_stage.py` imports `agentorg` and `fixtures_loader`
    resolves from the repository root. A worker started in `/` would otherwise get
    the `FileNotFoundError: '/app/fixtures/plan_result.json'` that CLAUDE.md
    records from the first AgentCore runtime to serve traffic.
    """
    command = [sys.executable, str(_RUN_STAGE), job.stage]
    if job.stage == "plan":
        command += ["--ticket-id", ticket_id, "--ticket-text", ticket_text,
                    "--trigger", trigger]
    else:
        command += ["--run-id", job.run_id]
    command += ["--poisoned", str(bool(poisoned)).lower(), "--auto-approve", "false"]
    if approver:
        command += ["--approver", approver]

    completed = subprocess.run(
        command,
        cwd=str(_ROOT),
        # `os.environ` as the base, not a bare dict: the stage needs PATH (the
        # security agent shells out to three scanners), and on the deployed path it
        # needs AWS_* for the model. An empty env would make every stage fall back
        # to its fixture with every job green, which is the failure this repository
        # is built to make visible.
        #
        # PYTHONPATH=_ROOT, AND IT IS NOT COSMETIC -- MEASURED
        # ────────────────────────────────────────────────────
        # `sys.path[0]` for `python scripts/run_stage.py` is `scripts/`, so the
        # repository root never reaches `sys.path` and `import agentorg` resolves
        # through whatever finder answers first. With an EDITABLE INSTALL that is
        # the install's mapping, which points at the checkout `pip install -e` was
        # run from -- not necessarily this one. MEASURED from this worktree, with
        # `_ROOT` correct and no PYTHONPATH:
        #
        #     state=/Users/sorour/sorour/TheAgentOrg/runs/<id>.state.json
        #                            ^ the SHARED checkout, not this tree
        #
        # `gates._STATE_DIR` derives from `agentorg.gates.__file__`, so the stage
        # wrote its state into a different tree's `runs/` and this worker's next
        # stage would `gates.load` from here and raise FileNotFoundError on a run
        # that had just succeeded. `cwd=_ROOT` does NOT fix it: cwd is not on
        # `sys.path` for a script invocation.
        #
        # This is the same defect as commit cf5cb83, which fixed it for
        # `tests/test_trigger_provenance.py` -- the only failing test in every one
        # of this phase's fourteen lane worktrees, read as their own regression by
        # THREE separate lanes. Same cause, same one-line fix, a different caller.
        # `env` can still override it, deliberately, so a caller with a genuine
        # reason keeps the last word.
        env={"PYTHONPATH": str(_ROOT), **os.environ, **(env or {})},
        capture_output=True,
        text=True,
        # `check=False`. A non-zero exit is DATA here, not an error: 3 is the
        # deterministic block rule working. `check=True` would raise on the
        # pipeline's most important outcome.
        check=False,
        timeout=timeout,
    )
    return StageOutcome(exit_code=completed.returncode,
                        stdout=completed.stdout, stderr=completed.stderr)
