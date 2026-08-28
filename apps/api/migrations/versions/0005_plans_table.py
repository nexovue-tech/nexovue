"""plan tiers, and the retention each one grants

ADR 0071 decided the tiers; this is where their numbers live. They are rows
rather than constants deliberately — retention days and, later, prices are the
values most likely to change after contact with customers, and changing a row
should not need a deploy.

`organizations.plan` becomes a foreign key. It has held a free-text default of
'free' since the first migration with nothing to check it against, so a typo
would silently grant whatever retention the resolver defaulted to.

Retention is resolved per project, not per organization: a project may ask for
less than its plan grants (staging rarely wants thirty days), never more. See
`plans.service.effective_retention`.

Revision ID: 0005
Revises: 0004
Created: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ADR 0071. Seeded, not hardcoded anywhere else: these rows are the source of
# truth and are expected to be edited before launch.
SEED: Sequence[tuple[str, str, int, int]] = (
    ("free", "Free", 7, 30),
    ("pro", "Pro", 30, 395),
    ("scale", "Scale", 90, 395),
    ("enterprise", "Enterprise", 90, 395),
)


def upgrade() -> None:
    plans = op.create_table(
        "plans",
        sa.Column("slug", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        # Spans and logs — what a customer reads while an incident is warm.
        sa.Column("raw_retention_days", sa.Integer(), nullable=False),
        # Metrics and rollups — small, and what answers "was it always this
        # slow". 395 days is the 13 months endpoint_stats_1m already keeps.
        sa.Column("derived_retention_days", sa.Integer(), nullable=False),
        sa.CheckConstraint("raw_retention_days > 0", name="ck_plans_raw_retention_positive"),
        sa.CheckConstraint(
            "derived_retention_days >= raw_retention_days",
            name="ck_plans_derived_covers_raw",
        ),
    )
    op.bulk_insert(
        plans,
        [
            {"slug": s, "name": n, "raw_retention_days": r, "derived_retention_days": d}
            for s, n, r, d in SEED
        ],
    )
    op.create_foreign_key(
        "fk_organizations_plan", "organizations", "plans", ["plan"], ["slug"], ondelete="RESTRICT"
    )
    # One definition of "what does this project actually keep", so the worker
    # and the control plane cannot drift. LEAST, not the plan value: a project
    # may ask for less than its tier grants, never more.
    op.execute("""
        CREATE VIEW project_retention AS
        SELECT projects.id                                             AS project_id,
               organizations.id                                        AS org_id,
               LEAST(projects.retention_days, plans.raw_retention_days) AS raw_retention_days,
               plans.derived_retention_days                            AS derived_retention_days
        FROM projects
        JOIN organizations ON organizations.id = projects.org_id
        JOIN plans         ON plans.slug = organizations.plan
        WHERE projects.deleted_at IS NULL AND organizations.deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS project_retention")
    op.drop_constraint("fk_organizations_plan", "organizations", type_="foreignkey")
    op.drop_table("plans")
