"""Where a request gets a payment processor.

Built per request from settings rather than held on the lifespan, unlike the
mail sender: the object is a settings record and an optional client, there is no
pool to share, and building it here keeps the whole of P1-73 inside this package.
If a shared connection pool ever earns its keep, this is the one function that
moves.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from nexovue_api.billing import (
    ConfiguredProcessor,
    PaymentsNotConfiguredError,
    build_payment_processor,
)
from nexovue_api.deps import AppSettings
from nexovue_api.settings import DOMESTIC_PAYMENT_VARIABLES


def get_domestic_payments(settings: AppSettings) -> ConfiguredProcessor:
    """The domestic gateway, or a refusal naming what is unset.

    Raising here rather than handing back None means a route that needs a
    processor cannot forget to check: the dependency either supplies one or the
    request never reaches the handler — `get_email_sender`'s reasoning exactly.
    """
    configured = settings.domestic_payments
    if configured is None:
        raise PaymentsNotConfiguredError(DOMESTIC_PAYMENT_VARIABLES)
    return ConfiguredProcessor(
        name=configured.provider, processor=build_payment_processor(configured)
    )


DomesticPayments = Annotated[ConfiguredProcessor, Depends(get_domestic_payments)]
