"""a second factor, and the way back in without it

T64 adds two-factor authentication. Three things have to be stored, and each one
is stored the way it is for a reason that would not survive being guessed at
later, so they are written down here.

**`users.totp_secret` is BYTEA and holds ciphertext, never the base32 string.**
This is the one credential in the control plane that cannot be hashed: verifying
a six-digit code means recomputing an HMAC with the *same* secret the
authenticator holds, so a digest is useless and the original has to survive.
`auth/encryption.py` seals it with AES-256-GCM under a key held in the API's
environment, authenticating this row's own id as associated data — so a row
copied into another user's record fails to open rather than handing that user's
second factor to whoever copied it.

The column is deliberately **not** named `totp_secret_encrypted`. Migration 0006
renamed `integrations.config_encrypted` to `config` precisely because a name that
promises encryption is worse than a neutral one when the promise is not kept in
the column itself, and a name cannot keep it: the type is bytes, the only writer
is the sealing function, and the reason lives in the model's comment where a
reader will actually meet it.

**`users.totp_confirmed_at`, and not a boolean, is what switches the factor on.**
Two reasons. "When" is the question support asks, which is the same argument
`email_verified_at` already carries. And the separation is what stops a customer
locking themselves out: a secret may be stored during enrolment and is worth
nothing until the customer has proved they can produce a code from it, so the
confirmed moment is the switch and the secret is only the material. The CHECK
below refuses the two states that would mean something has gone wrong — a
confirmed factor with no secret, and a secret nothing ever confirmed.

**`user_recovery_codes` is a table rather than an array on the user row**, because
each code is independently consumed. A JSONB array would make "spend one" a
read-modify-write of the whole set, and `used_at` is a per-code fact.

It carries **no `org_id`**, which is a departure from the denormalization rule in
`models.py` and is argued there: every lookup is "this signed-in person's own
codes", resolved from the session's user id, so there is no statement for a
tenant predicate to protect and a column no query reads would be exactly the
false assurance 0006 deleted. Tenant erasure still reaches these rows —
`organizations/tables.py` lists the table as a cascade from `users`, which a
deletion removes directly, and `test_deletion_live.py` holds that claim against
the live catalog.

`code_hash` is argon2, sized like `users.password_hash`. Not SHA-256, which is
what `api_keys` uses: an API key is 128 random bits and gains nothing from
stretching, while a recovery code is transcribed off paper by a human and is
therefore short — fifty bits, which a fast hash does not protect.

**No backfill and no compatibility shim.** Nexovue is pre-deployment; every
existing row gets NULL in both new columns, which is the honest state of an
account with no second factor and the state the CHECK admits.

**Rollback erases enrolments.** Dropping the columns discards every stored secret
and every recovery code, so a downgrade signs nobody out but does turn the second
factor off for everybody who had it. That is the only possible answer — the
secrets exist nowhere else — and it is stated so a rollback is not a surprise.

Revision ID: 0025
Revises: 0024
Created: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches `users.password_hash`. An argon2 encoding is about a hundred
# characters; the column is sized like its sibling so the two cannot drift.
HASH_LENGTH = 255

CHECK_NAME = "ck_users_totp_secret_and_confirmation_agree"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "totp_secret",
            sa.LargeBinary(),
            nullable=True,
            comment=(
                "The TOTP shared secret, sealed with AES-256-GCM by auth/encryption.py and "
                "authenticated against this row's id. Never plaintext, never logged, and "
                "never returned after enrolment. NULL when the account has no second factor."
            ),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "When the customer proved a code from the stored secret. This column, not "
                "the secret, is what makes the second factor required at sign-in. NULL "
                "means a password alone signs this account in. See T64."
            ),
        ),
    )
    op.create_check_constraint(
        CHECK_NAME,
        "users",
        "(totp_secret IS NULL) = (totp_confirmed_at IS NULL)",
    )

    op.create_table(
        "user_recovery_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuidv7()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "code_hash",
            sa.String(HASH_LENGTH),
            nullable=False,
            comment="Argon2, like users.password_hash. A code is short enough to need it.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "When this code was spent. Kept rather than deleted, so 'a recovery code "
                "was used on Tuesday' stays answerable — it is how somebody learns they "
                "have been broken into. A code with a value here is never accepted again."
            ),
        ),
    )
    # Every read is "this user's unused codes", so the user is the index.
    op.create_index("ix_user_recovery_codes_user_id", "user_recovery_codes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_recovery_codes_user_id", table_name="user_recovery_codes")
    op.drop_table("user_recovery_codes")
    op.drop_constraint(CHECK_NAME, "users", type_="check")
    op.drop_column("users", "totp_confirmed_at")
    op.drop_column("users", "totp_secret")
