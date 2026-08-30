"""how many traces a project has stored under its first-traces carve-out

Tail sampling keeps one healthy trace in ten (ADR 0010). A customer's very first
span is a hello-world request that worked — healthy, fast — so it had a nine in
ten chance of being dropped, and "Your first span arrived." never appeared. The
onboarding screen reads the ordinary trace list, which is downstream of the
sampler, so there was nothing for it to find.

The fix is a carve-out: every trace is kept until a project has stored its first
N of them (`WORKERS_SAMPLING_FIRST_TRACES`, 100), after which the percentage
applies as before. This table is where the count lives, because it has to be one
count across every worker process — per-process counters would keep N traces per
worker, and a project's first traces are its first ones, not its first ones here.

No row means no trace has been stored under the carve-out yet, which is exactly
the state a project is in when it is created: the control plane creates nothing,
and the worker inserts on its first flush. Existing projects therefore each get
one carve-out of N traces, once. That is deliberate — seeding them as finished
would need this migration to know N, which lives in the worker's environment,
and 100 traces per project is not worth a second definition of it.

Revision ID: 0011
Revises: 0010
Created: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_first_traces",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Traces, not spans: the carve-out is counted in the unit the sampler
        # decides in. Bigint because it is only ever added to.
        sa.Column("traces_stored", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("project_id", name="pk_project_first_traces"),
        # Workers only ever add to this. A negative total means something wrote
        # a decrement, and the carve-out would then never close.
        sa.CheckConstraint("traces_stored >= 0", name="ck_project_first_traces_not_negative"),
    )


def downgrade() -> None:
    op.drop_table("project_first_traces")
