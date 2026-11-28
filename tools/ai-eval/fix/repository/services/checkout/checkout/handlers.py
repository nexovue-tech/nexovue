"""The checkout endpoints."""

from importlib import import_module

from checkout.cart import average_price, total
from checkout.discount import discounted


class CheckoutError(Exception):
    """A checkout that cannot go on."""


def quote(prices: list[int], percent: int) -> int:
    """What a cart would cost after its discount."""
    full = total(prices)
    amount = discounted(full, percent)
    if amount < full // 2:
        raise CheckoutError("discount took more than half")
    return amount


def summary(prices: list[int]) -> str:
    """A cart's line for the order page."""
    return f"{len(prices)} items, about {average_price(prices)} each"


def ship(carrier: str, weight_kg: int) -> int:
    """The shipping charge from the chosen carrier."""
    module = import_module(f"checkout.carriers.{carrier}")
    charge = module.rate(weight_kg)
    if charge is None:
        raise CheckoutError("carrier gave no rate")
    return int(charge)
