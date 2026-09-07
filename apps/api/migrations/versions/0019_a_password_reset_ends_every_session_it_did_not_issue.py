"""a password reset ends every session it did not issue

P1-71 requires that a successful password reset ends every live session. That is
the part of the row the plan called *"real work, not a flag"*, and reading the
code says why: `sessions.py` keys Redis on `session:<fingerprint(token)>` and
`SessionStore.revoke` takes a *token*. Nothing maps a user to their sessions, so
"end them all" has no query to run.

**Two ways to give it one, and this is the cheaper by a long way.**

An index — a Redis set of a user's live session keys — costs a second write on
every login, drifts as entries expire underneath it, and adds a structure whose
own correctness has to be maintained. An **epoch** costs one column and one
comparison: a session created before `sessions_valid_from` is not valid, so
bumping the column to `now()` invalidates every session in existence for that
user in a single write, with nothing to enumerate and nothing to drift.

**And the comparison is free.** `auth/dependencies.py:get_current_identity`
already loads the user row from Postgres on every authenticated request, to
resolve the tenant. The check reads a column that is already in hand.
`sessions.Session` already carries `created_at`, so the other side of the
comparison is already in the session payload.

**NULL means every session is valid**, which is the state of every user until
somebody resets a password. Not `created_at`'s value and not epoch zero: NULL
says *no reset has happened*, where a timestamp would say *a reset happened
then*, and only the first is true on the day this lands.

**It is deliberately not called `password_changed_at`.** The column's meaning is
"sessions older than this are refused", and a future reason to end every session
— an account recovery, a suspected compromise, a support action — should bump
this same column rather than adding a second one the check would have to
consider. Naming it after one of its causes would guarantee that second column.

The seconds-resolution question is settled by the column type: `timestamptz` in
Postgres keeps microseconds, and a session created in the same microsecond as
the reset that invalidates it is not a case anybody can construct.

Revision ID: 0019
Revises: 0018
Created: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "sessions_valid_from",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Sessions created before this moment are refused. NULL means no reset "
                "has happened and every session stands. Bumped by a password reset, "
                "and by any future reason to end every session at once. See P1-71."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "sessions_valid_from")
