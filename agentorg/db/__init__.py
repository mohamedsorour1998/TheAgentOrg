"""The database layer: schema as data, one engine, forward-only migrations.

OWNER: Lane B. `agentorg/tenancy/ADR-001-database.md` records the decision.

Deliberately thin re-exports. The submodules carry the reasoning; this file exists so a
caller writes `from agentorg.db import connect, migrate` rather than reaching three
modules deep, and so `agentorg.db`'s public surface is one readable list.
"""

from .engine import (
    TenantNotBound,
    acting_as,
    acting_as_nobody,
    connect,
    current_tenant,
    require_tenant,
)
from .migrations import MIGRATIONS, applied_versions, migrate
from .schema import (
    DIALECTS,
    POSTGRES,
    SCOPED_TABLES,
    SQLITE,
    TABLES,
    TABLES_BY_NAME,
    TENANT_ZERO_ID,
    UNSCOPED_TABLES,
    Column,
    Table,
    render_schema,
)

__all__ = [
    "DIALECTS",
    "MIGRATIONS",
    "POSTGRES",
    "SCOPED_TABLES",
    "SQLITE",
    "TABLES",
    "TABLES_BY_NAME",
    "TENANT_ZERO_ID",
    "UNSCOPED_TABLES",
    "Column",
    "Table",
    "TenantNotBound",
    "acting_as",
    "acting_as_nobody",
    "applied_versions",
    "connect",
    "current_tenant",
    "migrate",
    "render_schema",
    "require_tenant",
]
