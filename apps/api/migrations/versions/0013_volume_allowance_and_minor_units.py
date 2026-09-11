"""the volume allowance the pricing model required, and the rename to minor units

**`plan_prices.included_bytes_per_service`** — the allowance the pricing model
priced against and never gave a unit for. Bytes: the only unit that spans
traces, logs and metrics, and the one that actually costs storage and transfer.
Counting spans instead would charge a well-instrumented service more for being
well instrumented, which is the opposite of what this product wants to
encourage. NULL means no allowance has been recorded — not an allowance of zero
— and the table ships empty anyway, so nothing is invented here.

**`price_per_service_cents` → `price_per_service_minor_units`** — the rename
that was waiting for the next migration touching this table. The column holds
minor units, and the yen's minor unit is the yen: the old name invites a divide
by a hundred that would quote a yen bill at a hundredth of its size. The table
has never held a row in any database, so the rename moves no data.

Revision ID: 0013
Revises: 0011
Created: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "plan_prices",
        "price_per_service_cents",
        new_column_name="price_per_service_minor_units",
    )
    op.drop_constraint("price_not_negative", "plan_prices", type_="check")
    op.create_check_constraint(
        "price_not_negative", "plan_prices", "price_per_service_minor_units >= 0"
    )

    op.add_column(
        "plan_prices",
        # Per billed service, per calendar month. NULL is "no allowance
        # recorded", which is why overage cannot be computed from a NULL row
        # rather than being computed as if everything were over.
        sa.Column("included_bytes_per_service", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "allowance_not_negative",
        "plan_prices",
        "included_bytes_per_service IS NULL OR included_bytes_per_service >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("allowance_not_negative", "plan_prices", type_="check")
    op.drop_column("plan_prices", "included_bytes_per_service")

    op.drop_constraint("price_not_negative", "plan_prices", type_="check")
    op.alter_column(
        "plan_prices",
        "price_per_service_minor_units",
        new_column_name="price_per_service_cents",
    )
    op.create_check_constraint("price_not_negative", "plan_prices", "price_per_service_cents >= 0")
