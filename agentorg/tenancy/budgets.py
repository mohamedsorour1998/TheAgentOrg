"""Budgets: a tenant's ceiling, checked BEFORE a run starts. Lane B, B5.

OWNER: Lane B.

THE ONE DESIGN DECISION HERE IS THE DIRECTION OF THE DEFAULT, and it is the same
decision `config.STATE_BACKEND` makes about an unknown value and `RunState.
ci_status_measured` makes about a blank: **a tenant with no budget row is REFUSED, not
admitted.**

Written the other way -- no row means unlimited -- the failure is invisible and expensive.
A tenant onboarded by a code path that forgot to write a budget row would spend without
bound, every run would succeed, and the first signal would be an invoice. "Nobody
configured a ceiling" and "this tenant may spend freely" are different facts and must not
share a representation. So `unlimited` is a column somebody sets on purpose.

MONEY IS INTEGER CENTS, everywhere in this module and in the schema. A float ceiling
compared against a float spend is a rounding bug with a currency symbol in front of it,
and which way it rounds decides whether a run is admitted. There is no float in this file.

WHY THE CHECK RETURNS A DECISION OBJECT RATHER THAN A BOOL. A bool cannot carry why, and
the caller has to tell a human -- on a PR comment or a screen -- what happened. A bare
False would send the next person to read this source to find out which of three reasons
applied, which is exactly the "unclassified" failure `agent_client`'s classifier refuses
to produce.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import engine

# The reasons a budget check can come back refused. Named constants because they reach a
# rendered surface and a typo'd literal in one branch would read as a different reason.
ALLOWED = "allowed"
NO_BUDGET = "no-budget-configured"
OVER_CEILING = "over-ceiling"


@dataclass(frozen=True)
class BudgetDecision:
    """Whether a run may start, and why. Never a bare bool -- see the module docstring."""

    allowed: bool
    reason: str
    ceiling_cents: int | None = None
    spent_cents: int | None = None
    would_spend_cents: int | None = None

    def explain(self) -> str:
        """One line a human can act on. Safe to put in a comment or a log.

        Carries no tenant id: this string is rendered onto pull requests, and a tenant
        identifier in a message on somebody else's repository is a small cross-tenant
        disclosure of exactly the kind this lane exists to prevent.
        """
        if self.allowed:
            if self.ceiling_cents is None:
                return "within budget (unlimited)"
            return (
                f"within budget: {self.spent_cents}c spent of {self.ceiling_cents}c"
            )
        if self.reason == NO_BUDGET:
            return (
                "refused: no budget is configured for this tenant. A missing budget is "
                "a refusal rather than an unlimited one, deliberately."
            )
        return (
            f"refused: this run would spend {self.would_spend_cents}c, taking the total "
            f"past the {self.ceiling_cents}c ceiling ({self.spent_cents}c already spent)"
        )


def _row(scope) -> dict | None:
    """The tenant's budget row, read under its own scope.

    Takes the same `TenantScope` the accessors take rather than a connection plus a
    tenant id, so there is no second way to name a tenant in this package. Two ways would
    mean one of them could disagree with the guards.
    """
    with engine.acting_as(scope.tenant_id):
        row = scope.connection.execute(
            'SELECT "tenant_id", "ceiling_cents", "spent_cents", "unlimited" '
            'FROM "budget" WHERE "tenant_id" = ?',
            (scope.tenant_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def check(scope, would_spend_cents: int = 0) -> BudgetDecision:
    """Whether this tenant may start a run that would cost `would_spend_cents`.

    CALLED BEFORE THE RUN, which is the whole point of B5 -- a ceiling discovered on an
    invoice is not a ceiling. `would_spend_cents` defaults to 0 so a caller can ask "is
    this tenant in good standing" without estimating, and 0 is honest for that question:
    it tests the already-spent total against the ceiling.
    """
    if would_spend_cents < 0:
        raise ValueError(
            f"would_spend_cents={would_spend_cents} is negative; a refund is not a run. "
            f"Refused rather than clamped, because a negative estimate that silently "
            f"became 0 would admit a run whose caller had computed a cost wrongly."
        )

    row = _row(scope)
    if row is None:
        return BudgetDecision(allowed=False, reason=NO_BUDGET)

    if row["unlimited"]:
        return BudgetDecision(
            allowed=True,
            reason=ALLOWED,
            ceiling_cents=None,
            spent_cents=row["spent_cents"],
            would_spend_cents=would_spend_cents,
        )

    total = row["spent_cents"] + would_spend_cents
    return BudgetDecision(
        # `<=` and not `<`: a run that lands exactly on the ceiling is within it. The
        # boundary is stated here rather than left to the reader, because an off-by-one
        # on a ceiling refuses a run that should have gone and nothing says why.
        allowed=total <= row["ceiling_cents"],
        reason=ALLOWED if total <= row["ceiling_cents"] else OVER_CEILING,
        ceiling_cents=row["ceiling_cents"],
        spent_cents=row["spent_cents"],
        would_spend_cents=would_spend_cents,
    )
