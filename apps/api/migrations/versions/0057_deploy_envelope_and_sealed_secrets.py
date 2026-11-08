"""The deploy envelope, sealed deploy secrets, and what the Deploys section shows

F28 (`docs/features/deployment-release-tracking/`), plan T2.1; revises 0056 (F20's
dashboards), after F22's 0055.

**Why this change is required.** The deploy webhook's new body signs each item's own
id and time (D6), so a captured request sent again stores nothing whatever its headers
say; it carries the rollout's state, a commit and a link (FR6, FR9); and each deploy is
filed under its release (design 3). Deploy secrets were stored in the clear and shown
on every read; they are now sealed and shown once (D7, FR2, FR3). The Deploys section
shows when a deploy and a dry-run check last arrived and the last refusal a signed
sender earned (FR1, FR10, D22), one row per project.

**Affected store and schema.** Postgres:

- `deploy_events`: `state` (NOT NULL, default `finished`, one of the four states),
  `commit_sha`, `url`, `release_id` and `webhook_secret_id`, both `ON DELETE SET
  NULL` so a deploy outlives an evicted release and a deleted secret. `delivery_id`
  becomes NOT NULL at 200 characters, ending the disagreement between 0007 (255) and
  the model (200), and its unique index loses its `IS NOT NULL` predicate. New
  indexes: `created_at` for the retention sweep, `release_id` for recalculating a
  release and for its eviction's SET NULL, and `(webhook_secret_id, created_at)` for
  removing what a withdrawn secret sent since a time (D37).
- `project_webhook_secrets`: `secret` is replaced by `sealed_secret` (NOT NULL),
  `secret_hint` (its last characters) and `last_used_at`.
- `deploy_intake_state`: new, keyed by project.

**Compatibility and backfill.** None, on purpose (pre-deployment; design, *Data and
migration impact*). Existing deploy secrets are deleted: they cannot be sealed here,
since the key is the API's alone, and a secret nobody can be shown again is of no use.
Deploys with no delivery id, or one past 200 characters, are deleted: the new door
never accepts them. Projects mint a new secret in the Deploys section.

**Locks.** The indexes are built concurrently, outside a transaction, and the two
foreign keys and the state check are added `NOT VALID` and validated there, so reads
and writes go on (`docs/engineering/database.md`). Shortening `delivery_id` and
setting it NOT NULL scan the table under a brief exclusive lock; `deploy_events` is
small by design ("volume is tiny"), and a NOT VALID-then-validate path does not exist
for a type change. A failed concurrent build leaves an invalid index, dropped first
when the upgrade runs again.

**Rollback and recovery.** Roll forward only: the deleted secrets cannot be restored,
and the deploys' states, commits and links exist nowhere else. Going back is a restore.

**Tenant, retention, and security impact.** Every new column and table carries or
inherits `org_id`; `deploy_intake_state` cascades from `projects`
(`organizations/tables.py`). A sealed secret opens only with the credential key and only
as its own project's and row's. `metadata` is masked before it is stored.

Revision ID: 0057
Revises: 0056
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies, as 0054's are: the deploy states (spec, *States*), the name and label
# lengths, a SHA-256's hex length and the secret hint's.
_DEPLOY_STATES = "'started', 'finished', 'failed', 'rolled_back'"
_NAME_LONGEST = 200
_LABEL_LONGEST = 64
_SHA_LONGEST = 64
_HINT_LONGEST = 8

_DELIVERY_KEY = "uq_deploy_events_project_id_delivery_id"
_NEW_INDEXES = (
    ("ix_deploy_events_created_at", ["created_at"]),
    ("ix_deploy_events_release_id", ["release_id"]),
    ("ix_deploy_events_webhook_secret_id_created_at", ["webhook_secret_id", "created_at"]),
)
_NOT_VALID = (
    ("deploy_events", "fk_deploy_events_release_id_releases"),
    ("deploy_events", "fk_deploy_events_webhook_secret_id_project_webhook_secrets"),
    ("deploy_events", "ck_deploy_events_state_is_known"),
)


def upgrade() -> None:
    _deploy_events()
    _webhook_secrets()
    _deploy_intake_state()
    with op.get_context().autocommit_block():
        _indexes_and_validation()


def _deploy_events() -> None:
    op.execute(
        "DELETE FROM deploy_events "
        f"WHERE delivery_id IS NULL OR char_length(delivery_id) > {_NAME_LONGEST}"
    )
    op.alter_column(
        "deploy_events",
        "delivery_id",
        type_=sa.String(_NAME_LONGEST),
        existing_type=sa.String(255),
        nullable=False,
    )
    op.add_column(
        "deploy_events",
        sa.Column(
            "state",
            sa.String(_LABEL_LONGEST),
            nullable=False,
            server_default=sa.text("'finished'"),
        ),
    )
    op.add_column("deploy_events", sa.Column("commit_sha", sa.String(_SHA_LONGEST), nullable=True))
    op.add_column("deploy_events", sa.Column("url", sa.Text(), nullable=True))
    op.add_column("deploy_events", sa.Column("release_id", _UUID, nullable=True))
    op.add_column("deploy_events", sa.Column("webhook_secret_id", _UUID, nullable=True))
    op.execute(
        "ALTER TABLE deploy_events ADD CONSTRAINT fk_deploy_events_release_id_releases "
        "FOREIGN KEY (release_id) REFERENCES releases (id) ON DELETE SET NULL NOT VALID"
    )
    op.execute(
        "ALTER TABLE deploy_events "
        "ADD CONSTRAINT fk_deploy_events_webhook_secret_id_project_webhook_secrets "
        "FOREIGN KEY (webhook_secret_id) REFERENCES project_webhook_secrets (id) "
        "ON DELETE SET NULL NOT VALID"
    )
    op.execute(
        "ALTER TABLE deploy_events ADD CONSTRAINT ck_deploy_events_state_is_known "
        f"CHECK (state IN ({_DEPLOY_STATES})) NOT VALID"
    )


def _webhook_secrets() -> None:
    # Clear text cannot be sealed here: the key is the API's alone (see above).
    op.execute("DELETE FROM project_webhook_secrets")
    op.drop_column("project_webhook_secrets", "secret")
    op.add_column(
        "project_webhook_secrets",
        sa.Column("sealed_secret", sa.LargeBinary(), nullable=False),
    )
    op.add_column(
        "project_webhook_secrets",
        sa.Column("secret_hint", sa.String(_HINT_LONGEST), nullable=False),
    )
    op.add_column("project_webhook_secrets", sa.Column("last_used_at", _TIMESTAMP, nullable=True))


def _deploy_intake_state() -> None:
    op.create_table(
        "deploy_intake_state",
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            _UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_check_at", _TIMESTAMP, nullable=True),
        sa.Column("last_deploy_at", _TIMESTAMP, nullable=True),
        sa.Column("last_refusal_at", _TIMESTAMP, nullable=True),
        sa.Column("last_refusal_position", sa.Integer(), nullable=True),
        sa.Column("last_refusal_field", sa.String(_LABEL_LONGEST), nullable=True),
        sa.Column(
            "last_refusal_secret_id",
            _UUID,
            sa.ForeignKey("project_webhook_secrets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def _indexes_and_validation() -> None:
    # The delivery key without its predicate: the column is never null now.
    op.drop_index(
        _DELIVERY_KEY, table_name="deploy_events", postgresql_concurrently=True, if_exists=True
    )
    op.create_index(
        _DELIVERY_KEY,
        "deploy_events",
        ["project_id", "delivery_id"],
        unique=True,
        postgresql_concurrently=True,
    )
    for name, columns in _NEW_INDEXES:
        op.drop_index(
            name, table_name="deploy_events", postgresql_concurrently=True, if_exists=True
        )
        op.create_index(name, "deploy_events", columns, postgresql_concurrently=True)
    for table, constraint in _NOT_VALID:
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}")


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: the deploy secrets this deleted cannot be restored, and the "
        "deploys' states, commits and links exist nowhere else. Going back past 0057 is "
        "a restore."
    )
