"""a session lives in Redis, and the table that said otherwise goes

`sessions` was created in 0001 and never written. Every authentication path
reads `auth/sessions.py`, which keys Redis on a fingerprint of the token and
lets the key's own TTL be the expiry; 0019 then chose an epoch column on `users`
over any per-user index precisely because nothing enumerates sessions. The
table's docstring promised "revoking one is immediate", and the code delivers
that promise through Redis, not through this table.

**An empty table is not free.** The deletion inventory listed it as a cascade
from `users`, the deletion suite seeded rows into it to prove that cascade, and
the ORM carried a model for it — three places maintaining a claim about where
sessions live that the code does not honour. A reader tracing "where are
sessions stored" met two answers and had to work out which was the real one.

Dropped rather than kept for a future server-side store: if that decision is
ever taken it will want a different shape (0019's reasoning about indexing per
user applies), and a table designed before the decision would be rebuilt anyway.

Revision ID: 0024
Revises: 0023
Created: 2026-09-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")


def downgrade() -> None:
    # The shape 0001 created, so a rollback lands on a database 0023 recognises.
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuidv7()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
