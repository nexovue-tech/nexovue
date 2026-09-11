"""integrations stop pretending to hold an encrypted secret, and gain what a poll needs

Door 4b holds no customer credential it can avoid holding: the customer grants a
role we assume, and we keep a role ARN and an ExternalId, neither of which is
secret. That leaves three problems in this table.

`config_encrypted` holds plain UTF-8 JSON. A column named `_encrypted` that is
not is worse than either honest name — a future reader assumes a protection that
is not there — so it becomes `config`, and JSONB rather than bytes, because that
is what it has always contained.

`external_id` gets a column of its own rather than living inside `config`. AWS is
explicit that it must be unguessable and must not be chosen or edited by the
customer; a key inside a JSON blob the customer submits is exactly that.

`interval_seconds` was being read out of `config` with a default, which meant the
scheduler could not see how often to poll without parsing a blob.

Revision ID: 0006
Revises: 0005
Created: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches the scheduler's own floor. A poll cheaper than this costs more in API
# calls than the resolution is worth, and CloudWatch itself publishes at 60s.
DEFAULT_INTERVAL_SECONDS = 300


def upgrade() -> None:
    # The column has only ever held UTF-8 JSON, so the cast is exact rather than
    # lossy. `USING` is required: Postgres will not guess how bytes become JSON.
    op.execute(
        "ALTER TABLE integrations "
        "ALTER COLUMN config_encrypted TYPE JSONB "
        "USING convert_from(config_encrypted, 'UTF8')::jsonb"
    )
    op.alter_column("integrations", "config_encrypted", new_column_name="config")

    op.add_column(
        "integrations",
        sa.Column("external_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column(
            "interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_INTERVAL_SECONDS),
        ),
    )
    op.create_check_constraint(
        "ck_integrations_interval_positive", "integrations", "interval_seconds > 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_integrations_interval_positive", "integrations", type_="check")
    op.drop_column("integrations", "interval_seconds")
    op.drop_column("integrations", "external_id")
    op.alter_column("integrations", "config", new_column_name="config_encrypted")
    # Back to bytes holding the same JSON text, which is what was there before.
    op.execute(
        "ALTER TABLE integrations "
        "ALTER COLUMN config_encrypted TYPE BYTEA "
        "USING convert_to(config_encrypted::text, 'UTF8')"
    )
