"""Tenancy: organisations, scoped access, per-tenant secrets and budgets.

OWNER: Lane B. The decision this package implements is recorded in
`ADR-001-database.md`, including the part that is easy to overstate -- exactly which
isolation is enforced by the database and which by these accessors.

WHAT TO READ FIRST, if you are changing anything here:

  * `accessors.py` -- every read and write takes a tenant, first positional, no default.
  * `crypto.py`    -- secrets are encrypted here and nowhere else; nothing is ever logged.
  * `budgets.py`   -- a tenant with no budget row is REFUSED, not admitted.
  * `tenant_zero.py` -- `RunState.tenant_id == ""` is TRANSLATED, never rewritten.

`tests/test_tenancy_leak.py` is the file that matters most: it attempts a cross-tenant
breach on every registered accessor rather than asserting isolation, because an assertion
that data is absent passes for several wrong reasons and an attempt that must be refused
passes for only one.
"""

from .accessors import (
    ACCESSORS,
    Accessor,
    CrossTenantAccess,
    NotFound,
    TenantScope,
    reads,
    scope_for,
    writes,
)
from .budgets import ALLOWED, NO_BUDGET, OVER_CEILING, BudgetDecision
from .budgets import check as check_budget
from .crypto import (
    CIPHER_LOCAL_V1,
    EncryptedRecord,
    MacMismatch,
    SecretKeyMissing,
    decrypt,
    encrypt,
)
from .tenant_zero import TENANT_ZERO_ID, adopt, for_run_state, is_tenant_zero

__all__ = [
    "ACCESSORS",
    "ALLOWED",
    "CIPHER_LOCAL_V1",
    "NO_BUDGET",
    "OVER_CEILING",
    "TENANT_ZERO_ID",
    "Accessor",
    "BudgetDecision",
    "CrossTenantAccess",
    "EncryptedRecord",
    "MacMismatch",
    "NotFound",
    "SecretKeyMissing",
    "TenantScope",
    "adopt",
    "check_budget",
    "decrypt",
    "encrypt",
    "for_run_state",
    "is_tenant_zero",
    "reads",
    "scope_for",
    "writes",
]
