"""where the usage meter has counted to, so a replay does not double a bill

`usage_daily` had a table, a model and a migration, and nothing wrote to it. The
job that fills it is at-least-once like everything else reading Kafka, so a
redelivered batch would add a customer's traffic to their usage a second time.

ADR 0060 and 0061 solved the same problem for ClickHouse counters with a
BEFORE/AFTER bracket, because ClickHouse has no transaction to hold the counters
and the offsets together. **Postgres does**, so this is simpler: one transaction
advances the watermark and adds the counters, and a flush retried after its
transaction already committed updates no rows — which is the signal not to add
them again.

Keyed by (topic, partition) rather than by consumer group: the meter reads each
ingestion topic on its own group, and a partition is the unit Kafka rebalances,
so it is the unit progress is recorded against.

Revision ID: 0009
Revises: 0008
Created: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_ingest_offsets",
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("partition_id", sa.Integer(), nullable=False),
        # The offset counted through, not the next one to read. Kafka offsets
        # are int64 and a long-lived partition will exceed an int.
        sa.Column("counted_through", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("topic", "partition_id", name="pk_usage_ingest_offsets"),
    )


def downgrade() -> None:
    op.drop_table("usage_ingest_offsets")
