"""users: failed sign-ins in a row, and the lock they earn

Readiness group 9, A1 (`docs/changes/remaining-recommendations/`).

**Why this change is required.** Sign-in was limited per address and per email
for a minute only, so one account could take ~14,400 guesses a day from rotating
addresses. `failed_logins` counts failures in a row; `locked_until` is the
temporary lock they earn (`auth/lockout.py`).

**Affected store and schema.** Postgres: two columns on `users`.

**Compatibility and backfill.** None: every account starts at 0 and unlocked.

**Rollback and recovery.** `downgrade` drops both columns; nothing else reads them.

**Tenant, retention, and security impact.** Columns on a tenant-scoped table,
deleted with the user. They hold counts and a time, nothing personal.

Revision ID: 0039
Revises: 0038
Created: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_logins")
