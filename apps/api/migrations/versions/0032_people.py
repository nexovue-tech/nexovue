"""people: invitations, removal, and an audit index the audit screen can page

F26 (`docs/features/team-and-api-access/`), Stage 1.

**Why this change is required.** An organization gains a second person. An
invited person is a `users` row with no password — the state
`auth/credentials.py` already refuses to sign in — plus a set-password link the
invite email carries. The link lives on the row, not in the Redis one-time
store, because a pending invite is listed with its expiry and a resend must kill
the link it replaces; the Redis store can do neither. A removed person keeps
their row so the audit log and "muted by" keep their name (a delete would `SET
NULL` every reference), and is marked removed instead.

**Affected store and schema.** Postgres only.
- `users.set_password_token_hash` (SHA-256 hex of a 32-byte link; unique when
  set, so accepting finds one row), `users.set_password_expires_at` (both set or
  both null), `users.invite_sent_at` (null means the invite email did not go),
  `users.removed_at`.
- `audit_log.detail` (JSONB, nullable): what changed, for the entries that
  change something about a person — a role from `member` to `viewer`, the
  address an invite went to. `target` names *what* was acted on; it had no
  room for *how*, and the audit screen shows both (FR36).
- `ix_audit_log_org_id_at (org_id, at)` becomes `(org_id, at, id)`: the audit
  screen pages newest first by `(at, id)`, and `at` alone does not order rows
  written in the same microsecond.

**Compatibility and backfill.** None: every existing user has a password, is
active, and was never invited. nexovue is pre-deployment.

**Rollback and recovery.** `downgrade` drops the columns and restores the old
index. Invitations pending at the time are lost with their columns.

**Tenant, retention, and security impact.** No new table. The link's plaintext
is never stored. The lookup by link hash runs before a tenant is known, and is
named in `test_postgres_tenancy_structure.py`'s exemptions with that reason.

Revision ID: 0032
Revises: 0031
Created: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("set_password_token_hash", sa.String(64), nullable=True))
    op.add_column(
        "users", sa.Column("set_password_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "set_password_link_and_expiry_agree",
        "users",
        "(set_password_token_hash IS NULL) = (set_password_expires_at IS NULL)",
    )
    op.create_index(
        "uq_users_set_password_token_hash",
        "users",
        ["set_password_token_hash"],
        unique=True,
        postgresql_where=sa.text("set_password_token_hash IS NOT NULL"),
    )
    op.add_column(
        "audit_log", sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.drop_index("ix_audit_log_org_id_at", table_name="audit_log")
    op.create_index("ix_audit_log_org_id_at_id", "audit_log", ["org_id", "at", "id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_org_id_at_id", table_name="audit_log")
    op.create_index("ix_audit_log_org_id_at", "audit_log", ["org_id", "at"])
    op.drop_column("audit_log", "detail")
    op.drop_index("uq_users_set_password_token_hash", table_name="users")
    op.drop_constraint("set_password_link_and_expiry_agree", "users", type_="check")
    op.drop_column("users", "removed_at")
    op.drop_column("users", "invite_sent_at")
    op.drop_column("users", "set_password_expires_at")
    op.drop_column("users", "set_password_token_hash")
