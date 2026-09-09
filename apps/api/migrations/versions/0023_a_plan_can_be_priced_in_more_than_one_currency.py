"""a plan can be priced in more than one currency

`plan_prices` was keyed `(plan_slug, effective_from)`, so a tier had exactly one
currency at a time and every customer everywhere was quoted it. Everything was
priced in USD, including for customers in India paying an Indian seller — who
should be charged rupees, with the tax treatment that follows from it.

**Currency joins the key rather than becoming a second table.** A price is
already temporal, and adding a dimension to a temporal key is what the table is
shaped for: `(plan_slug, currency, effective_from)` still answers "the price in
force on a day" by taking the latest row not after it, now per currency. A
separate table would have to repeat `effective_from` and then be joined back.

**`billing_subscriptions.currency` records what a mandate was signed in.** A
subscription keeps debiting the currency it was authorised in; converting per
charge would give a different amount every month as the rate moved, which is a
debit nobody expected and a chargeback. The tier alone no longer says which
currency an arrangement used, so the row has to.

The INR rows land here rather than in a later data-only migration, for the same
reason 0015 seeded the first prices: a currency the schema allows and no row
supplies is a currency the product cannot actually sell in. The figures are
Raj's — 399 and 330 rupees per service per month, against 6.00 and 5.00 dollars,
which is roughly the ratio Indian SaaS prices its home market at. `free` is
restated in rupees too, because a free tier that exists in only one currency
would make an Indian visitor's first screen a dollar sign.

Every column is restated on each new row rather than inherited, which is the
convention `test_plan_prices_live.py` guards: a repricing that carried an
allowance forward by omission would be a silent change to what a customer may
send.

Revision ID: 0023
Revises: 0022
Created: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENCY_CODE_LENGTH = 3
GIB = 1024**3

# The day these rates take effect. The USD rows already in the table keep their
# own `effective_from`, so nothing existing is restated by this.
EFFECTIVE_FROM = sa.text("DATE '2026-09-10'")

# plan_slug, price per service in paise, max billed services, included bytes per
# service, browser share. The USD rows they mirror are migrations 0015 and 0016.
INR_PRICES = (
    ("free", 0, 3, 2 * GIB, 25),
    ("pro", 39_900, None, 10 * GIB, None),
    ("scale", 33_000, None, 25 * GIB, None),
)


def upgrade() -> None:
    # `pk_plan_prices`, not Postgres's default `plan_prices_pkey`: this metadata
    # sets a naming convention, and a migration that assumes the default drops
    # nothing and then fails on a duplicate.
    op.drop_constraint("pk_plan_prices", "plan_prices", type_="primary")
    op.create_primary_key(
        "pk_plan_prices", "plan_prices", ["plan_slug", "currency", "effective_from"]
    )

    op.add_column(
        "billing_subscriptions",
        sa.Column(
            "currency",
            sa.String(length=CURRENCY_CODE_LENGTH),
            nullable=False,
            server_default="USD",
            comment=(
                "ISO 4217 code the mandate was authorised in. A subscription keeps "
                "debiting the currency it was signed in; converting per charge would "
                "give a different amount every month as the rate moved."
            ),
        ),
    )
    # The default exists only to fill the rows already there — every row written
    # from now on states its own currency, and leaving a default would let a bug
    # write a dollar mandate silently.
    op.alter_column("billing_subscriptions", "currency", server_default=None)

    prices = sa.table(
        "plan_prices",
        sa.column("plan_slug", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("currency", sa.String),
        sa.column("price_per_service_minor_units", sa.BigInteger),
        sa.column("max_billed_services", sa.Integer),
        sa.column("included_bytes_per_service", sa.BigInteger),
        sa.column("browser_share_percent", sa.SmallInteger),
    )
    op.execute(
        prices.insert().values(
            [
                {
                    "plan_slug": slug,
                    "effective_from": EFFECTIVE_FROM,
                    "currency": "INR",
                    "price_per_service_minor_units": paise,
                    "max_billed_services": ceiling,
                    "included_bytes_per_service": included,
                    "browser_share_percent": share,
                }
                for slug, paise, ceiling, included, share in INR_PRICES
            ]
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM plan_prices WHERE currency = 'INR'")
    op.drop_column("billing_subscriptions", "currency")
    op.drop_constraint("pk_plan_prices", "plan_prices", type_="primary")
    op.create_primary_key("pk_plan_prices", "plan_prices", ["plan_slug", "effective_from"])
