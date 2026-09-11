"""the ceiling the customer chose is stored on the mandate

The ceiling decision's third part — *"the customer can raise their own ceiling,
in advance, to a number they choose"* — listed the customer-set ceiling as "a
stored value" that did not exist yet. It still did not: `StartCheckoutRequest`
carried `services_committed`, `authorised_maximum_for` multiplied it by the rate
in force to size the mandate, and then the number was discarded. Nothing could
read back what the customer committed to, so nothing could show it, change it,
or enforce against it.

**On the arrangement, not on the tenant.** The number is what one mandate
authorised a bank to debit, and a bank authorises a specific maximum. Raising
the ceiling is therefore a new mandate at a new maximum, which is a new row —
`billing_subscriptions` already accumulates rows across arrangements for the
same reason, so the ceiling ages with the arrangement it belonged to. A column
on `organizations` would hold one number that silently outlived the mandate that
justified it.

**NOT NULL with no default.** Every row is written by `begin_plan_change`, which
has the customer's own number in hand; there is no path that creates an
arrangement without one. A default would invent a commitment nobody made, and a
nullable column would push "the customer chose nothing" onto every reader of a
value that is always chosen.

This stores the ceiling. It does not enforce it — the enforcement path says so
plainly: `allowance/postgres.go` returns unenforced when a plan's
`max_billed_services` is NULL, which it is on every paid tier, so a paid plan
takes an early return no cap is reachable from. That path is its own work.

Revision ID: 0022
Revises: 0021
Created: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "billing_subscriptions",
        sa.Column(
            "services_committed",
            sa.Integer(),
            nullable=False,
            comment=(
                "How many services the customer committed to pay for when this "
                "arrangement was started. The mandate's authorised maximum is this "
                "count at the rate in force, so it is the customer-set "
                "ceiling. Never edited: a new ceiling is a new mandate and a new row."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("billing_subscriptions", "services_committed")
