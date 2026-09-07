"""a tenant can be stopped at the door without being deleted or over its plan

Until now the receiver had exactly two ways to turn a tenant away: the plan
allowance, and deletion. Neither is the one an operator needs at 3am.

The allowance is a **billing** boundary — it refuses an organization that has
spent the volume its month includes, it applies to the free tier and nothing
else (ADR 0093), and it comes back on the 1st. Deletion is final. Between them
sits the case that has no answer: a tenant well inside its allowance, possibly a
paying one the allowance never touches, sending hard enough to hurt every other
tenant on the platform. There was no way to stop it. P1-52, and the gap
`phase-1-v2.md` §1 G5 names.

`organizations.ingest_suspended_at` is that stop valve.

**NULL is open, and that is the deliberate reading.** The valve closes only when
somebody writes to it, so a column nobody has touched cannot stop anybody — the
day this lands, every organization keeps ingesting. Any non-NULL value suspends;
the timestamp records *when* an operator closed it, and the receiver reads it as
`IS NOT NULL` rather than comparing it with now. That is on purpose: reading a
future date as "not yet" would be a scheduling feature nobody asked for, and
would turn a mistyped year into a valve that silently never closes.

**A timestamp rather than a boolean**, matching `deleted_at` beside it, because
the first question about a suspension is when it started and a boolean cannot
answer it.

**On `organizations`, so the receiver reads it for free.** The ingest path
already reads this row once per organization per cache window to answer the
allowance — one LATERAL statement in `allowance/postgres.go`. The valve is a
column on that same row, so closing it costs no extra query on the hot path and
inherits the same cache TTL. That TTL is also the delay: with
RECEIVER_ALLOWANCE_CACHE_TTL at its default of 30 seconds, a suspension takes up
to 30 seconds to bite on every receiver instance, and the ceiling in
`config.MaxAllowanceCacheTTL` is 5 minutes.

**What this deliberately does not do.** It does not revoke the organization's
keys — they go on authenticating, exactly as they do for a deleted organization,
because revocation belongs with account deletion in M8. It stops ingest at the
door and nothing else, and the refusal says so in its own words rather than
borrowing the allowance's: a suspended customer told their month ran out would
wait for the 1st, and the 1st does not reopen this.

No index. The only reader looks the row up by primary key.

Revision ID: 0018
Revises: 0017
Created: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "ingest_suspended_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "When an operator closed the ingest stop valve on this organization. "
                "NULL is open. Operational, not billing: a suspended organization may "
                "be well inside its plan allowance, and only a person reopens it. Read "
                "by the receiver's allowance gate as IS NOT NULL. See P1-52."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "ingest_suspended_at")
