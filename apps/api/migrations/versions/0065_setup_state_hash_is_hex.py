"""A GitHub setup's state hash is hex text, as every other token hash

F22 (`docs/features/case-management/`), a correction to 0055; follows F19's 0064.

**Why this change is required.** 0055 stored `connections.setup_state_hash`, the SHA-256
of a setup's one-time `state`, as `bytea`. Every binary column in the schema otherwise
holds a value sealed under a key, and the key rotation (F28 AC3) finds those columns by
`keys.SEALED_COLUMNS`, which `test_keys_live.py` keeps equal to the schema's binary
columns so a sealed column cannot be left out and stay under a key about to be dropped.
A hash is not sealed and cannot be resealed, so it does not belong on that list; and
naming it as an exception would make "binary means sealed" false for the next reader.
Every other token hash (`sessions.token_hash`, `api_keys.key_hash`, the one-time tokens)
is 64 hex characters, so this one now is too.

**Affected store and schema.** Postgres: `connections.setup_state_hash` from `bytea` to
`varchar(64)`, each value converted with `encode(…, 'hex')`. The two checks that name
the column test only for null, so they hold unchanged.

**Locks.** Changing a column's type rewrites the table under an exclusive lock.
`connections` holds at most one live row per organization and kind, so the rewrite is
instant; `lock_timeout` (`migrations/env.py`) bounds the wait for the lock.

**Compatibility and backfill.** The conversion is the backfill: a setup in progress
keeps its hash, now in hex, and the code compares hex from this revision on.

**Rollback and recovery.** `downgrade` converts back with `decode(…, 'hex')`; nothing
is lost either way.

**Tenant, retention, and security impact.** None: the same hash, written differently.
The raw `state` is still never stored, and disconnecting still clears the hash.

Revision ID: 0065
Revises: 0064
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA256_HEX_LENGTH = 64


def upgrade() -> None:
    op.alter_column(
        "connections",
        "setup_state_hash",
        type_=sa.String(SHA256_HEX_LENGTH),
        existing_type=sa.LargeBinary(),
        existing_nullable=True,
        postgresql_using="encode(setup_state_hash, 'hex')",
    )


def downgrade() -> None:
    op.alter_column(
        "connections",
        "setup_state_hash",
        type_=sa.LargeBinary(),
        existing_type=sa.String(SHA256_HEX_LENGTH),
        existing_nullable=True,
        postgresql_using="decode(setup_state_hash, 'hex')",
    )
