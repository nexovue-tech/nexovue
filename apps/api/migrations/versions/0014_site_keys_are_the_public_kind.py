"""site keys become the usable public kind, and each key table says which kind it holds

An ingest credential now has a *kind*, and the kind is the table (ADR 0086).
`api_keys` holds the secret, server-side kind: hashed, compared in constant time,
carrying full ingest rights for a project. `site_keys` holds the public,
browser-side kind: printed in the page that carries it, stored in the clear,
restricted by origin instead of by secrecy.

`site_keys` has existed since migration 0001 and has never had a reader or a
writer — the same shape of gap ADR 0081 found in `api_keys.last_used_at`. This
migration makes it usable rather than inventing a discriminator column on
`api_keys`, which would have forced `key_hash` nullable and turned the receiver's
unconditional hash comparison into a branch.

**Every existing `api_keys` row is secret, and this says so rather than assuming
it.** There is no backfill to run and no default to pick, because the statement is
true of the table and not of a column: a table comment records it, so somebody
reading the schema is told which kind lives where without having to find the ADR.

`name` is added NOT NULL through a server default that is then dropped. The
default is expected to name **zero** rows: nothing in this codebase has ever
inserted into `site_keys`. It is there so the migration cannot fail on an
environment that surprises us, not because a placeholder name is acceptable.

`public_key` narrows from varchar(64) to varchar(31). Sixty-four was a placeholder
from before the format existed; thirty-one is `nvp_live_` plus twenty-two base62
characters, exactly. Narrowing fails loudly if any row is longer, which is the
right outcome — a longer value would mean a second format nobody recorded.

Revision ID: 0014
Revises: 0013
Created: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `nvp_live_` + 22 base62 characters. Must stay equal to SITE_KEY_LENGTH in the
# models and to siteKeyLength in the receiver's auth package.
SITE_KEY_LENGTH = 31
PLACEHOLDER_LENGTH = 64

API_KEYS_COMMENT = (
    "Secret, server-side ingest keys only. Stored as a SHA-256 hash and verified "
    "in constant time. Public browser keys live in site_keys; the kind is the "
    "table, so this lookup cannot return one. See ADR 0086."
)
SITE_KEYS_COMMENT = (
    "Public, browser-side ingest keys only. Stored in the clear because the value "
    "ships in the page that carries it; restricted by allowed_origins rather than "
    "by secrecy, and it may accept browser telemetry for its project and nothing "
    "else. Secret server keys live in api_keys. See ADR 0086."
)

# Names zero rows: nothing has ever inserted into site_keys. Dropped immediately
# so a real insert has to supply a name.
UNNAMED_SITE_KEY = "Unnamed site key"


def upgrade() -> None:
    op.add_column(
        "site_keys",
        sa.Column(
            "name",
            sa.String(200),
            nullable=False,
            server_default=sa.text(f"'{UNNAMED_SITE_KEY}'"),
        ),
    )
    op.alter_column("site_keys", "name", server_default=None)

    # The same reading api_keys got in ADR 0081: whether anything is still using
    # this key is the question asked before withdrawing it.
    op.add_column("site_keys", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))

    op.alter_column(
        "site_keys",
        "public_key",
        type_=sa.String(SITE_KEY_LENGTH),
        existing_type=sa.String(PLACEHOLDER_LENGTH),
        existing_nullable=False,
    )

    op.execute(sa.text(f"COMMENT ON TABLE api_keys IS '{API_KEYS_COMMENT}'"))
    op.execute(sa.text(f"COMMENT ON TABLE site_keys IS '{SITE_KEYS_COMMENT}'"))


def downgrade() -> None:
    op.execute(sa.text("COMMENT ON TABLE site_keys IS NULL"))
    op.execute(sa.text("COMMENT ON TABLE api_keys IS NULL"))

    op.alter_column(
        "site_keys",
        "public_key",
        type_=sa.String(PLACEHOLDER_LENGTH),
        existing_type=sa.String(SITE_KEY_LENGTH),
        existing_nullable=False,
    )
    op.drop_column("site_keys", "last_used_at")
    op.drop_column("site_keys", "name")
