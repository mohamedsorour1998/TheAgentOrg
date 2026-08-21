"""The dynamodb state backend, and the one property that separates it from local.

WHY THIS FILE'S STUB IS A DICT AND NOT A LIST, which is the whole point of it.

`log.append` on the local backend opens a JSONL file in append mode: every call
adds a line, and two identical calls leave two lines. DynamoDB `PutItem` does
not append -- it REPLACES the item at that (partition key, sort key), silently,
returning success either way. So the two backends genuinely differ in behaviour
at exactly one place, and a stub that accumulated writes into a list would model
the local semantics while claiming to test the remote ones. It would pass
against correct code AND against code that loses events, and the loss would be
invisible: one fewer row in a timeline nobody counted.

Four times on this plan a test double produced coverage that could not fail --
a stub that could only emit `json.dumps(...)`, a fixture returning a
byte-identical diff on every call, a comment-stripper that blanked the heredoc
it was pointed at, and a shell stub whose pipeline changed the shape under test.
None was visible by reading the test. So `_FakeTable` below stores items in a
dict keyed on `(PARTITION_KEY, SORT_KEY)`, which is what the real service does,
and `test_two_events_in_the_same_second_both_survive` is the assertion that
would go red if the sort key ever stopped distinguishing them.

WHAT THIS STUB DELIBERATELY CANNOT EXPRESS, stated rather than left implicit:

  * Throttling (`ProvisionedThroughputExceededException`). The table is
    PAY_PER_REQUEST, so there is no provisioned ceiling to exceed, and boto3
    retries throttles internally -- a stub raising one would test botocore's
    retry logic, not this module's.
  * `ConditionalCheckFailedException`. Nothing in log.py or gates.py passes a
    ConditionExpression, so the condition that would fail does not exist. If one
    is ever added, this stub must grow a case for it before that code ships.
  * Eventual consistency. `query` here returns what was written; the real
    service may lag. No code in this repo reads a run it did not just write in
    the same process, so the window is not reachable -- but a future reader that
    polls for another job's write WOULD need this modelled.
  * Item-size and page-size limits in bytes. Pagination is exercised by
    `pages_after`, which splits deterministically instead, because the property
    under test is "the reader follows LastEvaluatedKey", not "1 MB is where the
    cut falls".

Every test here sets STATE_BACKEND explicitly through monkeypatch. The default is
`local` (config.py:159) and the rest of the suite depends on that default, so
nothing in this file may leak a backend to another test.
"""

import json
import pathlib
import re

import pytest

from agentorg import gates, log
from agentorg.common import config
from agentorg.state import LogEvent, RunState


def _event(run_id: str = "run-1", *, ts: str, event_id: str, action: str = "opened") -> LogEvent:
    """A LogEvent with the two key fields pinned, so collisions are constructible."""
    return LogEvent(
        event_id=event_id,
        ts=ts,
        run_id=run_id,
        ticket_id="T-1",
        actor="system",
        stage="plan",
        action=action,
    )


def _state(run_id: str = "run-1") -> RunState:
    return RunState(run_id=run_id, ticket_id="T-1", ticket_text="Add a per-IP login rate limit.")


class _FakeTable:
    """A DynamoDB table with REPLACEMENT semantics, because that is the difference.

    `put_item` overwrites the item at (pk, sk) exactly as the service does, so a
    sort key that fails to distinguish two events loses one here too.

    `pages_after` makes `query` return `LastEvaluatedKey` after that many items,
    so the paginating reader in log._query is exercised rather than assumed.
    """

    def __init__(self, *, pages_after: int | None = None):
        self.items: dict[tuple[str, str], dict] = {}
        self.pages_after = pages_after
        self.calls: list[str] = []

    def put_item(self, *, Item):
        self.calls.append("put_item")
        self.items[(Item[log.PARTITION_KEY], Item[log.SORT_KEY])] = dict(Item)

    def get_item(self, *, Key):
        self.calls.append("get_item")
        item = self.items.get((Key[log.PARTITION_KEY], Key[log.SORT_KEY]))
        return {"Item": dict(item)} if item else {}

    def update_item(self, *, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues):
        self.calls.append("update_item")
        key = (Key[log.PARTITION_KEY], Key[log.SORT_KEY])
        item = self.items.setdefault(key, {
            log.PARTITION_KEY: Key[log.PARTITION_KEY],
            log.SORT_KEY: Key[log.SORT_KEY],
        })
        # Only the one SET shape this module actually issues is honoured; any
        # other expression is refused rather than silently ignored, so a future
        # UpdateExpression cannot pass here while doing nothing.
        assert UpdateExpression == "SET #seen = :seen", UpdateExpression
        item[ExpressionAttributeNames["#seen"]] = ExpressionAttributeValues[":seen"]

    def query(self, **kwargs):
        self.calls.append("query")
        partition = kwargs["ExpressionAttributeValues"][":pk"]
        rows = sorted(
            (dict(item) for (pk, _sk), item in self.items.items() if pk == partition),
            key=lambda item: item[log.SORT_KEY],
        )
        if self.pages_after is None:
            return {"Items": rows}
        start = kwargs.get("ExclusiveStartKey")
        offset = 0
        if start is not None:
            offset = next(
                i + 1 for i, row in enumerate(rows)
                if row[log.SORT_KEY] == start[log.SORT_KEY]
            )
        page = rows[offset:offset + self.pages_after]
        response = {"Items": page}
        if offset + self.pages_after < len(rows):
            last = page[-1]
            response["LastEvaluatedKey"] = {
                log.PARTITION_KEY: last[log.PARTITION_KEY],
                log.SORT_KEY: last[log.SORT_KEY],
            }
        return response


@pytest.fixture()
def table(monkeypatch):
    """STATE_BACKEND=dynamodb against a fake table. Never touches AWS."""
    fake = _FakeTable()
    monkeypatch.setattr(config, "STATE_BACKEND", config.STATE_BACKEND_DYNAMODB)
    monkeypatch.setattr(log, "_table", lambda: fake)
    return fake


# --------------------------------------------------------------------------
# THE COLLISION PROPERTY. This is the test the rest of the file exists for.
# --------------------------------------------------------------------------


def test_two_events_in_the_same_second_both_survive(table):
    """PutItem REPLACES, so the sort key is the only thing keeping two events apart.

    `ts#event_id`, not `ts`. Two events written in the same clock tick share a
    timestamp -- ordinary for a stage that logs twice in a row -- and if the sort
    key were the timestamp alone, the second PutItem would overwrite the first
    and the log would be one row short with nothing raised.

    Asserts the COUNT and both event ids, because a count alone would still pass
    if one event were written twice.
    """
    first = _event(ts="2026-08-21T12:00:00+00:00", event_id="aaa", action="opened")
    second = _event(ts="2026-08-21T12:00:00+00:00", event_id="bbb", action="proposed")
    log.append(first)
    log.append(second)

    got = log.read("run-1")
    assert len(got) == 2, (
        f"expected both events, got {len(got)}: a PutItem overwrote the other, so "
        f"the sort key does not distinguish two events sharing a timestamp"
    )
    assert {e.event_id for e in got} == {"aaa", "bbb"}
    assert [e.action for e in got] == ["opened", "proposed"], (
        "events came back out of write order; the sort key must order them"
    )


def test_the_sort_key_carries_both_the_timestamp_and_the_event_id(table):
    """Pins the composition directly, so the collision test cannot be the only guard.

    The test above fails if the sort key stops distinguishing events. This one
    says WHY in one assertion, so a reader who breaks it is told what the key is
    for rather than only that two events collided.
    """
    event = _event(ts="2026-08-21T12:00:00+00:00", event_id="aaa")
    log.append(event)
    keys = [sk for (_pk, sk) in table.items]
    assert keys, "nothing was written, so this test would pass vacuously"
    assert keys == [f"{event.ts}#{event.event_id}"], (
        f"sort key is {keys!r}; it must be ts#event_id, or two events sharing a "
        f"timestamp overwrite each other"
    )


def test_the_event_count_round_trips_through_the_backend(table):
    """The brief's named RED-step property: write N, read N back."""
    for i in range(5):
        log.append(_event(ts=f"2026-08-21T12:00:0{i}+00:00", event_id=f"e{i}"))
    assert len(log.read("run-1")) == 5


def test_a_long_history_is_read_across_every_page(monkeypatch):
    """A reader that takes only the first Query page returns a TRUNCATED log.

    DynamoDB caps a Query response and returns LastEvaluatedKey when more
    remains. timeline._outcome reads the LAST event as the run's outcome, so a
    dropped final page reports a promoted run as incomplete -- a wrong verdict
    that looks like a complete answer.
    """
    fake = _FakeTable(pages_after=2)
    monkeypatch.setattr(config, "STATE_BACKEND", config.STATE_BACKEND_DYNAMODB)
    monkeypatch.setattr(log, "_table", lambda: fake)

    for i in range(7):
        log.append(_event(ts=f"2026-08-21T12:00:0{i}+00:00", event_id=f"e{i}"))

    got = log.read("run-1")
    assert len(got) == 7, (
        f"read {len(got)} of 7 events: the reader stopped at a page boundary "
        f"instead of following LastEvaluatedKey"
    )
    assert got[-1].event_id == "e6", "the final page was dropped"
    assert fake.calls.count("query") > 1, (
        "only one query was issued, so pagination was never exercised and this "
        "test proves nothing about multi-page reads"
    )


def test_the_state_document_is_never_returned_as_an_event(table):
    """The state row shares the run's partition and is not a LogEvent.

    Filtered on the sort key rather than on the presence of a `payload`
    attribute -- both rows have one -- so a state document can never be
    validated as an event.
    """
    log.append(_event(ts="2026-08-21T12:00:00+00:00", event_id="aaa"))
    log.write_state("run-1", json.dumps(_state().model_dump()))

    events = log.read("run-1")
    assert len(events) == 1, (
        f"read {len(events)} events; the state document was returned as one"
    )
    assert events[0].event_id == "aaa"


# --------------------------------------------------------------------------
# gates.save / load / StateRef across both backends
# --------------------------------------------------------------------------


def test_a_run_state_round_trips_through_the_table(table):
    """save() then load() returns the same run, with its decisions intact."""
    state = _state()
    state.revision_count = 2
    state.status = "blocked"
    gates.save(state)

    loaded = gates.load("run-1")
    assert loaded.run_id == "run-1"
    assert loaded.revision_count == 2
    assert loaded.status == "blocked"


def test_an_absent_run_raises_the_same_error_on_both_backends(table):
    """FileNotFoundError, not None and not a fresh RunState.

    scripts/run_stage.py turns this into a named SystemExit about a broken
    artifact handoff. Softening it to a fresh RunState would start a new run and
    report success for work it invented.
    """
    with pytest.raises(FileNotFoundError, match="never-saved"):
        gates.load("never-saved")


def test_the_state_ref_names_the_table_and_run_for_a_human(table):
    """__str__ is the projector contract: graph.py prints this line during a demo.

    `dynamodb://<table>/<run_id>` is exactly what `aws dynamodb get-item` needs,
    so a human watching a paused run can act on what is on screen.
    """
    ref = gates.save(_state())
    rendered = str(ref)
    assert rendered == f"dynamodb://{config.STATE_TABLE}/run-1", rendered
    assert ref.path is None, (
        "path must be None on the dynamodb backend rather than naming a file "
        "that does not exist"
    )


def test_the_state_ref_hands_back_the_document_it_just_wrote(table):
    """read_text() is the one Path-shaped method kept, and it works on both backends."""
    ref = gates.save(_state())
    assert RunState.model_validate_json(ref.read_text()).run_id == "run-1"


def test_reading_a_ref_for_a_vanished_run_raises_rather_than_returning_empty(table):
    ref = gates.save(_state())
    table.items.clear()
    with pytest.raises(FileNotFoundError):
        ref.read_text()


def test_asking_for_a_file_path_on_the_dynamodb_backend_refuses(table):
    """_state_path must raise rather than return a path that names nothing."""
    with pytest.raises(RuntimeError, match="no state FILE"):
        gates._state_path("run-1")


def test_saving_indexes_the_run_so_the_approve_screen_can_find_it(table):
    """Enumerating runs otherwise needs dynamodb:Scan, which is not granted.

    So save() writes a second item into the reserved index partition. Without it
    the approve screen shows an empty queue for a run that is genuinely paused.
    """
    gates.save(_state("run-a"))
    gates.save(_state("run-b"))
    assert sorted(log.list_indexed_run_ids()) == ["run-a", "run-b"]


def test_the_index_entry_is_upserted_not_duplicated(table):
    """Two saves of one run leave ONE index entry, via update_item.

    A full PutItem here would rewrite an item this module does not otherwise own,
    and an append would grow the index without bound.
    """
    gates.save(_state("run-a"))
    gates.save(_state("run-a"))
    assert log.list_indexed_run_ids() == ["run-a"]
    assert "update_item" in table.calls, (
        "the index entry was not written with update_item, so this test is not "
        "exercising the upsert it claims to"
    )


# --------------------------------------------------------------------------
# THE PATH-TRAVERSAL DEFENCE, which the glob used to provide geometrically.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "/etc/passwd",
    "..",
    ".",
    "a/b",
    "back\\slash",
    "colon:name",
    "nul\x00byte",
    log.RUN_INDEX_PARTITION,
    "x" * (log.MAX_RUN_ID_LENGTH + 1),
])
def test_a_hostile_run_id_is_refused_before_anything_is_written(table, hostile):
    """Every write goes through _require_safe_run_id, on BOTH backends.

    This used to be geometric rather than asserted: approve_server discovered
    runs with a glob, so a name it returned was by construction a real entry in
    one directory and `../../etc/passwd` could not come out of it. A Query
    against a table returns whatever bytes were written to it, so the guarantee
    is now stated instead of inherited -- and gates._state_path does no
    containment check of its own.
    """
    with pytest.raises(ValueError):
        log.append(_event(run_id=hostile, ts="2026-08-21T12:00:00+00:00", event_id="aaa"))
    assert not table.items, (
        f"{hostile!r} was refused but something was still written; the refusal "
        f"must happen before the PutItem"
    )


def test_a_percent_encoded_traversal_cannot_survive_form_decoding():
    """Decode BEFORE validating, which is the order approve_server uses.

    is_safe_run_id('..%2fetc') is True on its own, and that is correct: the
    validator's job is to judge a decoded value. approve_server runs parse_qs
    first (which percent-decodes), so the wire value arrives as '../etc' and is
    refused. Pinned here because reversing that order would open the traversal
    back up while every unit test on this function kept passing.
    """
    from urllib.parse import parse_qs

    decoded = parse_qs("run_id=..%2fetc%2fpasswd")["run_id"][0]
    assert decoded == "../etc/passwd", decoded
    assert not log.is_safe_run_id(decoded), (
        "a percent-encoded traversal survived decoding; validation must run on "
        "the decoded value"
    )


def test_a_run_id_that_is_merely_awkward_is_still_allowed(table):
    """The validator is a positive test for one safe component, not a blacklist.

    Markup is a safe FILENAME and a dangerous thing to interpolate into HTML, and
    those are two problems with two fixes: this one keeps the write contained,
    html.escape at the point of use keeps the render inert. Conflating them would
    move an escaping bug's fix into a validator, where the next unescaped
    interpolation would not be caught.
    """
    awkward = "<img src=x onerror=alert(1)>"
    assert log.is_safe_run_id(awkward)
    log.append(_event(run_id=awkward, ts="2026-08-21T12:00:00+00:00", event_id="aaa"))
    assert len(log.read(awkward)) == 1


def test_an_unsafe_id_already_in_the_table_is_listed_by_nobody_and_counted(monkeypatch):
    """list_indexed_run_ids returns raw bytes; the CALLER filters and REPORTS.

    Filtering inside the reader would make "the table holds a malformed id" and
    "the table holds nothing" the same answer. approve_server counts the refusals
    so an operator sees a real fact about the store rather than an empty queue.
    """
    from agentorg import approve_server

    fake = _FakeTable()
    monkeypatch.setattr(config, "STATE_BACKEND", config.STATE_BACKEND_DYNAMODB)
    monkeypatch.setattr(log, "_table", lambda: fake)
    # Written directly, bypassing the validator, exactly as a hostile or
    # corrupted writer would have.
    for bad in ("../escape", "ok-run"):
        fake.items[(log.RUN_INDEX_PARTITION, bad)] = {
            log.PARTITION_KEY: log.RUN_INDEX_PARTITION,
            log.SORT_KEY: bad,
        }

    assert sorted(log.list_indexed_run_ids()) == ["../escape", "ok-run"], (
        "the reader filtered, which would hide the difference between a "
        "malformed id and an empty table"
    )
    safe, refused = approve_server._run_ids()
    assert safe == ["ok-run"]
    assert refused == 1, f"refused count is {refused}; the malformed id must be counted"


# --------------------------------------------------------------------------
# The Terraform. `terraform validate` checks SYNTAX; these check INTENT.
#
# On this plan the ingress module shipped with zero test coverage and four
# security-weakening mutations survived it -- including `Resource = ["*"]` on
# every IAM statement, which validate accepted. `trivy config` is deliberately
# not cited as evidence here: measured on this repo it reports the same finding
# count in both the narrow and the wildcard state, so it does not discriminate.
#
# EVERY HELPER BELOW IS PROVED TO MATCH SOMETHING FIRST. Four bugs were found in
# the equivalent helpers for the ingress module because a brace put statements at
# an unexpected depth and the finder matched nothing -- green, and vacuous.
# --------------------------------------------------------------------------

_MODULE = pathlib.Path(__file__).resolve().parent.parent / "infra" / "Terraform" / "modules" / "state"

_FOUR_ACTIONS = {
    "dynamodb:PutItem",
    "dynamodb:Query",
    "dynamodb:GetItem",
    "dynamodb:UpdateItem",
}


def _module_text(filename: str = "main.tf") -> str:
    path = _MODULE / filename
    assert path.is_file(), f"{path} does not exist"
    body = path.read_text()
    assert body.strip(), f"{path} is empty"
    return body


def _strip_comments(body: str) -> str:
    """Drop `#` comment lines, keeping code.

    This module's main.tf is roughly half comments, and those comments discuss
    the exact strings these tests match on -- `Resource = "*"`, `Scan`,
    `DeleteItem` -- so a matcher run over the raw text would find the prose and
    pass while the code said the opposite. Proved to be doing real work by
    test_the_comment_stripper_removes_prose_and_keeps_code below.
    """
    return "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_comment_stripper_removes_prose_and_keeps_code():
    """The helper the IAM tests depend on. If this is wrong, they are vacuous.

    Pinned against the specific hazard: this module's comments NAME the actions
    and resource shapes the tests below refuse to find. A stripper that returned
    the raw text would let the prose satisfy an assertion about the code.
    """
    stripped = _strip_comments(_module_text())
    assert "aws_dynamodb_table" in stripped, "the stripper removed the code"
    assert "PAY_PER_REQUEST" in stripped, "the stripper removed the code"
    # These phrases exist ONLY in comments in this file.
    assert "audit trail" not in stripped, (
        "comment prose survived stripping, so an assertion about the code could "
        "be satisfied by a comment"
    )
    assert "deliberate omission" not in stripped


def test_the_iam_statement_names_exactly_the_four_actions_the_code_issues():
    """log._table() uses put_item, query, get_item, update_item and nothing else.

    Asserted as an exact set, so both a widening and an unused grant fail. In
    particular Scan must never appear: the approve screen enumerates runs by
    querying one reserved partition precisely so that permission is not needed.
    """
    stripped = _strip_comments(_module_text())
    actions = set(re.findall(r'"(dynamodb:[A-Za-z]+)"', stripped))
    assert actions, (
        "no dynamodb: actions found in the module's code -- the matcher found "
        "nothing, so every assertion below would pass vacuously"
    )
    assert actions == _FOUR_ACTIONS, (
        f"the module grants {sorted(actions)}; expected exactly "
        f"{sorted(_FOUR_ACTIONS)}. A fifth action is reachable by nothing in "
        f"agentorg/log.py, and Scan in particular would let any holder read "
        f"every run's audit trail in one call."
    )


def test_no_iam_statement_grants_a_wildcard_resource():
    """The table's own ARN, not a prefix and not a star.

    This exact mutation survived the ingress module's review: `Resource = ["*"]`
    on every statement, accepted by validate and reported clean by trivy. Here it
    would hand the runtime and CI roles read/write on every DynamoDB table in an
    account shared with three other projects.
    """
    stripped = _strip_comments(_module_text())
    resources = re.findall(r"resources\s*=\s*\[([^\]]*)\]", stripped)
    assert resources, (
        "no `resources = [...]` found in the module's code; the matcher found "
        "nothing rather than proving the grant is narrow"
    )
    for block in resources:
        assert "*" not in block, f"wildcard resource in an IAM statement: {block.strip()!r}"
        assert "aws_dynamodb_table.runs.arn" in block, (
            f"IAM resource is {block.strip()!r}; it must be this module's own "
            f"table ARN"
        )


def test_the_table_key_schema_is_the_one_the_code_writes():
    """PK run_id, SK ts_event_id -- the constants agentorg/log.py owns.

    A key schema that disagreed with log.PARTITION_KEY/SORT_KEY would fail at
    runtime on the first PutItem, which is a validation error in a container
    nobody is watching rather than a test failure here.
    """
    stripped = _strip_comments(_module_text())
    hash_key = re.search(r'hash_key\s*=\s*"([^"]+)"', stripped)
    range_key = re.search(r'range_key\s*=\s*"([^"]+)"', stripped)
    assert hash_key and range_key, "the table declares no hash_key/range_key pair"
    assert hash_key.group(1) == log.PARTITION_KEY, (
        f"table hash_key is {hash_key.group(1)!r} but agentorg/log.py writes "
        f"{log.PARTITION_KEY!r}"
    )
    assert range_key.group(1) == log.SORT_KEY, (
        f"table range_key is {range_key.group(1)!r} but agentorg/log.py writes "
        f"{log.SORT_KEY!r}"
    )


def test_the_table_is_not_scannable_by_grant_or_by_index():
    """No GSI and no Scan: enumerating runs is a Query on one reserved partition."""
    stripped = _strip_comments(_module_text())
    assert "global_secondary_index" not in stripped, (
        "a GSI was added; the run index is a reserved partition that save() "
        "upserts into, which needs no index and no Scan"
    )
    assert "dynamodb:Scan" not in stripped
    assert "dynamodb:DeleteItem" not in stripped, (
        "DeleteItem is a deliberate omission: this table is an audit trail and "
        "the module has no delete path"
    )


def test_the_table_name_matches_the_default_the_application_reads():
    """Two places carry this literal; a drift means the app reads an absent table."""
    declared = re.search(
        r'variable\s+"table_name"[\s\S]*?default\s*=\s*"([^"]+)"',
        _module_text("variables.tf"),
    )
    assert declared, "the module does not default table_name"
    assert declared.group(1) == config.STATE_TABLE, (
        f"module defaults the table to {declared.group(1)!r} but "
        f"config.STATE_TABLE is {config.STATE_TABLE!r}"
    )


def test_the_run_table_keeps_continuous_backups_and_encryption():
    """It is the decision log: PITR is the difference between truncated and gone."""
    stripped = _strip_comments(_module_text())
    assert "point_in_time_recovery" in stripped
    assert "server_side_encryption" in stripped
