"""Human approval gates — pause the graph, save state, resume after a decision.

OWNER: Sorour.

There are three gates in the pipeline:
    gate1  after PLAN     — "is this the right plan?"
    gate2  after SECURITY — "the scanners passed; ship it?" (auto-blocks on a block verdict)
    gate3  after SRE      — "final go/no-go to promote"

How pause/resume works without a running server: when the graph reaches a gate
it writes the RunState to runs/<run_id>.state.json and returns control. A human
(CLI or UI) records a HumanDecision; resume() reloads the state, appends the
decision, and the graph continues from where it stopped. This is why gates never
need a live process babysitting them.

That file is only trustworthy if it is also written at the END of a run, which
is why save() is public and run_pipeline calls it as it exits. Before it did,
every finished run still read status="running" with its last decision missing —
so a run the graph had REJECTED could be resumed and approved, because nothing
on disk said it was over.
"""

import json
import pathlib

from . import log
from .state import HumanDecision, LogEvent, RunState

_STATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "runs"


def _state_path(run_id: str) -> pathlib.Path:
    _STATE_DIR.mkdir(exist_ok=True)
    return _STATE_DIR / f"{run_id}.state.json"


def save(state: RunState) -> pathlib.Path:
    """The one place a RunState is serialized. Everything that writes goes here.

    Three callers: pause() before a gate, resume() after a decision, and the
    graph as it finishes. One writer rather than three on purpose — the file is
    the handoff between a graph that has stopped and a human who has not decided
    yet, so every extra copy of this line is another chance for the two halves
    to disagree about the format.

    Public rather than _private because the graph is one of those callers, and a
    module reaching into another module's underscore is how a single writer
    quietly becomes two.
    """
    path = _state_path(state.run_id)
    path.write_text(json.dumps(state.model_dump(), indent=2))
    return path


def pause(state: RunState, gate: str) -> pathlib.Path:
    """Persist state at a gate and return the path a human/UI reads."""
    path = save(state)
    log.append(LogEvent(
        run_id=state.run_id, ticket_id=state.ticket_id,
        actor="system", stage=gate, action="opened",
        summary=f"paused at {gate} awaiting human decision",
    ))
    return path


def resume(run_id: str, decision: HumanDecision) -> RunState:
    """Reload paused state, record the decision, and hand it back to the graph.

    The decision is written back before returning, so the next call reads a
    state that includes it. That matters because a UI decides one gate per
    click: without the write-back, resume() reloaded the same untouched file
    every time and each decision silently replaced the one before it — measured
    as two sequential approvals returning only the second, with the file on disk
    still holding none. The log kept the history, but nothing could be RESUMED
    from it, which is the one job this file has.
    """
    path = _state_path(run_id)
    state = RunState.model_validate_json(path.read_text())
    state.decisions.append(decision)
    log.append(LogEvent(
        run_id=state.run_id, ticket_id=state.ticket_id,
        actor="human", stage=decision.gate,
        action=decision.decision if decision.decision != "overridden" else "overridden",
        verdict=decision.decision, summary=decision.reason,
    ))
    if decision.decision == "rejected":
        state.status = "rejected"
    save(state)
    return state
