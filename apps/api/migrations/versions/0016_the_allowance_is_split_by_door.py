"""the allowance is split between the two doors, so a scraped site key cannot end the month

A public site key is printed in the customer's page, so anyone who views source
can send through the browser door as that customer. The rate limit costed that
at the key's per-second ceiling; the pooled allowance then made the month one
number for the organization, refused at every door once spent — and with that,
a scraped key could spend the whole month in under a second and silence the
customer's servers until the 1st. Neither decision had mentioned the other.

**Two columns, one on each side of the gate**.

`plan_prices.browser_share_percent` is how much of a plan's allowance the
browser door may spend, as a whole-number percentage; the server door has the
rest. A share rather than a second byte figure, so the two budgets cannot sum
to anything but the plan's total and a repricing of the total carries the split
with it. NULL is "no split recorded" — both doors spend the one allowance,
which is what every row before this one meant and still means. A column, not a
constant, for the reason 0005 and 0015 already gave: a number that changes
after contact with customers must not need a deploy.

`usage_daily.browser_bytes` is the part of `bytes` the meter counted through the
browser door. Beside `bytes`, never instead of it, so the total the usage screen
shows is unchanged. Existing rows get zero: their bytes were metered before the
door was recorded, and the gate reads them as the server's. The check that
`browser_bytes` never exceeds `bytes` is what stops a meter defect from
inventing browser traffic.

**The free tier's split is a new row, not an edit.** A later change to a price
row is a row with a later `effective_from`, never an edit to the one in force
(0015). The row seeded here is 0015's free row with a share of 25%, effective
from the day the split was decided. **25% is a guess dressed as a ratio** —
Raj's words — and it stands until evidence revises it. Pro and scale get no
row: they enforce no allowance (`max_billed_services` is NULL), so a split on
them would be a number nobody decided.

**A mid-month `effective_from` moves two lines at once, and both cost
something.** The gate reads the row in force *today* against the *whole*
month's metered bytes, and rows metered before `browser_bytes` existed hold
zero, so they all read as the server's. On the day this lands:

- a free organization that has already spent between 75% and 100% of its 6 GiB
  is **refused at the server door immediately** — their backend goes dark,
  which is the harm this split exists to prevent, inflicted by the fix;
- a free organization already past the whole 6 GiB has its **browser door
  reopened for 1.5 GiB**, because its historical browser bytes read as zero —
  so for the transition month the plan total is effectively 7.5 GiB, and
  0093's "a customer cannot buy more month by changing entrance" does not hold
  for that month.

A `effective_from` of the 1st of a month has neither consequence, and leaves
the pooled allowance — and the scraped-key exposure this split closes — in
force until then. **Which of the two is wanted is a customer decision that has
not been made; this date is not settled.** In the dev control plane the choice
costs nothing either way — read on 2026-09-04, its one organization had spent
11.88 MB of 6 GiB, 0.18% of the month. That is a reading of a development stack
on one day, not a fact about production, and the consequences above are what
the date has to be chosen against.

Revision ID: 0016
Revises: 0015
Created: 2026-09-04
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# The day the split first takes effect. A later change is a new row with a later
# `effective_from`, never an edit to this one. Mid-month is not a free choice —
# see the two consequences in the docstring above, and the customer decision
# this date is waiting on.
_EFFECTIVE_FROM = date(2026, 9, 4)

_GIB = 1024**3

# 0015's free row, restated whole, plus the share. Provisional: the share is a
# guess until evidence revises it.
_FREE_PRICE = {
    "plan_slug": "free",
    "effective_from": _EFFECTIVE_FROM,
    "currency": "USD",
    "price_per_service_minor_units": 0,
    "included_bytes_per_service": 2 * _GIB,
    "max_billed_services": 3,
    "browser_share_percent": 25,
}


def upgrade() -> None:
    op.add_column(
        "plan_prices",
        sa.Column(
            "browser_share_percent",
            sa.SmallInteger(),
            nullable=True,
            comment=(
                "Share of the month's allowance the browser door may spend, as a "
                "whole percentage; the server door has the rest. NULL is no split "
                "recorded: both doors spend the one allowance."
            ),
        ),
    )
    op.create_check_constraint(
        "browser_share_is_a_percentage",
        "plan_prices",
        "browser_share_percent IS NULL OR browser_share_percent BETWEEN 0 AND 100",
    )

    op.add_column(
        "usage_daily",
        sa.Column(
            "browser_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment=(
                "The part of bytes that came through the browser door. Rows from "
                "before the door was recorded hold zero and are read as the "
                "server's."
            ),
        ),
    )
    op.create_check_constraint(
        "browser_bytes_within_bytes",
        "usage_daily",
        "browser_bytes >= 0 AND browser_bytes <= bytes",
    )

    op.bulk_insert(
        sa.table(
            "plan_prices",
            sa.column("plan_slug", sa.String),
            sa.column("effective_from", sa.Date),
            sa.column("currency", sa.String),
            sa.column("price_per_service_minor_units", sa.BigInteger),
            sa.column("included_bytes_per_service", sa.BigInteger),
            sa.column("max_billed_services", sa.Integer),
            sa.column("browser_share_percent", sa.SmallInteger),
        ),
        [_FREE_PRICE],
    )


def downgrade() -> None:
    # Named by plan as well as by day: a later change seeding another plan on
    # the same date is not this migration's row, and deleting it would drop that
    # plan back to an older price.
    op.execute(
        sa.text(
            "DELETE FROM plan_prices WHERE effective_from = :day AND plan_slug = :slug"
        ).bindparams(day=_EFFECTIVE_FROM, slug=_FREE_PRICE["plan_slug"])
    )
    op.drop_constraint("browser_bytes_within_bytes", "usage_daily", type_="check")
    op.drop_column("usage_daily", "browser_bytes")
    op.drop_constraint("browser_share_is_a_percentage", "plan_prices", type_="check")
    op.drop_column("plan_prices", "browser_share_percent")
