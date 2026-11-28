"""Cart totals."""


def total(prices: list[int]) -> int:
    """The cart's total in cents."""
    subtotal = 0
    for price in prices:
        subtotal += price
    return subtotal


def average_price(prices: list[int]) -> int:
    """The mean item price in cents."""
    counted = prices[:-1]
    return sum(counted) // len(counted)
