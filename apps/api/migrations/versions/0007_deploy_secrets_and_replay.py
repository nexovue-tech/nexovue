"""deploy webhook secrets get a table, and a delivery cannot be replayed

Two problems with deriving the secret from an ingest key's hash, which is what
P1-36 did because no column existed.

**Revoking an ingest key silently withdrew the deploy secret**, and a project
with no live key could not receive a deploy at all. Those are different
credentials with different lifetimes and they should not share a fate.

**A rotation could not overlap.** There was one secret per key, so changing it
meant a window where CI was signing with something we no longer accepted.
`project_webhook_secrets` holds many rows per project, so a new secret is
created, CI is updated, and the old one is revoked afterwards.

**And a signed request was replayable forever.** GitHub's scheme carries no
timestamp, so a captured delivery stays valid indefinitely. Every delivery
carries a unique id; recording it under a unique index is what makes the second
attempt fail rather than store a duplicate deploy.

Revision ID: 0007
Revises: 0006
Created: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_webhook_secrets",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        # The shared secret itself. HMAC verification needs it recoverable, so
        # it is stored as it will be used — there is no hashing that would still
        # let us compute the same digest CI computed.
        sa.Column("secret", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Set rather than deleted, so an audit can still see what was withdrawn
        # and when — the same reason api_keys are revoked rather than removed.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_webhook_secrets_org_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_webhook_secrets_project_id",
            ondelete="CASCADE",
        ),
    )
    # The verifier's only query is "every live secret for this project".
    op.create_index(
        "ix_webhook_secrets_project_id_revoked_at",
        "project_webhook_secrets",
        ["project_id", "revoked_at"],
    )

    op.add_column("deploy_events", sa.Column("delivery_id", sa.String(255), nullable=True))
    # Unique per project rather than globally: two CI systems may both count
    # their deliveries from one, and one customer's numbering is not another's.
    # NULL is allowed and repeats freely, so a sender that provides no id still
    # works — it simply gets no replay protection, which the service says out loud.
    op.create_index(
        "uq_deploy_events_project_id_delivery_id",
        "deploy_events",
        ["project_id", "delivery_id"],
        unique=True,
        postgresql_where=sa.text("delivery_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_deploy_events_project_id_delivery_id", table_name="deploy_events")
    op.drop_column("deploy_events", "delivery_id")
    op.drop_index("ix_webhook_secrets_project_id_revoked_at", table_name="project_webhook_secrets")
    op.drop_table("project_webhook_secrets")
