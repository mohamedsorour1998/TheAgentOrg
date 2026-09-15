"""The DynamoDB table handle the readers use. Steps 6 and 8 of the migration plan.

**THIS REPLACES `engine.connect()` + `engine.acting_as()`, AND THE THING IT
REPLACES DID MORE THAN OPEN A SOCKET.** Those two calls registered
`current_tenant()` as an application-defined SQL function and pushed the tenant
into a Postgres session so RLS could see it. This does the equivalent, by a
different mechanism: the tenant is not a session setting, it is (a) the PARTITION
KEY every accessor builds from its `tenant_id` argument, and (b) since step 6, an
IAM session tag baked into the credential this handle is built from.

**THE CREDENTIAL IS SCOPED, AND THAT IS NEW AS OF 2026-09-15.** Step 8 repointed
these readers at DynamoDB and left them on `boto3.resource("dynamodb")` -- the
ambient credential, which under Amplify's SSR runtime is a role that can read
EVERY tenant's rows. Correct output, no isolation: the only thing keeping one
tenant out of another's data was the `tenant_id` argument being right at every
call site. `agentorg/db/tenant_credentials.py` mints keys that cannot read
another partition, so a wrong argument now fails closed at AWS instead of
returning somebody else's rows.

Two layers, and the design needs both. IAM cannot make a caller ask the right
question; application code cannot stop it asking the wrong one.

**`table()` TAKES THE TENANT AND HAS NO DEFAULT.** That is the whole lesson of
the Postgres path restated: forgetting `acting_as` produced an EMPTY result, not
an error, which is the worst failure available to a scoping mechanism because it
is indistinguishable from "this tenant has no data". A required positional
argument makes the same mistake a `TypeError` at the call site.

**NO AMBIENT FALLBACK, EVER.** If `TENANT_SCOPED_ROLE_ARN` is unset this raises.
The one-line alternative -- fall back to `boto3.resource("dynamodb")` when no
role is configured -- passes every test in both suites and silently restores
exactly the unscoped access this module exists to remove, on precisely the
machines where somebody forgot to set a variable. See `tenant_credentials`.
"""

from __future__ import annotations

import os

# The table name, defaulted to match `infra/Terraform/modules/tenancy`'s own
# default. Two places, one value -- the same coupling `STATE_TABLE` has with
# `modules/state`, and it is deliberate: a derived name would hide the fact that
# changing one requires changing the other.
TABLE_NAME = os.environ.get("TENANCY_TABLE", "theagentorg-tenancy")

# KEYED BY TENANT, for `tenant_credentials._CACHE`'s reason, which is the same
# hazard one layer up: a single `_TABLE` slot would hand the second tenant of a
# process a handle built from the first tenant's credential. The rows would come
# back and nothing would raise.
_TABLES: dict[str, object] = {}


def table(tenant_id: str):
    """The tenancy table, reachable only for `tenant_id`.

    Cached per tenant because each reader is a SHORT-LIVED SUBPROCESS --
    `web/lib/pipeline.ts` spawns one python per read -- so the cache saves nothing
    across requests and everything within one. Building a client costs roughly
    0.2s, measured on the Postgres path as the cost of importing four packages,
    and `runs.py` and `repositories.py` each build one and use it twice.

    Imported inside the function, following `tenant_credentials`' own rule: the
    readers are spawned with `PYTHONPATH=REPO_ROOT`, so `agentorg` resolves, but a
    module-scope import would make merely loading this file reach for boto3.
    """
    if tenant_id not in _TABLES:
        from agentorg.db import tenant_credentials

        _TABLES[tenant_id] = tenant_credentials.table(tenant_id)
    return _TABLES[tenant_id]
