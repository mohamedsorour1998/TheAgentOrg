"""No reader may obtain an UNSCOPED table handle. Step 6 of the migration plan.

Step 8 repointed `web/lib/reader/*.py` at DynamoDB and left every one of them on
`boto3.resource("dynamodb")` -- the ambient credential, which under Amplify's SSR
runtime is a role that can read every tenant's rows. The output was correct and
the isolation was nil: the only thing keeping one tenant out of another's data
was the `tenant_id` argument happening to be right at each call site.

Step 6 makes the credential itself tenant-scoped. These tests keep it that way,
and they exist because **the regression is invisible in a diff review**:
`_client.table()` and `_client.table(tenant_id)` differ by one word, both read as
correct, and the unscoped one returns rows rather than raising.

**EVERYTHING HERE IS ASSERTED OVER THE AST, and that is not a preference.**
`_client.py`'s docstring argues at length about `boto3.resource("dynamodb")` and
quotes the literal. A substring check for it would be satisfied by the paragraph
explaining why it must not appear -- this repository's named pattern, found twice
before (deploy.yml's smoke check, config.py's threshold comment), and most likely
in exactly this kind of file, which is more commentary than code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
READER_DIR = REPO_ROOT / "web" / "lib" / "reader"
CLIENT = READER_DIR / "_client.py"

# Every reader module except the producer itself.
_MODULES = sorted(p for p in READER_DIR.glob("*.py") if p.name != "_client.py")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _table_calls(path: Path) -> list[ast.Call]:
    """`_client.table(...)` call nodes in one module.

    Over the AST rather than by grep, so a call split across lines, or one merely
    mentioned in a docstring, is judged as code or not at all.
    """
    return [
        node
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "table"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_client"
    ]


# THE READERS THAT ACTUALLY TAKE A HANDLE. Derived rather than listed, and the
# next test asserts the derivation is not empty -- a parametrisation computed
# from the code under test is how a selection silently empties while the count
# still looks healthy, which this repository has measured twice (`pytest -k`
# misspelled, and Lane H's attack parametrisation).
_CONSUMERS = [p for p in _MODULES if _table_calls(p)]


def test_there_are_readers_to_check():
    """Anti-vacuity. Every test below iterates a glob, and an empty glob passes."""
    assert _MODULES, f"no reader modules found under {READER_DIR}; every test in this file would pin nothing"
    assert CLIENT.exists(), f"{CLIENT} is missing"
    assert _CONSUMERS, (
        "no reader calls `_client.table` at all, so the scoping assertions below "
        "are parametrised over an empty list and pin nothing."
    )


@pytest.mark.parametrize("path", _CONSUMERS, ids=lambda p: p.name)
def test_every_table_call_names_a_tenant(path: Path):
    """`_client.table()` with no argument is the unscoped read, spelled shortest."""
    for call in _table_calls(path):
        assert call.args or call.keywords, (
            f"{path.name}:{call.lineno} calls `_client.table()` with no tenant. "
            f"That is the UNSCOPED handle -- it reads with the process's own "
            f"credential, which can reach every tenant's partition, and it "
            f"returns rows rather than raising."
        )


@pytest.mark.parametrize("path", _MODULES, ids=lambda p: p.name)
def test_no_reader_builds_its_own_aws_handle(path: Path):
    """The bypass. `_client` can be as strict as it likes if nobody has to use it.

    Applies to EVERY reader, including the ones that take no table handle: the
    cheapest way to reintroduce an unscoped read is not to weaken `_client.table`
    but to skip it.
    """
    boto_calls = [
        node
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"resource", "client"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "boto3"
    ]
    assert not boto_calls, (
        f"{path.name} builds a boto3 handle directly at line(s) "
        f"{[c.lineno for c in boto_calls]}, bypassing `_client.table`. That "
        f"handle carries the ambient credential and is not constrained by "
        f"`dynamodb:LeadingKeys`."
    )


def test_the_approval_WRITE_is_not_iam_scoped_AND_THAT_GAP_IS_PINNED():
    """`approve.py` writes through the queue's CROSS-TENANT credential. Recorded.

    It is the one reader that takes no table handle, and excluding it quietly
    from the tests above would hide the reason. It calls `queue.resume`, and
    `queue/_dynamo.dynamo_queue()` builds an ambient client on purpose -- the
    worker claims whichever job is next and only then learns whose tenant it is,
    so `LeadingKeys` cannot constrain that principal. §4 of the migration plan
    admits exactly this asymmetry for the pipeline.

    **SO THE APPROVAL PATH'S ISOLATION IS APPLICATION CODE, NOT IAM.** What keeps
    one tenant from approving another's gate is `web/lib/authz.ts`, which refuses
    a run whose tenant differs from the session's -- and that check reads through
    the SCOPED handle, so it cannot see another tenant's run in the first place.
    Two layers where the read path has three, and the missing one is the one that
    survives a bug in the other two.

    This test FAILS when the gap closes, and its message says what else to
    finish -- a gap recorded only in a comment gets closed halfway, with the
    credential scoped and nothing reading it, and nothing would say so.
    """
    source = (READER_DIR / "approve.py").read_text()
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "queue" in imports, (
        "approve.py no longer imports `queue`; this test pins nothing about how "
        "the approval write is credentialed."
    )
    assert "_client" not in imports, (
        "approve.py now imports `_client`, so the approval write may have been "
        "moved onto the tenant-scoped handle. If so this gap is CLOSED -- delete "
        "this test, and check three things first: that `queue.resume` is reached "
        "with a scoped client, that the WORKER's own claim path still uses the "
        "cross-tenant credential (it must -- it does not know the tenant until "
        "after it claims), and that §4 of docs/design/dynamodb-migration.md is "
        "updated to say so."
    )


def test_the_table_factory_has_no_default_tenant():
    """A default would restore the unscoped call without any call site changing.

    `def table(tenant_id: str = "")` passes every test above -- each call site
    still names a tenant -- while making the argument optional again for whoever
    writes the next reader.
    """
    tree = _tree(CLIENT)
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "table"]
    assert len(funcs) == 1, f"expected exactly one `table` in {CLIENT.name}, found {len(funcs)}"

    args = funcs[0].args
    assert [a.arg for a in args.args] == ["tenant_id"], (
        "`table` must take the tenant as its only positional argument"
    )
    assert not args.defaults, (
        "`table(tenant_id)` has a DEFAULT. Under Postgres, forgetting to bind the "
        "tenant produced an empty result rather than an error -- the worst failure "
        "a scoping mechanism can have, because it is indistinguishable from 'this "
        "tenant has no data'. A required argument makes that a TypeError."
    )


def test_the_client_constructs_no_unscoped_resource():
    """No `boto3.resource(...)` / `boto3.client(...)` anywhere in the producer.

    The ambient handle is what step 8 shipped and what step 6 removes. Over the
    AST because this module's docstring quotes the exact call it forbids.
    """
    boto_calls = [
        node
        for node in ast.walk(_tree(CLIENT))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"resource", "client"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "boto3"
    ]
    assert not boto_calls, (
        f"{CLIENT.name} builds a boto3 handle directly at line(s) "
        f"{[c.lineno for c in boto_calls]}. That handle carries the AMBIENT "
        f"credential -- the Amplify SSR compute role, which can read every "
        f"tenant -- so tenant isolation would rest on the `tenant_id` argument "
        f"alone. It must come from `agentorg.db.tenant_credentials`."
    )


def test_the_client_gets_its_handle_from_the_scoped_minter():
    """The positive control. Without it, deleting `table` entirely passes above.

    Every test so far asserts an ABSENCE, and a file with no code satisfies all
    of them. This is the row that says the right thing is present.
    """
    tree = _tree(CLIENT)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "tenant_credentials" in imported, (
        f"{CLIENT.name} does not import `tenant_credentials`, so nothing here "
        f"mints a tenant-scoped credential and the absence-assertions above are "
        f"satisfied by a module that does nothing at all."
    )


def test_the_per_tenant_handle_cache_is_keyed_by_tenant():
    """A single cached handle serves the second tenant the first one's credential.

    Same hazard as `tenant_credentials._CACHE`, one layer up, and the same
    symptom: the rows come back and nothing raises. Asserted on the annotation
    because the cache is module state a test cannot exercise without a live STS.
    """
    tree = _tree(CLIENT)
    caches = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_TABLES"
    ]
    assert len(caches) == 1, (
        "`_TABLES` is not declared with an annotation in the module body; this "
        "test would pin nothing."
    )
    annotation = ast.unparse(caches[0].annotation)
    assert annotation.startswith("dict["), (
        f"the handle cache is annotated {annotation!r}, not a dict. A single "
        f"cached handle hands the second tenant of a process a client built from "
        f"the FIRST tenant's credential."
    )


# ── the gate: one env var across a writer and three readers ──────────────────

def test_the_writer_and_every_reader_name_THE_SAME_env_var():
    """FOUR DECLARATIONS OF ONE FACT, and they were wrong together until 2026-09-15.

    `run_index` writes the run row; `runs.py`, `detail.py` and `repositories.py` read
    it. Each decides "is the tenancy index configured?" from an environment variable,
    and each declared that variable separately. They agreed on `TENANT_DB` -- a
    POSTGRES DSN -- while step 8 had already repointed the readers at DynamoDB and the
    DynamoDB-only decision guaranteed the DSN would never be set.

    So all four agreed, all four were consistent, and the deployed run list was empty
    for a reason no log line named. `aws dynamodb scan` read `"count": 0` after every
    run this project has ever done.

    **AGREEMENT IS NOT CORRECTNESS**, which is why this test also pins the LITERAL.
    Asserting only that the four match would have passed throughout the outage --
    exactly the "property checked against an oracle that moves with it" failure this
    repository has measured twice, in `SEVERITY_ORDER` and in `VERDICT_ARGUMENTS`.
    """
    sources = [
        "agentorg/tenancy/run_index.py",
        "web/lib/reader/runs.py",
        "web/lib/reader/detail.py",
        "web/lib/reader/repositories.py",
    ]

    for rel in sources:
        tree = ast.parse((REPO_ROOT / rel).read_text())
        names = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        # The writer holds the name in a module constant rather than inline, so the
        # call-site scan above cannot see it. Both shapes are read, because a test
        # that only understood one would silently stop covering the other.
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "_TABLE_ENV"
                    and isinstance(node.value, ast.Constant)):
                names.add(node.value.value)

        assert names, (
            f"{rel} reads no environment variable at all; this test would pin nothing. "
            f"If the gate moved, move this assertion with it."
        )
        assert "TENANCY_TABLE" in names, (
            f"{rel} gates the tenancy index on {sorted(names)} and not on "
            f"`TENANCY_TABLE`. If it names a DSN, the writer and the readers are "
            f"pointed at two different databases and the run list is silently empty."
        )
        assert "TENANT_DB" not in names, (
            f"{rel} still reads `TENANT_DB`, a POSTGRES DSN. The operator's decision "
            f"is DynamoDB only, so that variable is never set and this gate is always "
            f"closed -- which reads as 'this tenant has no runs'."
        )


# ── ownership before the cross-tenant read ───────────────────────────────────

def test_detail_establishes_OWNERSHIP_before_it_loads_the_state_document():
    """ORDER IS THE ONLY CHECK THERE IS, and swapping two lines removes it.

    `gates.load(run_id)` reads `theagentorg-runs`, whose partition key is the run
    id with **no tenant in it** -- so `dynamodb:LeadingKeys` has nothing to compare
    and cannot constrain that read. The Amplify compute role holds Query/GetItem on
    that table directly (added 2026-09-15 so `/runs/[id]` can show a verdict at
    all), which makes the call cross-tenant capable by construction.

    What constrains it is that `detail.py` first reads the tenancy index through the
    TENANT-SCOPED handle. A caller who does not own the run gets `NotFound` from a
    credential that physically cannot see another tenant's index row, and the
    document is never reached.

    Reversed, the code still reads as correct -- load the state, then check who owns
    it -- and every test that asserts on the RESPONSE keeps passing, because the
    refusal still happens. What changes is that a cross-tenant document has already
    been read. Same shape as `test_the_sre_stage_measures_ci_before_invoking_the_agent`,
    and asserted over the AST for the same reason: a substring check would be
    satisfied by the comment explaining the ordering.
    """
    tree = ast.parse((READER_DIR / "detail.py").read_text())

    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef)
         and any(
             isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute)
             and c.func.attr == "load"
             for c in ast.walk(n))),
        None,
    )
    assert fn is not None, (
        "no function in detail.py calls `.load(...)`; if the state document is no "
        "longer read here, this ordering no longer applies -- delete this test and "
        "say so in main.tf, which grants the read on the strength of it."
    )

    def _line_of(pred) -> int | None:
        return next((n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Call) and pred(n)), None)

    ownership = _line_of(
        lambda c: isinstance(c.func, ast.Attribute) and c.func.attr == "get_run")
    document = _line_of(
        lambda c: isinstance(c.func, ast.Attribute) and c.func.attr == "load")

    assert ownership is not None, (
        f"{fn.name} never calls `get_run`, so nothing establishes that this tenant "
        f"owns the run before its state document is read."
    )
    assert document is not None, "this test would pin nothing"
    assert ownership < document, (
        f"{fn.name} loads the state document at line {document} BEFORE establishing "
        f"ownership at line {ownership}. `gates.load` reads a table keyed on run_id "
        f"with no tenant in the partition key, so LeadingKeys cannot constrain it -- "
        f"the ownership read through the scoped handle is the only thing that does."
    )
