"""super admin: one platform role, and the end of a separate staff account

`docs/changes/super-admin-in-the-console/`; supersedes ADR-016's staff table.

**Why this change is required.** Staff were their own kind of account —
`staff_accounts`, their own app, their own cookie, a second factor enrolled from
a terminal. The founder's decision of 2026-09-20 is one console and one sign-in
while there is one person with platform powers: the super admin is a `users` row
with a role of its own, seeded from the environment, and the powers are gated by
that role plus a re-authentication no older than thirty minutes.

- `user_role` gains `super_admin`. It is not a tenant role: it appears in no row
  of `auth/permissions.py`'s table, so it grants nothing *inside* an
  organization, and every tenant gate refuses it exactly as it refuses a viewer.
- `staff_accounts` is dropped. Nothing referenced it but the two columns below,
  and no staff account exists outside a development machine.
- `website_payments.recorded_by` and `plan_grants.granted_by` pointed at a staff
  account; they now point at the user who did it. Existing rows are cleared
  rather than translated — a development staff id has no user to become, and
  inventing one would put a name on an action nobody took.

**Affected store and schema.** Postgres only: one enum value, one table dropped,
two foreign keys re-pointed.

**Compatibility and backfill.** Nothing carries the new role until the seed
writes one. `recorded_by` and `granted_by` are nullable and become NULL where
they named a staff account; both are labels on an audit trail that keeps its own
record of who acted.

**Rollback and recovery.** `downgrade` restores `staff_accounts` empty and
re-points the two columns at it, clearing them again. **The enum value cannot be
removed** — Postgres has no `ALTER TYPE … DROP VALUE` — so a downgrade leaves
`super_admin` defined and unused. That is harmless: a role nothing holds and no
table row references is a string nobody reads.

**Tenant, retention, and security impact.** The super admin is a user like any
other and is subject to the same session, password and audit machinery. It
belongs to a reserved organization created by the seed, never by a customer.
Dropping `staff_accounts` removes the only credential that could not exist
without a second factor; that is recorded as a risk in the change's status.

Revision ID: 0035
Revises: 0034
Created: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# The two columns that named a staff account: table, column, the constraint it
# carries today, and the one it carries afterwards. The names are the project's
# own convention (`fk_<table>_<column>_<target>`), not Postgres's default, which
# is why they are written out rather than guessed.
_STAFF_REFERENCES: Sequence[tuple[str, str, str, str]] = (
    (
        "website_payments",
        "recorded_by",
        "fk_website_payments_recorded_by_staff_accounts",
        "fk_website_payments_recorded_by_users",
    ),
    (
        "plan_grants",
        "granted_by",
        "fk_plan_grants_granted_by_staff_accounts",
        "fk_plan_grants_granted_by_users",
    ),
)


def upgrade() -> None:
    # `IF NOT EXISTS` so a database that has been through this once — a
    # developer's, after a downgrade — is not refused on the way back up.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'super_admin'")

    for table, column, to_staff, to_users in _STAFF_REFERENCES:
        op.drop_constraint(to_staff, table, type_="foreignkey")
        # A staff id has no user to become. Cleared rather than translated.
        op.execute(f"UPDATE {table} SET {column} = NULL")
        op.create_foreign_key(to_users, table, "users", [column], ["id"], ondelete="SET NULL")

    op.drop_table("staff_accounts")


def downgrade() -> None:
    op.create_table(
        "staff_accounts",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("totp_secret", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("disabled_at", _TIMESTAMP, nullable=True),
    )
    for table, column, to_staff, to_users in _STAFF_REFERENCES:
        op.drop_constraint(to_users, table, type_="foreignkey")
        op.execute(f"UPDATE {table} SET {column} = NULL")
        op.create_foreign_key(
            to_staff, table, "staff_accounts", [column], ["id"], ondelete="SET NULL"
        )
    # `super_admin` stays in `user_role`: Postgres cannot remove an enum value.
