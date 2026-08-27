"""widen the api key lookup prefix

`key_prefix` was 12 characters — `nvk_live_` plus three random base62 — giving
238,328 distinct values under a unique constraint. By the birthday bound that is
a coin flip at roughly 500 live keys, and a collision does not degrade anything
gracefully: it fails a real customer's signup with a conflict caused by someone
else's key.

Sixteen characters gives seven random ones, 3.5e12 values, and leaves fifteen
characters of secret the prefix never reveals.

Revision ID: 0002
Revises: 0001
Created: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS_LENGTH = 12
CURRENT_LENGTH = 16


def upgrade() -> None:
    op.alter_column(
        "api_keys",
        "key_prefix",
        existing_type=sa.String(PREVIOUS_LENGTH),
        type_=sa.String(CURRENT_LENGTH),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Truncating would collapse distinct prefixes into duplicates and break the
    # unique constraint, so rows written at the wider length must go first.
    op.execute(sa.text(f"DELETE FROM api_keys WHERE length(key_prefix) > {PREVIOUS_LENGTH}"))
    op.alter_column(
        "api_keys",
        "key_prefix",
        existing_type=sa.String(CURRENT_LENGTH),
        type_=sa.String(PREVIOUS_LENGTH),
        existing_nullable=False,
    )
