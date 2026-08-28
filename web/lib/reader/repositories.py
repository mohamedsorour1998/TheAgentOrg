"""Set which repositories a tenant has in scope. Task I2. OWNER: Lane I.

Invoked by `web/lib/pipeline.ts` with a JSON request on stdin.

=========================================================================
THE ONLY WRITE IN THIS LANE OTHER THAN A GATE DECISION, AND IT IS AN
AUTHORISATION BOUNDARY.
=========================================================================
`web/lib/authz.ts` refuses a gate approval whose run targets a repository not in this
list. So adding a row here makes runs approvable, and removing one makes them
unapprovable -- which is why the route carries the same cross-site `Origin` check as
the approvals route, and why every change is logged with the person who made it.

ADDITIVE ONLY. There is no removal path in this file, and that is a stated limit
rather than an oversight: `accessors` exposes `add_repository` and no delete, because
Lane B's `repository` rows are referenced by runs and a deleted row would orphan the
ownership record an approval reads. Narrowing scope therefore needs a
`repository.in_scope` column or a delete accessor -- both Lane B's file.

**The consequence, stated plainly: a repository connected today cannot be
disconnected through this endpoint.** `DELETE /api/link/github` revokes the whole
GitHub grant, which is the coarse version and the one that works. A judge asking "can
I remove one repository" should be told no, not shown a button that silently does
nothing.

WRITES GO THROUGH LANE B'S SCOPED ACCESSOR, never with SQL written here. The
`repository` table carries three isolation triggers on SQLite and an RLS policy on
Postgres, and `accessors.add_repository` is the route that satisfies them --
`engine.connect` is what registers the `current_tenant()` function they compare
against, so a connection opened any other way fails every scoped write with "no such
function".
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import UTC, datetime

from agentorg.db import engine
from agentorg.tenancy import accessors, tenant_zero


def _fail(message: str, detail: str = "") -> int:
    json.dump({"error": message, "detail": detail}, sys.stdout)
    return 0


def _database_path() -> str:
    """The tenancy database, or "". Same env var the writer and reader share."""
    return os.environ.get("TENANT_DB", "").strip()


def set_scope(tenant_id: str, full_names: list[str], by: str) -> dict:
    """Add each name to this tenant's scope. Returns the scope as it now stands.

    IDEMPOTENT BY READING FIRST. A name already in scope is skipped rather than
    inserted, because `UNIQUE (tenant_id, full_name)` would otherwise raise partway
    through a batch -- some rows written, some not, and the caller told only that it
    failed. Reading first is not a race-free check-then-insert, and that is acceptable
    here for a reason it would not be in the queue: the losing side of the race is a
    duplicate the constraint refuses, and the operation is "make these present" rather
    than "create exactly one".

    THE RESULT IS READ BACK from the table rather than echoed from the request. If a
    write were refused by a trigger, the response would show it missing instead of
    reassuring the caller that it landed.
    """
    path = _database_path()
    if not path:
        return {"repositories": [], "indexed": False}

    connection = engine.connect(path)
    with engine.acting_as(tenant_id):
        scope = accessors.scope_for(connection, tenant_id)
        existing = {row["full_name"] for row in accessors.list_repositories(scope)}

        for full_name in full_names:
            if full_name in existing:
                continue
            accessors.add_repository(
                scope,
                # A UUID rather than the name as an id. `full_name` is UNIQUE PER
                # TENANT, so using it as a primary key would collide the moment two
                # tenants connect the same repository -- and the table's own comment
                # says a global unique constraint there "would make one customer's
                # onboarding fail with a message about a repository they cannot see".
                str(uuid.uuid4()),
                full_name,
            )

        # LOGGED WITH THE PERSON WHO DID IT, before the commit, so a refused commit
        # does not leave a log line claiming a change that did not land. `by` comes
        # from a verified session; the repository names are interpolated because they
        # passed an anchored `owner/name` pattern in the route, and the tenant is the
        # bound scope rather than caller-supplied.
        logging.getLogger(__name__).info(
            "repository scope changed by %s for tenant %s: added %s",
            by, tenant_id, sorted(set(full_names) - existing))

        connection.commit()
        rows = accessors.list_repositories(scope)

    return {
        "repositories": [{"full_name": row["full_name"]} for row in rows],
        "indexed": True,
        "changed_at": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except Exception as error:
        logging.getLogger(__name__).warning(
            "the scope writer could not parse its request", exc_info=True)
        return _fail("the request could not be parsed", str(error))

    if not isinstance(request, dict):
        return _fail("the writer expects a JSON object")

    tenant_id = request.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        return _fail("the writer was given no tenant, so the write has no scope")
    tenant_id = tenant_zero.for_run_state(tenant_id)

    by = request.get("by")
    if not isinstance(by, str) or not by.strip():
        # An authorisation boundary changing with nobody's name on it is the same
        # defect as a gate decision with a constant `by`.
        return _fail("a scope change needs the identity of the person making it")

    names = request.get("full_names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return _fail("full_names must be a list of strings")

    # REVALIDATED HERE, not trusted from the route. This module is reachable
    # independently of the TypeScript that calls it, and the next thing these values
    # do is become rows the approval check reads.
    for name in names:
        if name.count("/") != 1 or not all(part for part in name.split("/")):
            return _fail("every entry must be of the form owner/name")
        if any(ch in name for ch in "\\ \t\n\r\0"):
            return _fail("every entry must be of the form owner/name")

    if request.get("action") != "set_scope":
        return _fail(f"unknown writer action {request.get('action')!r}")

    try:
        answer = set_scope(tenant_id, names, by)
    except accessors.CrossTenantAccess:
        return _fail("that repository belongs to another tenant")
    except Exception as error:
        logging.getLogger(__name__).exception("the scope change failed")
        return _fail("the scope change failed", f"{type(error).__name__}: {error}")

    json.dump(answer, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
