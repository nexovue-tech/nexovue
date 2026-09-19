"""admin: staff, regions, website payments, plan grants and support access

F31 (`docs/features/team-and-api-access/`), Stage 3; ADR-008, ADR-009, ADR-016.

**Why this change is required.** nexovue's own staff onboard paying customers
(ADR-008), suspend and comp organizations, and — with an owner's consent — view
the console as one of their people. Each needs a place to stand:

- `staff_accounts`: staff are not `users` (ADR-016). A row cannot exist
  without a sealed TOTP secret: the terminal command that creates one enrols
  the factor first.
- `organizations.region`: every organization belongs to exactly one region
  (ADR-009, *Prepared now* #3 — "added when F31 is built").
- `website_payments`: the payment the website took before the organization
  existed, recorded once so there is one answer to "has this customer paid".
- `plan_grants`: a complimentary plan until a date. The plan in force is an
  active grant if there is one, else the paid plan; nothing rewrites
  `organizations.plan`, so a grant ends by its own date and no job can fail to
  end it.
- `support_access_grants`: an owner's consent, for a time, to be viewed as.

**Affected store and schema.** Postgres only: four tables, one column.

**Compatibility and backfill.** Every existing organization is `india` by the
column's default. The new tables start empty. nexovue is pre-deployment.

**Rollback and recovery.** `downgrade` drops the tables and the column. A
recorded payment, a grant or a staff account is lost with its table; the
organization's paid plan is untouched throughout.

**Tenant, retention, and security impact.** The three organization tables carry
`org_id` and cascade with it. `staff_accounts` is deliberately tenant-free:
staff are cross-tenant by design, and no customer route reads it. The TOTP
secret is sealed as customers' are; no secret is stored in clear.

Revision ID: 0033
Revises: 0032
Created: 2026-09-20
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
# ADR-009's list. A region is added here, by a migration, when it exists.
REGIONS = ("india", "india-south")


def _id() -> sa.Column[Any]:
    return sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()"))


def _org() -> sa.Column[Any]:
    return sa.Column(
        "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def _staff(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, _UUID, sa.ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True
    )


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("region", sa.String(32), nullable=False, server_default=sa.text("'india'")),
    )
    op.create_check_constraint(
        "region_is_known",
        "organizations",
        "region IN (" + ", ".join(f"'{region}'" for region in REGIONS) + ")",
    )

    op.create_table(
        "staff_accounts",
        _id(),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        # Sealed; NOT NULL is the rule that no staff account exists without one.
        sa.Column("totp_secret", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("disabled_at", _TIMESTAMP, nullable=True),
    )

    op.create_table(
        "website_payments",
        _id(),
        _org(),
        sa.Column("razorpay_payment_id", sa.String(64), nullable=False, unique=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        _staff("recorded_by"),
        sa.Column("recorded_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("amount_minor > 0", name="payment_is_positive"),
        # One per organization: the payment that bought it. Renewals are billing's.
        sa.UniqueConstraint("org_id", name="uq_website_payments_org_id"),
    )

    op.create_table(
        "plan_grants",
        _id(),
        _org(),
        sa.Column(
            "plan_slug",
            sa.String(64),
            sa.ForeignKey("plans.slug", ondelete="RESTRICT"),
            nullable=False,
        ),
        # The services the allowance is sized on while the grant is in force.
        sa.Column("services", sa.Integer(), nullable=False),
        sa.Column("starts_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("ends_at", _TIMESTAMP, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _staff("granted_by"),
        # Ended early by staff; NULL means it runs to `ends_at`.
        sa.Column("ended_at", _TIMESTAMP, nullable=True),
        sa.CheckConstraint("ends_at > starts_at", name="grant_ends_after_it_starts"),
        sa.CheckConstraint("services > 0", name="grant_covers_a_service"),
    )
    op.create_index("ix_plan_grants_org_id_ends_at", "plan_grants", ["org_id", "ends_at"])

    op.create_table(
        "support_access_grants",
        _id(),
        _org(),
        sa.Column(
            "granted_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("granted_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", _TIMESTAMP, nullable=False),
        sa.Column("withdrawn_at", _TIMESTAMP, nullable=True),
        sa.CheckConstraint("expires_at > granted_at", name="access_expires_after_it_starts"),
    )
    op.create_index(
        "ix_support_access_grants_org_id_expires_at",
        "support_access_grants",
        ["org_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_access_grants_org_id_expires_at", table_name="support_access_grants")
    op.drop_table("support_access_grants")
    op.drop_index("ix_plan_grants_org_id_ends_at", table_name="plan_grants")
    op.drop_table("plan_grants")
    op.drop_table("website_payments")
    op.drop_table("staff_accounts")
    op.drop_constraint("region_is_known", "organizations", type_="check")
    op.drop_column("organizations", "region")
