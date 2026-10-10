"""billing: a delivery names its event, and a failed payment gets a grace period

Readiness group 3 (`docs/changes/money-is-right/`).

**Why this change is required.**

- `billing_webhook_deliveries.delivery_id` could be `''`: a webhook with no event
  id was recorded under it, and every later id-less delivery was then dropped as
  its duplicate. The adapter now refuses one; the CHECK makes `''` unstorable.
- `subscription.halted` — the processor has given up on the payments — changed
  nothing, so the customer kept the paid tier for ever. The founder chose a
  7-day grace with an email, then the free tier (2026-10-10). `grace_until`
  holds the end of it; `lapsed` is the state after, distinct from `cancelled`
  because nobody ended it, and a late successful charge revives it.

**Affected store and schema.** Postgres: one enum value, one nullable column,
one CHECK.

**Compatibility and backfill.** None needed: no row has `''` (the only writer
was the adapter, pre-deployment), and `grace_until` starts empty.

**Rollback and recovery.** `downgrade` moves any `lapsed` row to `cancelled`,
rebuilds the enum without `lapsed` (Postgres cannot drop an enum value), and
drops the column and the CHECK.

**Tenant, retention, and security impact.** None: columns on tables already
tenant-scoped and erased with the organization.

Revision ID: 0038
Revises: 0037
Created: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD VALUE commits on its own; nothing below uses the new value.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE billing_subscription_status ADD VALUE IF NOT EXISTS 'lapsed'")
    op.add_column(
        "billing_subscriptions",
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "a_delivery_names_its_event", "billing_webhook_deliveries", "delivery_id <> ''"
    )


def downgrade() -> None:
    op.drop_constraint("a_delivery_names_its_event", "billing_webhook_deliveries", type_="check")
    op.drop_column("billing_subscriptions", "grace_until")
    op.execute("UPDATE billing_subscriptions SET status = 'cancelled' WHERE status = 'lapsed'")
    op.execute("ALTER TYPE billing_subscription_status RENAME TO billing_subscription_status_old")
    op.execute("CREATE TYPE billing_subscription_status AS ENUM ('pending', 'active', 'cancelled')")
    op.execute(
        "ALTER TABLE billing_subscriptions ALTER COLUMN status TYPE billing_subscription_status "
        "USING status::text::billing_subscription_status"
    )
    op.execute("DROP TYPE billing_subscription_status_old")
