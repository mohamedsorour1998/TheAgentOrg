"""The DynamoDB table handle the readers use. Step 8 of the migration plan.

**THIS REPLACES `engine.connect()` + `engine.acting_as()`, AND THE THING IT
REPLACES DID MORE THAN OPEN A SOCKET.** Those two calls registered
`current_tenant()` as an application-defined SQL function and pushed the tenant
into a Postgres session so RLS could see it. Nothing here does anything
equivalent, because nothing here needs to: the tenant is not a session setting,
it is the PARTITION KEY, and every accessor builds it from the `tenant_id`
argument rather than from ambient state.

That difference is worth stating plainly rather than leaving a reader to notice
the missing `acting_as`. Under Postgres, forgetting to bind the tenant produced
an EMPTY result -- not an error -- which is the worst failure available to a
scoping mechanism, since it is indistinguishable from "this tenant has no data".
Under this design the tenant is a required positional argument, so forgetting it
is a `TypeError` at the call site.

**NO CREDENTIALS ARE CONFIGURED HERE.** boto3 resolves them from the environment:
the Amplify SSR compute role in the deployed app, and the operator's own session
locally. There is no DSN, no password, and nothing for a compose file to carry --
which is the entire reason this migration happened, since Amplify's SSR compute
cannot join a VPC and could never have reached a private Postgres.
"""

from __future__ import annotations

import os

# The table name, defaulted to match `infra/Terraform/modules/tenancy`'s own
# default. Two places, one value -- the same coupling `STATE_TABLE` has with
# `modules/state`, and it is deliberate: a derived name would hide the fact that
# changing one requires changing the other.
TABLE_NAME = os.environ.get("TENANCY_TABLE", "theagentorg-tenancy")

_TABLE = None


def table():
    """The boto3 `Table` resource, built once per process.

    Cached because each reader is a SHORT-LIVED SUBPROCESS -- `web/lib/pipeline.ts`
    spawns one python per read -- so the cache saves nothing across requests and
    everything within one. Building a client costs roughly 0.2s, measured on the
    Postgres path as the cost of importing four packages, and a reader that built
    two would pay it twice for one screen.
    """
    global _TABLE
    if _TABLE is None:
        import boto3

        _TABLE = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _TABLE
