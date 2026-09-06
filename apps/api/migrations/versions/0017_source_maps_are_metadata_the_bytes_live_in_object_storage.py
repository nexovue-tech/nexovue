"""source maps get a metadata table; the bytes they describe live in object storage

A browser error's stacktrace names positions in a minified bundle — `main.a3f9.js`
line 1, column 84,213 — which tells a developer nothing. Symbolication is the
lookup that turns that back into `Checkout.tsx` line 47, and it needs the source
map the customer's build produced. So the customer has to upload one, and we have
to be able to find the right one when an error arrives.

**Metadata here, bytes elsewhere.** A source map is routinely larger than the
bundle it describes — tens of megabytes for a large application, and one per
build. Postgres is the control plane's transactional store, not a blob store;
putting map bytes in it would grow the database that every screen's latency
depends on, and back them up on every dump. `storage_key` names the object
instead. ADR 0015 fixes that store as the generic S3 API — SeaweedFS locally,
AWS S3 in deployment — so this column holds a key, never a URL: a URL would
bake the endpoint of one deployment into rows that outlive it.

**Two ways to find the map for a stack frame, because the reliable one is not
always present.**

`debug_id` is the ECMA-426 debug id: a build stamps the same id into both the
bundle and its map, the browser reports it with the error, and the lookup is
exact. It is nullable because a bundler that does not emit one is the common
case today, and refusing those uploads would mean refusing most of them.

`release` is the fallback — the build the map belongs to. It joins logically to
`spans.deployment_version` and `deploy_events.version`, the same cross-store join
by string those two already have, so a map can be matched to the deploy that
shipped it. Together with `minified_path` it identifies a file within a build,
which is what a stack frame's file name gives us when there is no debug id.

**No unique constraint on either lookup.** A re-upload of the same file — a
retried CI step, a rebuilt artifact — would then fail rather than supersede, and
a build that legitimately emits two chunks sharing a debug id would be rejected
outright. Which row wins when several match is a service decision, and the
service can see `uploaded_at`; the schema does not pre-empt it.

Revision ID: 0017
Revises: 0016
Created: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# An S3 key: the bucket's own limit is 1024 UTF-8 bytes.
STORAGE_KEY_LENGTH = 1024

# A bundler path, which can be a long nested route inside a build output.
MINIFIED_PATH_LENGTH = 1024

# ECMA-426 states no length; every generator in practice emits a UUID or a
# 32-character hex digest. Stored as text, not as a uuid column, because the
# specification names it a string and a build is free to use one that is not a
# UUID at all.
DEBUG_ID_LENGTH = 64

RELEASE_LENGTH = 200


def upgrade() -> None:
    op.create_table(
        "source_maps",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("release", sa.String(RELEASE_LENGTH), nullable=False),
        sa.Column("debug_id", sa.String(DEBUG_ID_LENGTH), nullable=True),
        sa.Column("minified_path", sa.String(MINIFIED_PATH_LENGTH), nullable=False),
        sa.Column("storage_key", sa.String(STORAGE_KEY_LENGTH), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        # A negative or zero size is a failed upload recorded as a success, and
        # a symbolication that then downloads nothing reports "no source map"
        # rather than "the upload was truncated".
        sa.CheckConstraint("size_bytes > 0", name="size_is_positive"),
    )

    # The exact lookup: a stack frame carrying a debug id needs one row, without
    # knowing the release. Partial, because a null debug id is the common case
    # and indexing those rows twice buys nothing.
    op.create_index(
        "ix_source_maps_project_id_debug_id",
        "source_maps",
        ["project_id", "debug_id"],
        postgresql_where=sa.text("debug_id IS NOT NULL"),
    )
    # The fallback lookup, and the listing a customer sees for one build.
    op.create_index(
        "ix_source_maps_project_id_release_minified_path",
        "source_maps",
        ["project_id", "release", "minified_path"],
    )


def downgrade() -> None:
    # Dropping the table takes its indexes, constraints and foreign keys with
    # it. The objects in the store are not this migration's to delete: they are
    # named by rows that no longer exist, and deleting customer data on a
    # schema rollback is not a rollback.
    op.drop_table("source_maps")
