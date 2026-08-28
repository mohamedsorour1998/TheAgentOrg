"""Indexing a run against its tenant. Owner: the integrator.

WHY THIS FILE EXISTS. Lane B built `tenancy.accessors.record_run` and its whole leak
suite around the `run` table, and NOTHING ON THE PIPELINE PATH EVER CALLED IT. Measured
before the fix:

    grep -rn record_run agentorg/ scripts/ tests/
    -> agentorg/tenancy/accessors.py:409   the definition
    -> tests/test_tenancy_leak.py          four uses, all Lane B's own tests

So the table was correct, tenant-scoped, covered by a suite that attempts the breach on
every accessor, and EMPTY ON EVERY REAL RUN. Lane I's tenant-scoped run list read it
faithfully and would have shown a judge nothing. Lane I found it, refused to work around
it -- a Node-side fallback reading `runs/*.state.json` would have bypassed tenant scoping
entirely -- and reported it as a named gap.

Third instance this phase of one shape: a correct answer nobody asks for. Lane C's
scoring library had no caller; Lane E's usage payload had no wiring; this had no writer.
Every test passed in all three cases. None of the three features existed.

So the assertions here are about the CALL, not the accessor. `test_tenancy.py` already
proves `record_run` writes a row; what nothing proved is that a pipeline reaches it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from agentorg.db import engine, migrations
from agentorg.state import RunState
from agentorg.tenancy import accessors, run_index, tenant_zero

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tenancy_db(tmp_path, monkeypatch):
    """A migrated database with tenant zero adopted, wired to `TENANT_DB`."""
    path = str(tmp_path / "tenancy.db")
    connection = engine.connect(path)
    migrations.migrate(connection)
    tenant_zero.adopt(connection)
    connection.commit()
    connection.close()
    monkeypatch.setenv("TENANT_DB", path)
    return path


def _rows(path):
    connection = engine.connect(path)
    try:
        with engine.acting_as(tenant_zero.TENANT_ZERO_ID):
            scope = accessors.scope_for(connection, tenant_zero.TENANT_ZERO_ID)
            return accessors.list_runs(scope)
    finally:
        connection.close()


# ── the write, and the marker tenant ──────────────────────────────────────────

def test_a_run_is_indexed_against_tenant_zero(tenancy_db):
    """The blank `RunState.tenant_id` every existing run carries must resolve.

    `scope_for` REFUSES a blank tenant, deliberately: "a blank scope matches a blank
    tenant column, which is a row nobody owns". So the marker has to go through
    `tenant_zero.for_run_state` first, and skipping that translation is the failure this
    pins -- it would raise rather than write, and `record_run` swallows raises.
    """
    state = RunState(ticket_id="41", ticket_text="rate limit")
    assert state.tenant_id == "", "the marker changed; this test's premise is gone"

    assert run_index.record_run(state) is True

    rows = _rows(tenancy_db)
    assert len(rows) == 1, f"expected one indexed run, got {rows}"
    assert rows[0]["ticket_id"] == "41"


def test_the_status_follows_the_run_to_its_ending(tenancy_db):
    """A blocked run must not be listed as still running.

    The index is written at `plan`, when the status is `running`, and revised at the
    ending. Without the second call the UI shows every run as running forever -- which
    reads as a stuck pipeline rather than as a missing update.
    """
    state = RunState(ticket_id="43", ticket_text="rate limit")
    run_index.record_run(state)

    state.status = "blocked"
    assert run_index.update_status(state) is True

    assert _rows(tenancy_db)[0]["status"] == "blocked"


def test_indexing_is_a_no_op_with_no_database_configured(monkeypatch):
    """`TENANT_DB` unset is the single-tenant deployment, and must stay silent.

    Every knob in this repository is chosen so the default preserves today's behaviour.
    An indexing call that raised or logged an error here would make the existing
    deployment noisy about a feature it does not use.
    """
    monkeypatch.delenv("TENANT_DB", raising=False)
    state = RunState(ticket_id="7", ticket_text="x")

    assert run_index.record_run(state) is False
    assert run_index.update_status(state) is False


def test_a_broken_database_does_not_fail_the_run(tmp_path, monkeypatch, caplog):
    """An index is not the run's record, so it may never fail a run that did its work.

    A poisoned run that correctly blocked and then died writing an index row would
    report a crash where the demo's whole point is a clean refusal. The trade is that a
    silent failure leaves the list short -- so the WARNING is the load-bearing half, and
    the return value is what a test can assert on.
    """
    monkeypatch.setenv("TENANT_DB", str(tmp_path / "no-schema.db"))
    state = RunState(ticket_id="7", ticket_text="x")

    with caplog.at_level("WARNING"):
        assert run_index.record_run(state) is False

    assert caplog.records, (
        "indexing failed SILENTLY. That is the did-not-run-versus-passed ambiguity this "
        "repository exists to prevent, arriving through the fix for it"
    )
    assert state.run_id in caplog.text, "the warning does not name the run it lost"


# ── the call sites, over the AST ──────────────────────────────────────────────

@pytest.mark.parametrize(("path", "creator"), [
    ("agentorg/graph.py", "_walk"),
    ("scripts/run_stage.py", "_stage_plan"),
])
def test_the_creating_stage_indexes_the_run(path, creator):
    """Both paths that CREATE a run must index it.

    Asserted over the AST, not by substring: this module's own docstring and the call
    site's comment both name `record_run` at length, and CLAUDE.md records two cases
    where a test was satisfied by the comment explaining the thing it checked.

    Two paths because there are two pipelines -- `graph._walk` is the in-process one the
    suite drives, `run_stage._stage_plan` is what the cloud pipeline and Lane A's queue
    both execute. Wiring one and not the other is how a feature works in tests and not
    in production.
    """
    tree = ast.parse((REPO_ROOT / path).read_text())
    func = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == creator), None)
    assert func is not None, f"{creator} not found in {path}; this test would pin nothing"

    calls = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call)
        and (n.func.attr if isinstance(n.func, ast.Attribute)
             else getattr(n.func, "id", "")) == "record_run"
    ]
    assert calls, (
        f"{path}:{creator} never calls record_run, so a run created on this path is "
        f"never indexed and the UI's tenant-scoped list is short by one"
    )


@pytest.mark.parametrize("path", ["agentorg/graph.py", "scripts/run_stage.py"])
def test_both_paths_update_the_status_too(path):
    """Writing the index and never revising it is worse than not writing it.

    A list where every run says `running` invites the reading that the pipeline is
    stuck. Pinned per file for the reason above: one path wired and one not is the
    works-in-tests failure.
    """
    tree = ast.parse((REPO_ROOT / path).read_text())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (n.func.attr if isinstance(n.func, ast.Attribute)
             else getattr(n.func, "id", "")) == "update_status"
    ]
    assert calls, f"{path} never calls run_index.update_status"


def test_update_status_does_not_insert_a_row_it_could_not_find(tenancy_db):
    """No upsert. A wrong-tenant update must not become a new row under the caller.

    `accessors.update_run_status` calls `_require` first, which refuses a run this
    tenant does not own. An upsert in `run_index` would turn that refusal into a
    cross-tenant WRITE -- the exact breach Lane B's leak suite attempts on every
    accessor, arriving through a helper written to be convenient.
    """
    state = RunState(ticket_id="99", ticket_text="never indexed")

    assert run_index.update_status(state) is False, (
        "update_status reported success for a run that was never indexed"
    )
    assert _rows(tenancy_db) == [], (
        "update_status INSERTED a row. It must only ever update one that exists, or a "
        "wrong-tenant update becomes a cross-tenant write."
    )
