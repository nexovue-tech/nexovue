"""plan_prices: the fewest services a plan can be bought for

Founder decision, 2026-10-31 (`docs/changes/website-follow-ups/`).

**Why this change is required.** Scale costs less per service than Pro and includes more
volume and retention, so with no minimum it beats Pro at every size and nobody has a reason
to choose Pro.

**Affected store and schema.** Postgres: one nullable column on `plan_prices`, set to 10 on
Scale's rows.

**Compatibility and backfill.** Scale's existing rows are updated rather than repriced with a
new `effective_from`. That rule exists so a past invoice can be recomputed from what it billed
on, and a minimum plays no part in that sum. Nexovue is pre-deployment, so no mandate is
affected.

**Rollback and recovery.** `downgrade` drops the column; nothing is lost but the minimum.

**Tenant, retention, and security impact.** None: `plan_prices` is global and holds no
customer data.

Revision ID: 0040
Revises: 0039
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCALE_MINIMUM = 10


def upgrade() -> None:
    op.add_column(
        "plan_prices",
        sa.Column(
            "min_committed_services",
            sa.Integer(),
            nullable=True,
            comment="The fewest services this plan can be bought for. NULL is no minimum.",
        ),
    )
    op.create_check_constraint(
        "min_committed_services_positive",
        "plan_prices",
        "min_committed_services IS NULL OR min_committed_services >= 1",
    )
    op.execute(
        sa.text(
            "UPDATE plan_prices SET min_committed_services = :minimum WHERE plan_slug = 'scale'"
        ).bindparams(minimum=SCALE_MINIMUM)
    )


def downgrade() -> None:
    op.drop_constraint("min_committed_services_positive", "plan_prices", type_="check")
    op.drop_column("plan_prices", "min_committed_services")
