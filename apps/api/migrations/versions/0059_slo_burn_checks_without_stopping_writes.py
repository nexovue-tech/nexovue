"""0058's checks, validated without stopping writes

F19 (`docs/features/slo-tracking/`), plan T4.1; follows 0058.

**Why this change is required.** 0058 widened the condition-kind CHECKs on
`alert_rules` and `incidents`, and added `incidents.about_signal_is_known`, all
`NOT VALID`, because a CHECK added plainly reads every row while holding reads and
writes off. This validates them under SHARE UPDATE EXCLUSIVE, which lets both go on
(`docs/engineering/database.md`), as 0050 did for 0041.

**Affected store and schema.** Postgres: `VALIDATE CONSTRAINT` on the three checks, in
an autocommit block so each validation holds its lock alone.

**Compatibility and backfill.** None: every existing row passes (0058 says why).

**Rollback and recovery.** `downgrade` does nothing: validity is not structure, and
0058's own downgrade refuses.

**Tenant, retention, and security impact.** None: no data is added or read out.

Revision ID: 0059
Revises: 0058
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0058's NOT VALID checks, by the names the naming convention gave them.
NOT_VALID_CHECKS = (
    ("alert_rules", "ck_alert_rules_condition_is_known"),
    ("incidents", "ck_incidents_condition_is_known"),
    ("incidents", "ck_incidents_about_signal_is_known"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for table, check in NOT_VALID_CHECKS:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {check}")


def downgrade() -> None:
    """Nothing to undo: a validated check is the same check."""
