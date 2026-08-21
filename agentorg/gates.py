"""Human approval gates — pause the graph, save state, resume after a decision.

OWNER: Sorour.

There are three gates in the pipeline:
    gate1  after PLAN     — "is this the right plan?"
    gate2  after SECURITY — "the scanners passed; ship it?" (auto-blocks on a block verdict)
    gate3  after SRE      — "final go/no-go to promote"

How pause/resume works without a running server: when the graph reaches a gate
it writes the RunState to the state backend and returns control. A human (CLI or
UI) records a HumanDecision; resume() reloads the state, appends the decision,
and the graph continues from where it stopped. This is why gates never need a
live process babysitting them.

That record is only trustworthy if it is also written at the END of a run, which
is why save() is public and run_pipeline calls it as it exits. Before it did,
every finished run still read status="running" with its last decision missing —
so a run the graph had REJECTED could be resumed and approved, because nothing
on disk said it was over.

TWO STORAGE BACKENDS, AS IN log.py. `config.STATE_BACKEND` chooses a
`runs/<run_id>.state.json` file (`local`, the default) or an item in the
DynamoDB table (`dynamodb`). save() and resume() behave identically on the local
path -- same file, same bytes, same return value -- because that is the path the
suite and the judged demo run on.

WHAT CHANGED FOR THE CALLER, AND IT IS ONLY THIS: save() and pause() used to
return a `pathlib.Path`. There is no path when the state is a DynamoDB item, so
they return a `StateRef` -- a value that knows where the state went and formats
itself as one line a human can act on. `graph.py` prints it, so `str()` is the
whole of its contract; it is deliberately NOT a Path subclass, because the
useful failure is a caller doing path arithmetic on it and being told so, not
one silently building `runs/<id>.state.json/..` against a table.
"""

import json
import pathlib
from dataclasses import dataclass

from . import log
from .common import config
from .state import HumanDecision, LogEvent, RunState

_STATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "runs"


@dataclass(frozen=True)
class StateRef:
    """WHERE a run's state was written, without promising it is a file.

    Opaque on purpose. The old `pathlib.Path` return was read by `graph.py`,
    which prints it, and `scripts/run_stage.py`, which prints it too -- but a
    Path also supports `.read_text()`, `/`, `.parent` and `.exists()`, and every
    one of those is a way for a caller to reach around this module to the local
    filesystem. On the dynamodb backend there is nothing for them to reach, so
    the type says so rather than raising four different AttributeErrors later.

    `read_text()` is the ONE Path-shaped method kept, and it is kept because its
    meaning survives the abstraction: "hand me back the state document you just
    wrote" is answerable by both backends, where `.parent` and `.exists()` are
    not. tests/test_gates_cli.py reads
    `RunState.model_validate_json(ref.read_text())` off this return value to
    assert what save() wrote, and that assertion is just as true against a table
    as against a file -- so it keeps working, unedited, on either backend.

    `path` is populated on the local backend only, and is None on the other. A
    caller that genuinely needs a filesystem path can ask for it and get an
    honest None instead of a string that names nothing.

    __str__ IS THE PROJECTOR CONTRACT. graph.py prints
    `[gate1] paused. state saved -> {ref}`, and that line is on screen during a
    judged demo, so it has to name something a human can act on: a real path on
    the local backend, and `dynamodb://<table>/<run_id>` -- table and run id,
    which is exactly what `aws dynamodb get-item` needs -- on the other.
    """

    backend: str
    run_id: str
    path: pathlib.Path | None = None
    table: str | None = None

    def __str__(self) -> str:
        if self.path is not None:
            return str(self.path)
        return f"{self.backend}://{self.table}/{self.run_id}"

    def read_text(self) -> str:
        """The serialized RunState this ref points at, from either backend.

        Deliberately NOT a convenience re-export of `pathlib.Path.read_text`:
        on the dynamodb backend it is a GetItem, and it raises
        FileNotFoundError for an absent run on both, exactly as `load()` does.
        The name matches Path's because the callers that had a Path are asking
        the same question they always were.
        """
        if self.path is not None:
            return self.path.read_text()
        document = log.read_state(self.run_id)
        if document is None:
            raise FileNotFoundError(
                f"no state item for run {self.run_id!r} in {self.table!r}"
            )
        return document


def _state_path(run_id: str) -> pathlib.Path:
    """The local backend's file for a run. Validated, as log._path is.

    Kept public-ish (underscored, but read by scripts/run_stage.py and three test
    files) because it was already, and because on the local backend it remains
    the honest answer to "where is this run". It raises on the dynamodb backend
    rather than returning a path that names nothing -- a caller that needs the
    location in a backend-agnostic way wants the StateRef save() hands back.
    """
    if config.STATE_BACKEND != config.STATE_BACKEND_LOCAL:
        raise RuntimeError(
            f"there is no state FILE on the {config.STATE_BACKEND!r} backend; "
            f"the run's state is an item in {config.STATE_TABLE!r}. Read it with "
            f"gates.load(run_id), or use the StateRef that save()/pause() return."
        )
    _STATE_DIR.mkdir(exist_ok=True)
    return _STATE_DIR / f"{log._require_safe_run_id(run_id)}.state.json"


def save(state: RunState) -> StateRef:
    """The one place a RunState is serialized. Everything that writes goes here.

    Three callers: pause() before a gate, resume() after a decision, and the
    graph as it finishes. One writer rather than three on purpose — the record is
    the handoff between a graph that has stopped and a human who has not decided
    yet, so every extra copy of this line is another chance for the two halves
    to disagree about the format.

    Public rather than _private because the graph is one of those callers, and a
    module reaching into another module's underscore is how a single writer
    quietly becomes two.

    The serialization itself is backend-independent -- `json.dumps(model_dump())`
    either way -- so a run written by one backend can be read by the other. The
    `indent=2` is kept on the local path because that file is read by humans and
    every run already on disk has it.
    """
    if config.STATE_BACKEND == config.STATE_BACKEND_LOCAL:
        path = _state_path(state.run_id)
        path.write_text(json.dumps(state.model_dump(), indent=2))
        return StateRef(backend=config.STATE_BACKEND, run_id=state.run_id, path=path)

    log.write_state(state.run_id, json.dumps(state.model_dump(), indent=2))
    return StateRef(backend=config.STATE_BACKEND, run_id=state.run_id,
                    table=config.STATE_TABLE)


def load(run_id: str) -> RunState:
    """The paused RunState for a run, from whichever backend holds it.

    The read half of save(), and the only reader in this module. Raises
    FileNotFoundError for an absent run on BOTH backends -- deliberately the same
    exception, because callers already handle it and "no such run" is one
    condition however the storage spells it. scripts/run_stage.py turns it into a
    named SystemExit about a broken artifact handoff; do not soften it into a
    fresh RunState, which would start a new run and report success for work it
    invented.
    """
    if config.STATE_BACKEND == config.STATE_BACKEND_LOCAL:
        return RunState.model_validate_json(_state_path(run_id).read_text())

    document = log.read_state(run_id)
    if document is None:
        raise FileNotFoundError(
            f"no state item for run {run_id!r} in {config.STATE_TABLE!r}"
        )
    return RunState.model_validate_json(document)


def pause(state: RunState, gate: str) -> StateRef:
    """Persist state at a gate and return a ref a human/UI reads."""
    ref = save(state)
    log.append(LogEvent(
        run_id=state.run_id, ticket_id=state.ticket_id,
        actor="system", stage=gate, action="opened",
        summary=f"paused at {gate} awaiting human decision",
    ))
    return ref


def resume(run_id: str, decision: HumanDecision) -> RunState:
    """Reload paused state, record the decision, and hand it back to the graph.

    The decision is written back before returning, so the next call reads a
    state that includes it. That matters because a UI decides one gate per
    click: without the write-back, resume() reloaded the same untouched record
    every time and each decision silently replaced the one before it — measured
    as two sequential approvals returning only the second, with the file on disk
    still holding none. The log kept the history, but nothing could be RESUMED
    from it, which is the one job this file has.
    """
    state = load(run_id)
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
