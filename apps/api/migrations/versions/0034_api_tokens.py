"""api tokens: an organization's own read credential, and what it spends

F36 (`docs/features/team-and-api-access/`), Stage 2; ADR-017.

**Why this change is required.** A customer's CI, script or AI assistant has no
way to read its own telemetry: the console's API takes a session cookie, and
ingest keys write and never read (BR8). An API token is the organization's, not
a person's, so a teammate leaving does not break a pipeline (BR9, AC17).

- `api_tokens`: the credential. `project_id` NULL means every project; a row is
  scoped to one otherwise (BR11). `expires_at` is NOT NULL — every token ends,
  and the customer chooses when. `revoked_at` withdraws one immediately without
  losing the record of what existed. `created_by` is a label for the list, and
  goes to NULL when that person is removed rather than taking the token with
  them.
- `api_token_usage_daily`: how many requests a token made on a day, folded from
  Redis at most once a minute per token, so the token list can show a customer
  what their own automation spends without a Postgres write per request.

**Affected store and schema.** Postgres only: two tables.

**Compatibility and backfill.** Both start empty; nothing reads them until the
read surface is wired to them. nexovue is pre-deployment.

**Rollback and recovery.** `downgrade` drops both. Every token is lost with the
table, which is the same as revoking them: a customer mints a new one. No
telemetry, project or member row is touched.

**Tenant, retention, and security impact.** `api_tokens` carries `org_id` and
cascades with the organization; the counters cascade with their token. The
secret is stored as a SHA-256 hash, as ingest keys are — `token_prefix` is the
only plaintext, and it is the part that indexes the lookup, not the part that
authenticates. There is no retention job: a revoked token's row is the record
that it existed.

Revision ID: 0034
Revises: 0033
Created: 2026-09-20
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)


def _id() -> sa.Column[Any]:
    return sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()"))


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        _id(),
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NULL is "every project in this organization", which is why there is no
        # separate scope column: one nullable foreign key says both things.
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("name", sa.String(200), nullable=False),
        # One role today. A column rather than an assumption, so the read-only
        # rule is a value someone can see rather than code nobody reads.
        sa.Column("role", sa.String(64), nullable=False, server_default=sa.text("'read'")),
        sa.Column("token_prefix", sa.String(20), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", _TIMESTAMP, nullable=False),
        sa.Column("revoked_at", _TIMESTAMP, nullable=True),
        sa.Column("last_used_at", _TIMESTAMP, nullable=True),
        sa.CheckConstraint("role IN ('read')", name="token_role_is_known"),
        sa.CheckConstraint("expires_at > created_at", name="token_expires_after_it_is_made"),
    )
    op.create_index("ix_api_tokens_org_id", "api_tokens", ["org_id"])

    op.create_table(
        "api_token_usage_daily",
        sa.Column(
            "token_id",
            _UUID,
            sa.ForeignKey("api_tokens.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("requests", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("requests >= 0", name="token_requests_do_not_go_backwards"),
    )


def downgrade() -> None:
    op.drop_table("api_token_usage_daily")
    op.drop_index("ix_api_tokens_org_id", table_name="api_tokens")
    op.drop_table("api_tokens")
