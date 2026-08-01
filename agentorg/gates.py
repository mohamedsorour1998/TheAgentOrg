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
"""

import json
import pathlib

from .state import RunState, HumanDecision, LogEvent
from . import log

_STATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "runs"


def _state_path(run_id: str) -> pathlib.Path:
    _STATE_DIR.mkdir(exist_ok=True)
    return _STATE_DIR / f"{run_id}.state.json"


def pause(state: RunState, gate: str) -> pathlib.Path:
    """Persist state at a gate and return the path a human/UI reads."""
    path = _state_path(state.run_id)
    path.write_text(json.dumps(state.model_dump(), indent=2))
    log.append(LogEvent(
        run_id=state.run_id, ticket_id=state.ticket_id,
        actor="system", stage=gate, action="opened",
        summary=f"paused at {gate} awaiting human decision",
    ))
    return path


def resume(run_id: str, decision: HumanDecision) -> RunState:
    """Reload paused state, record the decision, and hand it back to the graph."""
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
    return state
