"""audit_log records non-user actors and impersonation

Three changes, all cheap now because the table holds only the rows written since
writing to it began, and all expensive later because an audit record cannot be
reconstructed after the fact.

`org_id` becomes nullable so a refused sign-in can be recorded. A login attempt
against an address that matches no account has no organization to belong to, and
a NOT NULL column meant those attempts could not be written at all — leaving
brute force against the login endpoint invisible.

`actor_label` names an actor who is not a row in `users`. Our own staff are not
members of a customer's organization, so F31's impersonation would have recorded
them as a null actor, which already means "the system did it". The most
security-sensitive entries would have been the least attributable.

`impersonation_context` carries the impersonation session id on every entry
written while one is open. `requirements.md` §F31 asks for exactly this field at
exactly this moment: adding it as the first entries are written costs nothing,
and retrofitting it means backfilling a security record that cannot be
reconstructed.

Revision ID: 0004
Revises: 0003
Created: 2026-08-28
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Long enough for an email address, which is the longest thing a label holds.
ACTOR_LABEL_LENGTH: Final = 320


def upgrade() -> None:
    op.alter_column("audit_log", "org_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column(
        "audit_log", sa.Column("actor_label", sa.String(ACTOR_LABEL_LENGTH), nullable=True)
    )
    op.add_column("audit_log", sa.Column("impersonation_context", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "impersonation_context")
    op.drop_column("audit_log", "actor_label")
    # Entries with no organization cannot survive the column becoming NOT NULL.
    # They are refused sign-ins against unknown addresses; the alternative is a
    # downgrade that fails outright on a table that has recorded one.
    op.execute("DELETE FROM audit_log WHERE org_id IS NULL")
    op.alter_column("audit_log", "org_id", existing_type=sa.Uuid(), nullable=False)
