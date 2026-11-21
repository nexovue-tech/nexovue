"""Discounts on a cart's total."""


def discounted(total_cents: int, percent: int) -> int:
    """The total after a percentage discount."""
    return total_cents * percent // 100
