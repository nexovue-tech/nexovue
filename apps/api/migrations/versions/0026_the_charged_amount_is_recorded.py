"""the charged amount is recorded rather than recomputed

`billing_webhook_deliveries` said *that* a charge succeeded and *when*, never
*how much*. That was deliberate — the row exists to make a replay a no-op, and
holding no body and no payload is what keeps a signed request out of our storage
— but it left the charge history with nothing to show, so the amount was derived
as `rate x services_committed`.

**A derived amount is not a record of what happened.** The rate lives in
`plan_prices`, the count on `billing_subscriptions`, and both are values that
change: a price rise, a new commitment, or a checkout spanning midnight UTC each
rewrite a charge that already happened. Provider tax was never in it at all. A
charge is a fact about a moment, and a fact that recalculates differently later
is not one.

**Minor units, in a BIGINT, with the currency beside it.** The same shape
`plan_prices.price_per_service_minor_units` uses and for the same reason: binary
floating point cannot hold 0.10, and an amount that drifts by a hundredth is an
amount a customer disputes. The currency is stored rather than inferred from the
organization, because an integer alone is not an amount — 600 is six dollars or
six hundred yen depending on a field that must not be somewhere else.

**Nullable, and no backfill.** Null means two different true things: this
delivery was not a charge, or it was one whose payload stated no amount. Neither
is nought, which is a figure somebody would reconcile against and find wrong.
Rows that arrived before this migration keep their nulls and fall back to the
derived amount, which is what the screen showed them anyway — inventing history
from today's prices is the exact error this column exists to stop.

Revision ID: 0026
Revises: 0025
Created: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "billing_webhook_deliveries"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("amount_minor_units", sa.BigInteger(), nullable=True))
    op.add_column(_TABLE, sa.Column("amount_currency", sa.String(length=3), nullable=True))


def downgrade() -> None:
    """Roll forward only (group 9, C6).

    It drops amount_minor_units and amount_currency — what every charge was.

    Going back past this revision is a restore of the database, never a
    downgrade — the records it would drop exist nowhere else.
    """
    raise RuntimeError(
        "0026 is roll forward only: its downgrade would drop billing records. "
        "Restore a backup instead."
    )
