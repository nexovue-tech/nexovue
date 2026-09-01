"""the first prices, so the usage screen can show a cost

`plan_prices` has existed since 0013 and held nothing, which is why the usage
screen has always said "No cost shown". That was correct — a price nobody set is
not a price of zero, and inventing one would put a number in front of a customer
that nobody decided. These are decided, and they are rows rather than constants
for the reason 0005 already gave: a price is among the values most likely to
change after contact with customers, and changing one should not need a deploy.

**Provisional, and priced against what the market charges rather than against
cost.** Comparable entry points at the time of writing: Grafana Cloud Pro is a
$19/month platform fee plus usage; SigNoz is $49/month standard with logs and
traces at $0.30/GB; per-host SaaS observability runs $15-58 per host per month.

Our unit is a *service*, which is finer than a host — several services usually
share one machine — so the per-unit price has to sit below a per-host price or a
customer with ten services on three hosts pays us more than they pay Datadog for
the same fleet. At $6, ten services cost $60/month against roughly $138 for three
hosts of Datadog infrastructure and APM. That is the comparison this pricing is
built to win, and it is the one worth re-checking when it changes.

The included volume is sized the same way. Ten gigabytes per service at SigNoz's
$0.30/GB is about $3 of ingest inside a $6 price, which leaves margin before
storage. A more generous allowance was the first draft and was rejected on that
arithmetic: 25GB would have been $7.50 of ingest inside a $6 price.

**Enterprise deliberately has no row.** A tier priced by agreement has no list
price, and inventing one would make the usage screen state a number no contract
uses. With no row the screen shows no cost, which is the truth for that tier.

**What this does not settle.** ADR 0056 says both that we block above the
allowance and that overage stays live and bills — two different products — and
nothing here resolves that. `max_billed_services` caps the free tier at three;
what happens when a paid tier exceeds its included volume is still undecided, and
the usage screen says in words that overage is not in the figure it shows.

Revision ID: 0015
Revises: 0014
Created: 2026-09-01
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# The day these first take effect. A later change is a new row with a later
# `effective_from`, never an edit to this one — that is what stops a repricing
# restating what a past invoice was computed from (ADR 0083).
_EFFECTIVE_FROM = date(2026, 9, 1)

_GIB = 1024**3

_PRICES = (
    # slug, price per service per month in minor units, included bytes, service cap
    ("free", 0, 2 * _GIB, 3),
    ("pro", 600, 10 * _GIB, None),
    ("scale", 500, 25 * _GIB, None),
)


def upgrade() -> None:
    op.bulk_insert(
        sa.table(
            "plan_prices",
            sa.column("plan_slug", sa.String),
            sa.column("effective_from", sa.Date),
            sa.column("currency", sa.String),
            sa.column("price_per_service_minor_units", sa.BigInteger),
            sa.column("included_bytes_per_service", sa.BigInteger),
            sa.column("max_billed_services", sa.Integer),
        ),
        [
            {
                "plan_slug": slug,
                "effective_from": _EFFECTIVE_FROM,
                "currency": "USD",
                "price_per_service_minor_units": price,
                "included_bytes_per_service": included,
                "max_billed_services": cap,
            }
            for slug, price, included, cap in _PRICES
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM plan_prices WHERE effective_from = :day").bindparams(
            day=_EFFECTIVE_FROM
        )
    )
