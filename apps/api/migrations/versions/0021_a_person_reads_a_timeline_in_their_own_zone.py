"""a person reads a timeline in their own zone

P1-72 asks for a timezone on the account, and the row says why it is not
cosmetic: *"every screen renders timestamps in the browser's locale today, so an
incident timeline read in IST against a UTC ClickHouse row is a reader doing
arithmetic during an outage."* Doing arithmetic is the failure — during an
outage it is done wrong.

**It holds an IANA name, not an offset.** `Asia/Kolkata`, not `+05:30`. An
offset is only true until the next daylight-saving change, so a column of
offsets is a column that is wrong for part of every year and silently so. The
IANA name is the thing that keeps meaning: `zoneinfo` resolves it against the
tz database at render time, which is where the offset for *that instant* is
known.

**NULL means nobody has chosen one**, and the screen keeps rendering in the
browser's zone. That is exactly what every screen does today, so NULL is the
honest state of every row this migration touches. A `server_default` of `'UTC'`
was rejected for two reasons: it would be indistinguishable from somebody
deliberately choosing UTC, and it would move every existing account from "your
own clock" to UTC on the day it landed — the arithmetic this row exists to
remove, applied to everybody at once.

**Validated on write, in the API rather than by a check constraint.** The set of
valid names is the tz database, which ships with the operating system and the
`tzdata` package and changes several times a year as governments move their
clocks. A constraint listing them would be a migration every time IANA cuts a
release, and would be wrong between releases. `profile/timezones.py` asks
`zoneinfo` instead — the same library that will render the timestamp — so what
is accepted is exactly what can be rendered. An unvalidated string here is a
screen that crashes for one customer and nobody else.

The column is deliberately on `users` and not on `organizations`: a timezone is
where a *person* is, and two people on one account are routinely in two places.

Revision ID: 0021
Revises: 0020
Created: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches `models.TIMEZONE_NAME_LENGTH`. The longest name the database ships is
# `America/Argentina/ComodRivadavia` at 32 characters.
TIMEZONE_NAME_LENGTH = 64


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "timezone",
            sa.String(length=TIMEZONE_NAME_LENGTH),
            nullable=True,
            comment=(
                "The IANA time zone name this person reads timestamps in, such as "
                "'Asia/Kolkata'. Never an offset: offsets change twice a year. NULL "
                "means no zone has been chosen and the screen falls back to the "
                "browser's. Validated against the tz database on write. See P1-72."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "timezone")
