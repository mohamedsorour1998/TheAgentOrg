"""Append-only decision log — one row per event. Never update, never delete.

OWNER: Sorour.

Every stage of the graph calls append() as it acts. The log is the source of
truth the timeline UI renders and the judges score for UX. Because it is
append-only, the full history of a run is always reconstructable.

TWO STORAGE BACKENDS, ONE SIGNATURE. `config.STATE_BACKEND` chooses between a
JSONL file per run (`local`, the default) and a DynamoDB table (`dynamodb`).
append() and read() take and return exactly what they always did -- that promise
was written here before either backend existed and it is what let gates.py,
graph.py, timeline.py, approve_server.py, scripts/run_stage.py and
tests/dora_runner.py keep calling this module unchanged.

THE LOCAL BRANCH IS THE DEFAULT AND IS THE TESTED ONE. It is first in every
function below and returns early, so the `dynamodb` code cannot change what a
local run does. The judged demo runs on `local`; this table buys durability for
a pipeline running in a container, which is a different problem from the demo.

=========================================================================
THE TABLE, AND WHY IT IS ONE TABLE WITH THREE KINDS OF ROW
=========================================================================

`theagentorg-runs`, partition key `run_id`, sort key `ts_event_id`. One
partition per run, so a run's whole history is one Query:

  run_id            ts_event_id                     what it is
  ----------------  ------------------------------  -----------------------
  <a run id>        "<iso ts>#<event uuid>"         one logged event
  <a run id>        "state#current"                 the paused RunState
  "__runs__"        "<a run id>"                    the run INDEX (see below)

The sort key for an event is `ts#event_id`, so a Query returns a run's events in
chronological order without sorting them here -- the same order the JSONL file
gives by construction. `event_id` is in the key because `ts` alone is not
unique: two events written in the same microsecond would otherwise be one row,
and PutItem would silently replace the first with the second. An append-only log
that can drop a row is not append-only.

`state#current` cannot collide with an event: every `ts` begins with a four-digit
year, so no event sort key can begin with "s". Pinned by a test rather than left
to that reasoning.

THE RUN INDEX EXISTS BECAUSE `Scan` IS NOT GRANTED, DELIBERATELY. The approve
screen has to enumerate runs, which in DynamoDB means either a Scan or a second
index -- and the IAM grant in infra/Terraform/modules/state/ is exactly PutItem,
Query, GetItem and UpdateItem on this one table. So the index is a reserved
PARTITION rather than a new permission: gates.save() touches `("__runs__",
run_id)` on every write, and listing runs is a Query of that one partition with
the actions already granted. `__runs__` is refused as a real run id by
`is_safe_run_id` below, so a run cannot be named into its own index.

=========================================================================
NOTHING HERE TRUSTS A run_id IT DID NOT VALIDATE
=========================================================================

`is_safe_run_id` is in this module, not in approve_server, because the local
backend's own guarantee used to be geometric: a run id came out of
`pathlib.Path.glob`, so it was a real directory entry and could not be `../..`
anything. A DynamoDB Query has no such property -- it returns whatever bytes are
in the table -- so the containment that was implied by the glob is stated here
and applied to BOTH backends' listings. See agentorg/approve_server.py, whose
path-traversal defence this now is.
"""

import json
import logging
import pathlib

from .common import config
from .state import LogEvent

_LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "runs"

# The table's key attributes. `ts_event_id` HOLDS `ts#event_id` -- the value is
# composed, the attribute name is not, because `#` starts an expression
# attribute name in DynamoDB's expression language and a key called `ts#event_id`
# could not be written in a KeyConditionExpression without escaping it.
PARTITION_KEY = "run_id"
SORT_KEY = "ts_event_id"

# The sort key of a run's state document. Not a `ts#event_id`, and it cannot be
# mistaken for one: an event's sort key starts with the year.
STATE_SORT_KEY = "state#current"

# The reserved partition holding one item per run. See the module docstring for
# why this is a partition rather than a Scan or a GSI.
RUN_INDEX_PARTITION = "__runs__"

# A run id longer than this is refused. DynamoDB caps a partition key at 2048
# bytes and most filesystems cap a component at 255, so the smaller limit is the
# real one -- and a 5000-character run id is an attack, not a run.
MAX_RUN_ID_LENGTH = 200

# Characters that would let a run id leave the directory (or the partition) it is
# supposed to name. `/` and `\` are path separators; `:` is a Windows
# drive-relative prefix; NUL cannot even be turned into a path -- `pathlib`
# raises ValueError on it, which is a traceback rather than a refusal.
_UNSAFE_IN_RUN_ID = ("/", "\\", ":", "\x00")


def is_safe_run_id(run_id: str) -> bool:
    """Whether `run_id` may be used to build a path or a partition key.

    THIS IS THE PATH-TRAVERSAL DEFENCE, and it is a positive test for "one safe
    component", not a blacklist of known-bad strings. `agentorg/gates.py`'s
    `_state_path` does no containment check of its own: with `../../etc/passwd`
    it resolves outside runs/ entirely (verified), and until this function
    existed the only thing standing in the way was that approve_server's run ids
    came out of a glob and were therefore real filenames.

    IT DELIBERATELY DOES NOT REJECT MARKUP. `<img src=x onerror=alert(1)>` is a
    perfectly safe FILENAME and a perfectly dangerous thing to interpolate into
    HTML, and those are two different problems with two different fixes: this one
    keeps the write inside runs/, and `html.escape` at the point of use keeps the
    render inert. Conflating them would move an escaping bug's fix into a
    validator, where the next unescaped interpolation would not be caught.
    Pinned by tests in tests/test_approve_server.py that require such a run to
    still be LISTED.
    """
    return bool(
        run_id
        and len(run_id) <= MAX_RUN_ID_LENGTH
        and run_id not in (".", "..", RUN_INDEX_PARTITION)
        and not any(bad in run_id for bad in _UNSAFE_IN_RUN_ID)
    )


def _require_safe_run_id(run_id: str) -> str:
    """`run_id`, or a refusal naming why. Every write goes through here."""
    if not is_safe_run_id(run_id):
        raise ValueError(
            f"unsafe run id (length {len(run_id)}): a run id is one path "
            f"component and one partition key, so it may not be empty, be "
            f"longer than {MAX_RUN_ID_LENGTH} characters, be '.' or '..', be "
            f"the reserved {RUN_INDEX_PARTITION!r} partition, or contain any of "
            f"{' '.join(map(repr, _UNSAFE_IN_RUN_ID))}. The value is not echoed: "
            f"it is untrusted input and this message can reach a rendered page."
        )
    return run_id


def _path(run_id: str) -> pathlib.Path:
    _LOG_DIR.mkdir(exist_ok=True)
    return _LOG_DIR / f"{_require_safe_run_id(run_id)}.jsonl"


def _table():
    """The DynamoDB table. The one seam every dynamodb test replaces.

    Lazy `import boto3`, following common/agent_client._agentcore_control_client
    and github_ops._agentcore_client: a module-level import would make every
    `import agentorg.log` -- including CI's, which never makes an AWS call and
    runs entirely on the local backend -- pay for botocore's import.

    The RESOURCE interface, not the client one, because its four methods map
    one-to-one onto the four IAM actions granted in
    infra/Terraform/modules/state/: put_item/PutItem, query/Query,
    get_item/GetItem, update_item/UpdateItem. Nothing here can reach a fifth.
    """
    import boto3

    return boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(
        config.STATE_TABLE
    )


def _sort_key(event: LogEvent) -> str:
    """`ts#event_id` for one event. One definition, two readers."""
    return f"{event.ts}#{event.event_id}"


def append(event: LogEvent) -> LogEvent:
    """Append one event to the run's log. Returns the event for chaining."""
    if config.STATE_BACKEND == config.STATE_BACKEND_LOCAL:
        with _path(event.run_id).open("a") as fh:
            fh.write(json.dumps(event.model_dump()) + "\n")
        return event

    # The DynamoDB PutItem this module was designed around. `payload` carries the
    # whole event as JSON rather than one attribute per field, for the same
    # reason the local branch writes a whole line: LogEvent is a pydantic model
    # with a growing set of optional fields (`scan_provenance` arrived in week
    # 3), and a per-attribute mapping is a second declaration of that model, free
    # to drift. The two key attributes are the only thing spread out, because
    # they are the only thing DynamoDB itself has to read.
    _table().put_item(Item={
        PARTITION_KEY: _require_safe_run_id(event.run_id),
        SORT_KEY: _sort_key(event),
        "payload": event.model_dump_json(),
    })
    return event


def read(run_id: str) -> list[LogEvent]:
    """Read the full ordered event history for a run."""
    if config.STATE_BACKEND == config.STATE_BACKEND_LOCAL:
        path = _path(run_id)
        if not path.exists():
            return []
        return [LogEvent.model_validate_json(line) for line in path.read_text().splitlines()]

    events: list[LogEvent] = []
    for item in _query(_require_safe_run_id(run_id)):
        # The state document shares this partition and is not an event. Filtered
        # on the sort key rather than on the presence of a `payload` attribute,
        # so a state row can never be validated as a LogEvent.
        if item.get(SORT_KEY) != STATE_SORT_KEY:
            events.append(LogEvent.model_validate_json(item["payload"]))
    return events


def _query(partition: str) -> list[dict]:
    """Every item in one partition, in sort-key order, ACROSS ALL PAGES.

    PAGINATED, and that is not defensive coding. DynamoDB caps a Query response
    at 1 MB and returns `LastEvaluatedKey` when more remains, so a run with a
    long history answers in pages -- and a reader that takes only the first page
    returns a TRUNCATED log while looking exactly like a complete one. The
    timeline renders the last event as the run's outcome (agentorg/timeline.py's
    `_outcome`), so a dropped final page would report a promoted run as
    INCOMPLETE, or a blocked one as whatever it did last before the cut.

    The same trap is already recorded in common/agent_client._resolve_arn for
    list_agent_runtimes and in github_ops:503.
    """
    table = _table()
    items: list[dict] = []
    start_key = None
    while True:
        kwargs = {
            "KeyConditionExpression": "#pk = :pk",
            "ExpressionAttributeNames": {"#pk": PARTITION_KEY},
            "ExpressionAttributeValues": {":pk": partition},
        }
        if start_key is not None:
            kwargs["ExclusiveStartKey"] = start_key
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def write_state(run_id: str, document: str) -> None:
    """Store a run's serialized RunState, and index the run. dynamodb only.

    TWO WRITES, and the second is the reason the approve screen works at all.
    The state document goes in the run's own partition; the run id also gets an
    item in the reserved index partition, because enumerating runs otherwise
    needs `dynamodb:Scan` and that action is deliberately not granted.

    `update_item` for the index entry rather than `put_item`: it is an upsert of
    one bookkeeping attribute that every save re-touches, and a full PutItem
    would rewrite an item this module does not otherwise own.
    """
    safe = _require_safe_run_id(run_id)
    table = _table()
    table.put_item(Item={
        PARTITION_KEY: safe,
        SORT_KEY: STATE_SORT_KEY,
        "payload": document,
    })
    table.update_item(
        Key={PARTITION_KEY: RUN_INDEX_PARTITION, SORT_KEY: safe},
        UpdateExpression="SET #seen = :seen",
        ExpressionAttributeNames={"#seen": "last_saved_at"},
        ExpressionAttributeValues={":seen": _document_ts(document)},
    )


def _document_ts(document: str) -> str:
    """A timestamp for the index entry, read out of the state document.

    Read from the document rather than taken from the clock so this function
    stays deterministic and so the index cannot disagree with the state it
    indexes. An unparseable document still gets an index entry -- being able to
    LIST a corrupt run is what lets approve_server count it as unreadable
    instead of showing an empty queue.
    """
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        logging.getLogger(__name__).warning(
            "state document is not JSON; indexing the run without a timestamp",
            exc_info=True)
        return ""
    return parsed.get("started_at", "") if isinstance(parsed, dict) else ""


def read_state(run_id: str) -> str | None:
    """A run's serialized RunState, or None if the run has none. dynamodb only."""
    item = _table().get_item(Key={
        PARTITION_KEY: _require_safe_run_id(run_id),
        SORT_KEY: STATE_SORT_KEY,
    }).get("Item")
    return item.get("payload") if item else None


def list_indexed_run_ids() -> list[str]:
    """Every run id in the reserved index partition. dynamodb only.

    Raw, UNVALIDATED, and deliberately so: this returns what the table actually
    holds. The caller filters with `is_safe_run_id` and REPORTS what it dropped
    -- see agentorg/approve_server.py._run_ids. Filtering here instead would make
    "the table holds a malformed id" and "the table holds nothing" the same
    answer, which is the conflation this codebase exists to prevent.

    There is no local equivalent in this module on purpose. On the local backend
    a run is discovered by globbing the state directory, and that directory
    belongs to gates.py and approve_server.py, which own their own constants for
    it.
    """
    return [str(item.get(SORT_KEY, "")) for item in _query(RUN_INDEX_PARTITION)]
