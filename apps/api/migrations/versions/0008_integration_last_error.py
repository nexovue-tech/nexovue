"""an integration says why it is failing, not just that it is

`status` alone tells a customer something is wrong; it does not tell them a
trust policy is missing our account, or that the ExternalId does not match.
Those are the two things that actually go wrong here and both are fixed in the
customer's own AWS account, so the message has to reach them.

Nothing secret goes in this column. ADR 0076 means we hold no credential to
leak, and the text comes from AWS describing a permission decision.

Revision ID: 0008
Revises: 0007
Created: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("integrations", sa.Column("last_error", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("integrations", "last_error")
