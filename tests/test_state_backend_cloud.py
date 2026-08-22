"""The cloud path's state seam: reading a run, and being VISIBLE while it waits.

Two defects in one seam, so one file.

B4 -- READING. PROVED, before the fix:

    $ STATE_BACKEND=dynamodb .venv-main/bin/python -c \
        "from agentorg import gates; gates._state_path('x')"
    RuntimeError: there is no state FILE on the 'dynamodb' backend; the run's
    state is an item in 'theagentorg-runs'. Read it with gates.load(run_id) ...

`run_stage._load` called `gates._state_path`, which refuses on that backend BY
DESIGN, so every cloud stage after `plan` raised. CLAUDE.md carried it as KNOWN
DEBT; `gates.load` already handled both backends correctly, so the fix is three
lines rather than the rewrite the old docstring implied.

THE REFUSAL FOR AN ABSENT RUN IS KEPT, and that is the half that matters.
`gates.load` raises `FileNotFoundError` on BOTH backends for a run that does not
exist, deliberately the same exception. `_load` turns it into the named
`SystemExit` about a broken artifact handoff. It must NOT be softened into a
fresh `RunState` -- that would start a new run and report success for work it
invented, which is the same defect as a check that did not run.

B5 -- BEING FOUND. `graph.py` calls `gates.pause` twice, from `_auto_gate` and
`_cli_gate`, both BEFORE returning a decision. `scripts/run_stage.py` never
called it at all, so NO cloud run appeared on `approve_server` -- the seam a
planned frontend reads.

`approve_server._awaiting` lists a run iff it has an open pause marker for a gate
with no decision recorded yet: `paused - decided`. The marker is the summary
sentence `gates.pause` writes, and this file IMPORTS that constant rather than
restating it.

WHY THE PAUSE BELONGS TO THE STAGE BEFORE THE GATE. In the cloud the gate job
does not start until somebody has already clicked, so a `gates.pause` inside
`_stage_gate` would write the marker and the decision in the same job:
`paused - decided` would be empty and the run would STILL never be listed. The
window the marker describes is the one where GitHub is holding the job at the
Environment, and the only code that runs before that window opens is the
preceding stage.
"""

import argparse
import importlib.util
from pathlib import Path

import pytest

from agentorg import approve_server, gates, log
from agentorg.common import config
from agentorg.state import RunState

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_SCRIPT = REPO_ROOT / "scripts" / "run_stage.py"

TICKET = "Add a per-IP login rate limit."

# IMPORTED, never restated -- see this file's header.
_MARKER = approve_server._PAUSE_MARKER


def _stage_module():
    spec = importlib.util.spec_from_file_location("run_stage_backend_test", STAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(**kw):
    base = {"run_id": "", "ticket_id": "", "ticket_text": "",
            "poisoned": "false", "auto_approve": "false", "approver": "reviewer-1",
            "trigger": "manual"}
    base.update(kw)
    return argparse.Namespace(**base)


def _no_comments(module, monkeypatch):
    monkeypatch.setattr(module.github_ops, "post_comment",
                        lambda state, body, finding=None: "local://x")


def test_load_reads_through_gates_load_and_never_touches_the_path_helper(monkeypatch):
    """THE SEAM TEST. `_state_path` must not be on this code path at all.

    Asserted by making `gates._state_path` raise `AssertionError` if it is
    called, rather than by checking the happy path still works: reading through
    `gates.load` on the LOCAL backend produces an identical result either way,
    because `gates.load` calls `_state_path` itself. So a test that only checked
    the returned state would pass against both the fix and the bug -- the exact
    shape of confidence that cannot be falsified.
    """
    module = _stage_module()
    original = RunState(ticket_id="CLOUD-1", ticket_text="x")

    def _forbidden(run_id):
        raise AssertionError(
            f"run_stage._load called gates._state_path({run_id!r}). That helper "
            f"refuses on the dynamodb backend BY DESIGN, so every cloud stage "
            f"after `plan` raises there. Read through gates.load instead, which "
            f"already handles both backends."
        )

    monkeypatch.setattr(module.gates, "load", lambda run_id: original)
    monkeypatch.setattr(module.gates, "_state_path", _forbidden)

    loaded = module._load(original.run_id)
    assert loaded.run_id == original.run_id


def test_load_works_on_the_DYNAMODB_backend(monkeypatch, tmp_path):
    """The measured defect, driven on the backend that exhibited it.

    `log.write_state` / `read_state` are the dynamodb seam `gates.load` uses;
    stubbed with an in-memory dict so this needs no AWS. The point is that
    `_load` reaches the state at all -- before the fix this raised RuntimeError
    from `_state_path` before any read was attempted.
    """
    module = _stage_module()
    monkeypatch.setattr(config, "STATE_BACKEND", "dynamodb")

    stored: dict[str, str] = {}
    monkeypatch.setattr(module.log, "write_state",
                        lambda run_id, doc: stored.__setitem__(run_id, doc))
    monkeypatch.setattr(module.log, "read_state", lambda run_id: stored.get(run_id))

    original = RunState(ticket_id="CLOUD-1", ticket_text="x")
    gates.save(original)
    assert stored, "the save did not reach the stubbed dynamodb seam"

    loaded = module._load(original.run_id)
    assert loaded.run_id == original.run_id, (
        "run_stage._load could not read a run back on the dynamodb backend"
    )
    assert loaded.ticket_id == "CLOUD-1"


def test_an_absent_run_is_a_NAMED_SystemExit_not_a_fresh_RunState(monkeypatch,
                                                                 tmp_path):
    """The refusal that must survive the change.

    Softening this into a fresh `RunState` would start a new run and report
    success for work it invented -- every approval already recorded silently
    discarded, with the job still green. `gates.load` raises FileNotFoundError
    on both backends for exactly this, and `_load` names the likely cause.
    """
    module = _stage_module()
    monkeypatch.setattr(module.gates, "_STATE_DIR", tmp_path)

    with pytest.raises(SystemExit) as caught:
        module._load("00000000-0000-4000-8000-000000000000")

    message = str(caught.value)
    assert "no state file" in message, (
        f"the refusal does not say what was missing. "
        f"`tests/test_run_pipeline_workflow.py:1381` asserts on this exact "
        f"phrase, so it is a cross-file dependency rather than a wording "
        f"preference: {message!r}"
    )
    assert "artifact" in message, (
        f"the refusal does not name the likely cause -- a broken artifact "
        f"handoff -- so an operator reading it has nowhere to start: {message!r}"
    )
    assert "new run" in message or "cannot start" in message, (
        f"the refusal does not say that starting a fresh run is what it is "
        f"refusing to do: {message!r}"
    )


def test_the_absent_run_refusal_also_holds_on_the_dynamodb_backend(monkeypatch):
    """Both backends raise FileNotFoundError for an absent run; both must exit.

    A fix that caught `FileNotFoundError` only where the local backend raises it
    would leave the cloud path returning something for a run that does not
    exist.
    """
    module = _stage_module()
    monkeypatch.setattr(config, "STATE_BACKEND", "dynamodb")
    monkeypatch.setattr(module.log, "read_state", lambda run_id: None)

    with pytest.raises(SystemExit):
        module._load("00000000-0000-4000-8000-000000000000")


def test_the_docstring_no_longer_claims_this_is_broken():
    """The prose is part of the fix, because it is what the next reader trusts.

    The old docstring said this function RAISES on the dynamodb backend and
    called it KNOWN DEBT deliberately not fixed. Leaving that would send the next
    person to re-derive a limitation that no longer exists.

    Asserted on the CLAIM, not on the words: CLAUDE.md's standing rule is that a
    corrected claim is stated rather than quietly deleted, so the docstring
    legitimately still contains the phrase "KNOWN DEBT" while recording what that
    claim used to say. A substring check would therefore forbid the honest form
    and reward silent deletion. What must be gone is the assertion that the fix
    has NOT been made.
    """
    module = _stage_module()
    doc = module._load.__doc__ or ""
    assert "gates.load" in doc, (
        "the docstring does not say what this function now reads through"
    )
    assert "deliberately not fixed" not in doc, (
        "the docstring still says the dynamodb path is deliberately unfixed"
    )
    assert "It is fixed now" in doc or "is fixed now" in doc, (
        "the docstring records the old limitation without stating that it has "
        "been fixed, so a reader cannot tell which claim is current"
    )


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    """All THREE directory seams `_awaiting` reads, redirected at tmp_path.

    `gates._STATE_DIR`, `log._LOG_DIR` and `approve_server._RUNS` each resolve to
    <repo>/runs independently -- patching fewer than three leaves the listing
    reading the real directory, which holds ~10k files from every previous test
    run and would make any assertion about "which runs are awaiting" meaningless.
    """
    monkeypatch.setattr(gates, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(log, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(approve_server, "_RUNS", tmp_path)


def _no_comments(module, monkeypatch):
    monkeypatch.setattr(module.github_ops, "post_comment",
                        lambda state, body, finding=None: "local://x")


def test_the_marker_constant_is_what_gates_pause_actually_writes():
    """The anti-vacuity check for every assertion below.

    If `_PAUSE_MARKER` no longer appeared in what `gates.pause` writes, every
    test in this file would look for a sentence nobody writes, match nothing, and
    pass. That is this repo's recorded failure shape: a matcher that can match
    nothing must assert that it matched.
    """
    state = RunState(ticket_id="MARK-1", ticket_text=TICKET)
    gates.pause(state, "gate1")
    summaries = [e.summary for e in log.read(state.run_id)]
    assert any(_MARKER in summary for summary in summaries), (
        f"approve_server._PAUSE_MARKER ({_MARKER!r}) does not appear in what "
        f"gates.pause writes: {summaries}. Every assertion in this file would "
        f"then match nothing and pass."
    )


def test_the_cloud_plan_stage_leaves_a_run_the_approval_screen_can_FIND(monkeypatch):
    """The defect: no cloud run was ever visible to the approval screen.

    Asserted through `approve_server._awaiting()` rather than by grepping the log
    for the marker, because the marker alone is not the property that matters --
    `_awaiting` also requires the run to be non-terminal and the gate to have no
    decision yet, and a marker written in the wrong place satisfies the grep
    while still never listing the run.
    """
    module = _stage_module()
    _no_comments(module, monkeypatch)

    rc = module.STAGES["plan"](_args(ticket_id="PAUSE-1", ticket_text=TICKET))
    assert rc == module.EXIT_OK

    awaiting, unreadable = approve_server._awaiting()
    assert not unreadable, f"{unreadable} run(s) were unreadable"
    assert awaiting, (
        "after the cloud `plan` stage, approve_server._awaiting() is EMPTY -- so "
        "the run is invisible to the approval screen, which is the seam a "
        "frontend reads. gates.pause was never called on this path."
    )
    assert len(awaiting) == 1, f"expected exactly one awaiting run, got {awaiting}"
    [(_run_id, open_gates)] = awaiting.items()
    assert open_gates == ["gate1"], (
        f"the run is listed as awaiting {open_gates}, not gate1 -- the gate it "
        f"is actually held at"
    )


@pytest.mark.parametrize(
    ("stage_before", "expected_gate"),
    [("plan", "gate1"), ("develop", "gate2"), ("sre", "gate3")],
)
def test_every_gate_gets_a_pause_marker_from_the_stage_before_it(
        monkeypatch, stage_before, expected_gate):
    """All three gates, not just the first.

    Parametrised over the whole chain because one gate working proves nothing
    about the others -- and `_GATE_AFTER` is derived from `STAGE_CHAIN`, so this
    is also what pins that derivation to the gates it is supposed to cover.
    """
    module = _stage_module()
    _no_comments(module, monkeypatch)
    assert module._GATE_AFTER[stage_before] == expected_gate, (
        f"_GATE_AFTER[{stage_before!r}] is "
        f"{module._GATE_AFTER[stage_before]!r}, not {expected_gate!r}"
    )

    module.STAGES["plan"](_args(ticket_id="PAUSE-1", ticket_text=TICKET))
    run_id = next(p.stem.removesuffix(".state")
                  for p in Path(gates._STATE_DIR).glob("*.state.json"))

    # Walk to the stage under test, approving each gate on the way. `plan` has
    # already run, so its case needs no further stages -- without this guard the
    # loop walked the whole chain to `promote` and the run was no longer waiting
    # at anything.
    if stage_before != "plan":
        for stage in module.STAGE_CHAIN[1:]:
            rc = module.STAGES[stage](_args(run_id=run_id))
            assert rc == module.EXIT_OK, f"{stage} exited {rc}"
            if stage == stage_before:
                break

    awaiting, _ = approve_server._awaiting()
    assert run_id in awaiting, (
        f"after the `{stage_before}` stage the run is not listed as awaiting "
        f"anything, so {expected_gate} is invisible to the approval screen"
    )
    assert awaiting[run_id] == [expected_gate], (
        f"the run is listed as awaiting {awaiting[run_id]}, not "
        f"[{expected_gate!r}]"
    )


def test_an_APPROVED_gate_stops_being_listed(monkeypatch):
    """`paused - decided`, from the other side.

    A marker that is never cleared would leave every run on the screen forever,
    asking humans to decide things already decided. The decision is what removes
    it, and `_stage_gate` records that through `gates.resume`.
    """
    module = _stage_module()
    _no_comments(module, monkeypatch)

    module.STAGES["plan"](_args(ticket_id="PAUSE-1", ticket_text=TICKET))
    run_id = next(p.stem.removesuffix(".state")
                  for p in Path(gates._STATE_DIR).glob("*.state.json"))
    assert run_id in approve_server._awaiting()[0], "gate1 was never listed"

    module.STAGES["gate1"](_args(run_id=run_id))

    awaiting, _ = approve_server._awaiting()
    assert awaiting.get(run_id, []) == [], (
        f"after gate1 was approved the run is still listed as awaiting "
        f"{awaiting.get(run_id)}, so the screen asks for a decision that has "
        f"already been made"
    )


def test_the_pause_does_not_write_the_state_TWICE(monkeypatch):
    """`gates.pause` calls `save` itself, so `_emit` must not do both.

    Harmless on the local backend -- the second write is byte-identical -- but on
    dynamodb it is a second PutItem, and either way it misrepresents how many
    writers this state has. `gates.py:37` is explicit that one writer is the
    design.
    """
    module = _stage_module()
    _no_comments(module, monkeypatch)

    saves: list[str] = []
    real_save = gates.save

    def _counting_save(state):
        saves.append(state.run_id)
        return real_save(state)

    monkeypatch.setattr(module.gates, "save", _counting_save)

    module.STAGES["plan"](_args(ticket_id="PAUSE-1", ticket_text=TICKET))

    assert len(saves) == 1, (
        f"the plan stage wrote the state {len(saves)} times. gates.pause already "
        f"calls save, so `_emit` must route through pause INSTEAD of calling "
        f"save as well."
    )


def test_a_terminal_cloud_run_is_not_listed_as_awaiting(monkeypatch):
    """A blocked run must not appear on the approval screen.

    The poisoned demo's run ends at `develop` with `status=blocked`, and the two
    exits before the pause in that stage do not write a marker. A run offering a
    gate2 decision on a change the block rule stopped is the gate being asked to
    undo the one thing this pipeline exists to demonstrate.
    """
    module = _stage_module()
    _no_comments(module, monkeypatch)

    module.STAGES["plan"](_args(ticket_id="PAUSE-1", ticket_text=TICKET))
    run_id = next(p.stem.removesuffix(".state")
                  for p in Path(gates._STATE_DIR).glob("*.state.json"))
    module.STAGES["gate1"](_args(run_id=run_id))
    rc = module.STAGES["develop"](_args(run_id=run_id, poisoned="true"))
    assert rc == module.EXIT_BLOCKED, (
        f"the poisoned run exited {rc}, not EXIT_BLOCKED; this test needs the "
        f"blocked exit and is otherwise pinning nothing"
    )

    awaiting, _ = approve_server._awaiting()
    assert run_id not in awaiting, (
        f"a BLOCKED run is listed as awaiting {awaiting.get(run_id)}. The "
        f"approval screen is offering a human the chance to approve a change the "
        f"deterministic block rule stopped."
    )


def test_the_local_path_still_writes_the_marker():
    """graph.py's two `gates.pause` calls, so the cloud fix cannot regress them.

    Both paths must be visible to the same screen, and the local one was already
    correct -- this is the control that says so, and would catch a "unification"
    that moved the pause and broke the working half.
    """
    from agentorg import graph

    final = graph.run_pipeline("CLEAN-1", TICKET)
    paused = {e.stage for e in log.read(final.run_id)
              if e.action == "opened" and _MARKER in e.summary}
    assert paused == {"gate1", "gate2", "gate3"}, (
        f"graph.py wrote pause markers for {sorted(paused)}, not all three gates"
    )
    decided = {d.gate for d in final.decisions}
    assert paused == decided, (
        f"markers {sorted(paused)} and decisions {sorted(decided)} disagree, so "
        f"a completed local run would be listed as still awaiting something"
    )
