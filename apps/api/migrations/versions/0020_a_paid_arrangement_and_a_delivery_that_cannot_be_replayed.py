"""a paid arrangement, and a billing delivery that cannot be replayed

P1-73 puts a real payment processor behind the payments port, and two facts have
to survive a restart for that to be worth anything.

**Which arrangement an organization has, and with whom.** `organizations.plan`
says what tier a tenant is on; it says nothing about the subscription that pays
for it, so nothing can cancel one, and nothing can tell a tenant that was moved
by hand from one that pays. `billing_subscriptions` is that record — and rows
accumulate rather than being edited across arrangements, because a customer who
cancels and resubscribes has two, and the first is what a past invoice was
raised under.

**What is deliberately absent is the more important half.** The rule is the
processor's customer reference and nothing else avoidable, which is the
integrations' posture — hold no credential we can avoid — applied to money:
there is no card number here, no last four, no expiry, no PAN, no bank account,
no billing address. `Checkout` returns a **URL** rather than a form precisely so
none of that reaches this application, and a column here would be the quiet way
it arrived anyway.

**And a signed delivery is replayable forever unless something remembers it.**
That is migration 0007's lesson on the deploy webhook, and it costs more here:
the processor delivers **at least once by design**, so a duplicate is ordinary
traffic rather than an attack. `billing_webhook_deliveries` records the
processor's own per-payload id under a unique index, which is what makes the
second arrival a no-op rather than a second plan change.

That table holds **no body, no signature and no header**. The raw body is the
thing the signature covers and the thing a log must never carry; the translated
`kind` is everything anything downstream ever needed from it.

The delivery id is unique across organizations rather than within one. The id is
the processor's and so is its uniqueness — scoping it per tenant would leave a
captured delivery replayable under a different organization's name.

Revision ID: 0020
Revises: 0019
Created: 2026-09-08
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUIDV7 = sa.text("uuidv7()")
NOW = sa.text("now()")

# `pending` is a checkout that was started and not yet authorised. Every
# processor calls that something different — INITIALIZED, incomplete,
# BANK_APPROVAL_PENDING — and none of those words belongs in this schema.
BILLING_SUBSCRIPTION_STATUS = postgresql.ENUM(
    "pending", "active", "cancelled", name="billing_subscription_status", create_type=False
)

# The longest reference any processor under consideration issues (Cashfree's
# subscription id is 250 characters), rounded to the next byte.
REFERENCE_LENGTH = 255
SHORT_LABEL_LENGTH = 64


def _org_fk() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "org_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    BILLING_SUBSCRIPTION_STATUS.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "billing_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUIDV7),
        _org_fk(),
        # Which implementation of the port this arrangement lives at. The
        # vendor's name is the *value*, never the column name — the port's rule,
        # the same one that keeps `PAYMENTS_DOMESTIC_*` neutral. Two processors
        # is the expected end state, so a row has to say which one it belongs to.
        sa.Column("processor", sa.String(SHORT_LABEL_LENGTH), nullable=False),
        # Opaque, never parsed: what a later cancel names and what every webhook
        # echoes back.
        sa.Column("subscription_ref", sa.String(REFERENCE_LENGTH), nullable=False),
        # NULL until an event carries it, and NULL forever at a processor with
        # no customer object — which is not the same statement as "unknown".
        sa.Column("processor_customer_ref", sa.String(REFERENCE_LENGTH), nullable=True),
        # Ours (`pro`, `scale`), never a processor's price id. A foreign key for
        # `organizations.plan`'s reason: a typo would name a tier that grants
        # whatever the resolver fell back to.
        sa.Column(
            "plan_slug",
            sa.String(SHORT_LABEL_LENGTH),
            sa.ForeignKey("plans.slug", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", BILLING_SUBSCRIPTION_STATUS, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        # Timestamps rather than flags: "since when" is the question an invoice
        # dispute asks, and a boolean cannot answer it.
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        # Two processors may legitimately mint the same string, which is why the
        # pair is unique rather than the reference alone.
        sa.UniqueConstraint(
            "processor", "subscription_ref", name="uq_billing_subscriptions_processor_ref"
        ),
    )
    op.create_index(
        "ix_billing_subscriptions_org_id_status",
        "billing_subscriptions",
        ["org_id", "status"],
    )

    op.create_table(
        "billing_webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUIDV7),
        _org_fk(),
        sa.Column("processor", sa.String(SHORT_LABEL_LENGTH), nullable=False),
        sa.Column("delivery_id", sa.String(REFERENCE_LENGTH), nullable=False),
        # The translated event, in our vocabulary — a `BillingEventKind` value.
        # The processor's own event name is deliberately not stored: it is the
        # thing the port exists to keep out of everything above it.
        sa.Column("kind", sa.String(SHORT_LABEL_LENGTH), nullable=False),
        sa.Column("subscription_ref", sa.String(REFERENCE_LENGTH), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        # The whole replay defence.
        sa.UniqueConstraint(
            "processor", "delivery_id", name="uq_billing_webhook_deliveries_processor_delivery_id"
        ),
    )
    op.create_index(
        "ix_billing_webhook_deliveries_org_id_received_at",
        "billing_webhook_deliveries",
        ["org_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_billing_webhook_deliveries_org_id_received_at",
        table_name="billing_webhook_deliveries",
    )
    op.drop_table("billing_webhook_deliveries")
    op.drop_index("ix_billing_subscriptions_org_id_status", table_name="billing_subscriptions")
    op.drop_table("billing_subscriptions")
    # Dropping a table does not drop the enum type it used.
    BILLING_SUBSCRIPTION_STATUS.drop(op.get_bind(), checkfirst=False)
