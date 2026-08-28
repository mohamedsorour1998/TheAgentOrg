"""K7, first half: submission is idempotent, and no route can reach a gate.

OWNER: Lane K. Split across three files so each stays under review size:

  * this one        -- idempotency under retry, and the gate refusal over the AST
  * test_api_auth.py     -- what the credential layer refuses
  * test_api_cancel.py   -- cancellation mid-run, at all three positions

THE GATE TEST IS THE MOST IMPORTANT ASSERTION IN THIS LANE, and it is written
over the **AST** rather than as a substring search for a measured reason. CLAUDE.md
records the same failure twice in one lane: a test asserting `"SEVERITY_ORDER" in
source` was satisfied by a COMMENT saying "SEVERITY_ORDER is imported, not
restated", so replacing the import with a hardcoded tuple left all 19 tests green.

This package's modules are roughly half commentary and several of them discuss
`gates.resume` and `queue.resume` at length -- deliberately, because the argument
for their absence has to live somewhere. A `grep` for `resume` therefore matches
every one of those files, so a substring test here would be satisfied by the very
prose explaining the refusal while an actual call sat beside it. The AST walk sees
calls and imports and does not see comments.
"""

import ast
import pathlib

import pytest

from agentorg import api, queue
from agentorg.api import auth, idempotency, service

API_DIR = pathlib.Path(api.__file__).resolve().parent
API_MODULES = sorted(API_DIR.glob("*.py"))

# GUARD AGAINST A VACUOUS FILE, in the form CLAUDE.md prescribes: a matcher that
# can match nothing must say so. If the glob found nothing, every structural test
# below would iterate an empty list and report success having read no source.
assert API_MODULES, "no modules found under agentorg/api/; the AST tests would pin nothing"
assert len(API_MODULES) >= 7, (
    f"expected the full api package, found only {[p.name for p in API_MODULES]}"
)


@pytest.fixture(autouse=True)
def _clean_substrate():
    """A fresh queue, key store, idempotency store and config store per test.

    On BOTH sides, for `conftest.py` guard 5's reason: state carried between tests
    makes a stale hit look exactly like a fresh answer, and `queue.reset`'s own
    docstring predicts that a second lane enqueuing anything is the moment its
    file-scoped fixture becomes insufficient. This lane is that second lane.
    """
    queue.reset()
    auth.set_key_store(auth.InMemoryKeyStore())
    idempotency.set_idempotency_store(idempotency.IdempotencyStore())
    service.reset()
    yield
    queue.reset()
    auth.set_key_store(auth.InMemoryKeyStore())
    idempotency.set_idempotency_store(idempotency.IdempotencyStore())
    service.reset()


@pytest.fixture()
def credential():
    """A verified credential holding every scope."""
    _, key = auth.issue_key("tenant-alpha")
    return auth.resolve(f"Bearer {key}")


def _submission(ticket_id="7", text="add a per-IP rate limit to app/auth.py"):
    return service.RunSubmission(ticket_id=ticket_id, ticket_text=text)


# ──────────────────────────────────────────────────────────────────────────────
# THE GATE REFUSAL. Over the AST, never over the text.
# ──────────────────────────────────────────────────────────────────────────────

def _calls_and_imports(path: pathlib.Path) -> tuple[set[str], set[str]]:
    """Every called name and every imported name in one module, from its AST.

    Returns dotted call targets (`queue.resume`, `gates.resume`) and imported
    names. Comments and docstrings are invisible here by construction, which is
    the entire reason this helper exists rather than a `in source` check.
    """
    tree = ast.parse(path.read_text())
    calls: set[str] = set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                calls.add(ast.unparse(target))
            elif isinstance(target, ast.Name):
                calls.add(target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports.add(f"{node.module or ''}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
    return calls, imports


def test_the_ast_helper_actually_sees_calls_and_not_comments(tmp_path):
    """THE GUARD ON THE GUARD. A blind helper would make every test below vacuous.

    Written first and deliberately: the tests underneath assert that a name is
    ABSENT, and an assertion of absence passes when the helper cannot see
    anything at all. So this proves the helper finds a real call, and proves it
    does NOT find one that appears only in a comment or a string -- which is the
    exact discrimination the AST is chosen for.
    """
    module = tmp_path / "probe.py"
    module.write_text(
        '"""A docstring mentioning queue.resume at length."""\n'
        "# a comment mentioning gates.resume too\n"
        "SQL = 'gates.resume in a string literal'\n"
        "import json\n"
        "from agentorg import queue\n"
        "def f():\n"
        "    queue.enqueue('r', 'plan')\n"
    )
    calls, imports = _calls_and_imports(module)
    assert "queue.enqueue" in calls, "the helper cannot see a real call"
    assert "json" in imports and "agentorg.queue" in imports
    assert "queue.resume" not in calls, (
        "the helper matched a docstring; it would then match this package's own "
        "prose about the refusal and every absence test below would be vacuous"
    )
    assert "gates.resume" not in calls, "the helper matched a comment or a string"


@pytest.mark.parametrize("path", API_MODULES, ids=lambda p: p.name)
def test_no_api_module_can_reach_a_gate_resume(path):
    """K5's load-bearing property: nothing here advances a paused run.

    `queue.resume` is the ONLY exit from `paused` and `gates.resume` is the only
    writer of a `HumanDecision`. A control-plane route reaching either would let a
    machine credential approve the security gate -- see `api/__init__.py`.

    Asserted per module so a failure names the file, and asserted over calls AND
    imports: an import alone is enough to worry about, because the next edit is
    one line from using it.
    """
    calls, imports = _calls_and_imports(path)
    forbidden_calls = {c for c in calls if c.endswith((".resume", ".pause"))}
    assert not forbidden_calls, (
        f"{path.name} calls {sorted(forbidden_calls)}. The API must not advance or "
        f"hold a run at a gate: `queue.resume` is the only exit from `paused` and "
        f"a machine credential reaching it approves the security gate."
    )
    forbidden_imports = {i for i in imports if "gates" in i}
    assert not forbidden_imports, (
        f"{path.name} imports {sorted(forbidden_imports)}. `gates` is the one "
        f"writer of a RunState and of a HumanDecision; the control plane reads the "
        f"queue instead."
    )


def test_no_api_module_imports_the_unauthenticated_approval_screen():
    """`approve_server` has no auth and must not be reachable from a served route.

    It binds loopback and resumes a pipeline past a human gate. Importing it here
    would put that capability behind a network listener this module is designed to
    be exposable -- which is precisely the combination its own docstring forbids
    ("NEVER expose it off-host").
    """
    offenders = []
    for path in API_MODULES:
        _, imports = _calls_and_imports(path)
        if any("approve_server" in name for name in imports):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} import approve_server, which has NO authentication and "
        f"resumes a paused pipeline past a human gate."
    )


def test_no_scope_grants_a_gate_decision():
    """The vocabulary cannot express it either.

    Belt to the AST's braces, and not redundant: a scope named `gates:approve`
    would read as a capability that exists and is merely unassigned, and the next
    person grants it and goes looking for the route that must be broken.
    """
    assert auth.SCOPES, "auth.SCOPES is empty; this test would pin nothing"
    suspicious = [s for s in auth.SCOPES
                  if any(word in s for word in ("approve", "resume", "gate", "promote"))]
    assert not suspicious, f"these scopes suggest a gate decision: {suspicious}"


def test_the_openapi_document_states_the_gate_refusal():
    """A reader of the generated client must not have to notice a silence.

    The absence of an approval route is a fact about this API. Left unstated, a
    customer concludes the endpoint is undocumented rather than absent, and asks
    for it to be enabled.
    """
    description = api.openapi_document()["info"]["description"]
    assert "cannot approve, reject or resume" in description
    assert "absent rather than unassigned" in description


# ──────────────────────────────────────────────────────────────────────────────
# K1 + K7: submission, and idempotency under retry
# ──────────────────────────────────────────────────────────────────────────────

def test_a_submission_enqueues_exactly_one_plan_job(credential):
    """K1. The run exists on the queue, at `plan`, ready to be claimed."""
    status, replayed = service.submit_run(credential, _submission())
    assert replayed is False
    jobs = queue.jobs_for_run(status.run_id)
    assert len(jobs) == 1, f"expected one plan job, found {[j.stage for j in jobs]}"
    assert jobs[0].stage == "plan"
    assert jobs[0].status == "ready"
    assert jobs[0].ticket_text == "add a per-IP rate limit to app/auth.py", (
        "the ticket text must be ON THE ROW: `worker.run_one` reads its inputs "
        "from the claimed job, and a text left in the submitting process's memory "
        "produced `run_stage: error: plan needs --ticket-id and --ticket-text`"
    )


def test_a_retry_with_the_same_idempotency_key_does_not_start_a_second_run(credential):
    """K7's headline. The measured defect this protects against is in
    `api/idempotency.py`'s docstring: the queue's UNIQUE index cannot carry this,
    because `adopt_run_id` frees the placeholder run id mid-run.
    """
    first, replayed_first = service.submit_run(
        credential, _submission(), idempotency_key="build-42")
    second, replayed_second = service.submit_run(
        credential, _submission(), idempotency_key="build-42")

    assert replayed_first is False
    assert replayed_second is True
    assert second.run_id == first.run_id
    assert len(queue.jobs_for_run(first.run_id)) == 1, (
        "a retry started a second job for the same run, so the pipeline would "
        "invoke every agent twice -- a PR comment posted twice and a model bill "
        "paid twice"
    )


def test_the_replay_returns_the_original_ids_and_not_merely_a_success(credential):
    """A 200 that did not name the first run would hide the deduplication.

    A client retrying on a timeout needs the id it failed to receive. A bare
    success would leave it unable to watch the run it started.
    """
    first, _ = service.submit_run(credential, _submission(), idempotency_key="k")
    second, replayed = service.submit_run(credential, _submission(), idempotency_key="k")
    assert replayed is True
    assert second.run_id == first.run_id
    assert second.stage == "plan"
    assert second.status == "ready"


def test_two_submissions_without_a_key_are_two_runs(credential):
    """THE CONTROL FOR THE TEST ABOVE, and it is not a formality.

    Without this, an implementation that deduplicated EVERY submission -- or one
    that never enqueued a second job for any reason -- would satisfy the
    idempotency test. A client that did not ask for deduplication must not get it,
    because two genuine tickets with the same text are two runs.
    """
    first, _ = service.submit_run(credential, _submission())
    second, replayed = service.submit_run(credential, _submission())
    assert replayed is False
    assert second.run_id != first.run_id, (
        "two keyless submissions collapsed into one run; the idempotency test "
        "above would then pass for the wrong reason"
    )
    assert len(queue.jobs_for_run(first.run_id)) == 1
    assert len(queue.jobs_for_run(second.run_id)) == 1


def test_the_same_key_from_two_tenants_is_two_runs():
    """The idempotency record is keyed per tenant, and it must be.

    `Idempotency-Key` is client-chosen, so two customers will eventually send the
    same one. Keyed on the string alone, tenant B's submission is answered with
    tenant A's run id -- a cross-tenant disclosure delivered by the deduplication
    layer rather than by a query.
    """
    _, alpha_key = auth.issue_key("tenant-alpha")
    _, beta_key = auth.issue_key("tenant-beta")
    alpha = auth.resolve(f"Bearer {alpha_key}")
    beta = auth.resolve(f"Bearer {beta_key}")

    first, _ = service.submit_run(alpha, _submission(), idempotency_key="build-1")
    second, replayed = service.submit_run(beta, _submission(), idempotency_key="build-1")

    assert replayed is False, (
        "tenant-beta's submission was answered as a replay of tenant-alpha's; the "
        "idempotency record is not tenant-scoped"
    )
    assert second.run_id != first.run_id


def test_the_placeholder_run_id_carries_the_tenant(credential):
    """Two tenants submitting one ticket id in the same second must not collide.

    `queue.enqueue` refuses a duplicate `(run_id, stage, attempt)` by RAISING and
    its message names the existing job -- so a collision would answer one tenant
    with a refusal naming another tenant's job id. `worker.start_run` has no such
    requirement, which is why this differs from it deliberately.
    """
    status, _ = service.submit_run(credential, _submission(ticket_id="7"))
    assert "tenant-alpha" in status.run_id, (
        f"the placeholder {status.run_id!r} does not name the tenant, so two "
        f"tenants submitting ticket 7 in the same millisecond collide"
    )


def test_a_submission_records_its_trigger_and_the_value_differs_from_the_others():
    """`trigger` must distinguish an API run from a hand dispatch and from an issue.

    `tests/test_trigger_provenance.py` states the property for `manual` vs
    `issue`: identical values would make a run recording the value
    indistinguishable from one whose trigger was never set, so the field would be
    "present, populated and worthless". A third source needs a third value.
    """
    _, key = auth.issue_key("tenant-alpha")
    credential = auth.resolve(f"Bearer {key}")
    status, _ = service.submit_run(credential, _submission())
    job = queue.jobs_for_run(status.run_id)[0]
    assert job.trigger == "api"
    assert job.trigger not in ("manual", "issue"), (
        "the API's trigger value collides with an existing one, so a run started "
        "through the API is indistinguishable from one started the other way"
    )


def test_poisoned_travels_onto_the_row_as_a_real_boolean(credential):
    """The demo's poisoned path is reachable through the API.

    `run-pipeline.yml` takes this as a STRING because `workflow_dispatch` inputs
    "arrive as STRINGS, booleans included" and the REST dispatch API rejects real
    JSON booleans. That constraint belongs to the Actions boundary; importing it
    here would 422 a caller for sending a correct JSON `true`.
    """
    status, _ = service.submit_run(
        credential,
        service.RunSubmission(ticket_id="8", ticket_text="x", poisoned=True),
    )
    assert queue.jobs_for_run(status.run_id)[0].poisoned is True

    clean, _ = service.submit_run(
        credential,
        service.RunSubmission(ticket_id="9", ticket_text="x", poisoned=False),
    )
    assert queue.jobs_for_run(clean.run_id)[0].poisoned is False, (
        "an explicit poisoned=False must stay False -- `developer.run`'s "
        "`poisoned or state.poisoned` bug is the same shape one layer down"
    )
