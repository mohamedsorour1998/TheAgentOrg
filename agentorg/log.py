"""Append-only decision log — one row per event. Never update, never delete.

OWNER: Sorour.

Every stage of the graph calls append() as it acts. The log is the source of
truth the timeline UI renders and the judges score for UX. Because it is
append-only, the full history of a run is always reconstructable.

Storage is a JSONL file per run (one JSON object per line). Swapping this for
DynamoDB later means changing only the two lines marked below — the append()
and read() signatures stay identical, so nothing else in the codebase breaks.
"""

import json
import pathlib

from .state import LogEvent

_LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "runs"


def _path(run_id: str) -> pathlib.Path:
    _LOG_DIR.mkdir(exist_ok=True)
    return _LOG_DIR / f"{run_id}.jsonl"


def append(event: LogEvent) -> LogEvent:
    """Append one event to the run's log. Returns the event for chaining."""
    # <-- swap this line for a DynamoDB PutItem to move off local storage.
    with _path(event.run_id).open("a") as fh:
        fh.write(json.dumps(event.model_dump()) + "\n")
    return event


def read(run_id: str) -> list[LogEvent]:
    """Read the full ordered event history for a run."""
    path = _path(run_id)
    if not path.exists():
        return []
    # <-- swap this line for a DynamoDB Query to move off local storage.
    return [LogEvent.model_validate_json(line) for line in path.read_text().splitlines()]
