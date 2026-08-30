"""the billing unit ADR 0056 chose, and a price that does not rewrite history

Three things, all of them missing before this:

**`usage_daily_services`** — the billable quantity. ADR 0056 prices per service
monitored, and `usage_daily` counts spans, logs, points and bytes: the four
numbers a per-GB bill would want and none the chosen model needs. A distinct
count is not a sum, so it cannot be a column that a flush adds to. It is a set,
one row per service a project actually sent something for on a day, and the
count is `count(*)` over it. Set membership is what makes the meter's
at-least-once redelivery harmless: inserting a name twice leaves the same set.

**`plan_prices`** — the price, in its own table with an effective date rather
than a column on `plans`. A price column is an UPDATE, and an UPDATE restates
what every past invoice was computed from. Rows here are appended and never
edited, so the bill for a day reads the row that was in force on that day.
**Seeded with nothing on purpose**: ADR 0056 fixed the unit and not the amounts,
and an invented price is a number a customer would read as a bill.

**`usage_ingest_offsets.counted_through_received_at`** — how far the meter has
counted in receive time, not just in offsets. Without it nothing can say whether
a day is finished: "yesterday" is not the answer when the meter spent the night
catching up, and a day still being counted must not be presented as a settled
one.

Revision ID: 0010
Revises: 0009
Created: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A `service.name` is a resource attribute with no length limit in OTLP. This is
# the width stored; the meter truncates to it, so an absurd name is recorded as
# a service rather than rejecting the flush that carried it.
SERVICE_NAME_LENGTH = 255

# ISO 4217. Three characters, and the code is stored beside the amount because
# an amount without one is not a price.
CURRENCY_CODE_LENGTH = 3


def upgrade() -> None:
    op.create_table(
        "usage_daily_services",
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("service_name", sa.String(SERVICE_NAME_LENGTH), primary_key=True),
        # When this service was first billable on this day. Not part of the key:
        # the row is a membership fact, and a replay must not move it.
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "plan_prices",
        sa.Column(
            "plan_slug",
            sa.String(64),
            sa.ForeignKey("plans.slug", ondelete="RESTRICT"),
            primary_key=True,
        ),
        # The day this price starts applying, inclusive. There is no end date:
        # the next row's start is the end, so two rows cannot leave a gap or
        # overlap.
        sa.Column("effective_from", sa.Date(), primary_key=True),
        sa.Column("currency", sa.String(CURRENCY_CODE_LENGTH), nullable=False),
        # Minor units, not a float: money in binary floating point rounds, and
        # cents are whole. ADR 0056's unit — one service monitored.
        sa.Column("price_per_service_cents", sa.BigInteger(), nullable=False),
        # ADR 0056's ceiling on the service-count component. NULL is uncapped,
        # which is a different statement from a cap of zero.
        sa.Column("max_billed_services", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("price_per_service_cents >= 0", name="price_not_negative"),
        sa.CheckConstraint(
            "max_billed_services IS NULL OR max_billed_services > 0",
            name="ceiling_positive",
        ),
    )

    # Nullable, and it means "this partition has never been metered by a worker
    # that records receive time". A day cannot be called settled while any
    # partition is unknown, so the null is read as "not yet", not as zero.
    op.add_column(
        "usage_ingest_offsets",
        sa.Column("counted_through_received_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("usage_ingest_offsets", "counted_through_received_at")
    op.drop_table("plan_prices")
    op.drop_table("usage_daily_services")
