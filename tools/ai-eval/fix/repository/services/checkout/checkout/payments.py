"""Calls to the payment gateway."""

from config.timeouts import PAYMENT_TIMEOUT_SECONDS


class GatewayTimeout(Exception):
    """The gateway did not answer in time."""


def charge(amount_cents: int, waited_seconds: float) -> str:
    """Charge, or time out."""
    if waited_seconds > PAYMENT_TIMEOUT_SECONDS:
        raise GatewayTimeout(f"no answer in {PAYMENT_TIMEOUT_SECONDS}s")
    return f"charged {amount_cents}"
