"""The parcel carrier's rates."""

BASE_CENTS = 450
PER_KG_CENTS = 120
HEAVY_FROM_KG = 5


def rate(weight_kg: int) -> int | None:
    """The charge for one parcel, in cents."""
    if weight_kg <= HEAVY_FROM_KG:
        return BASE_CENTS + weight_kg * PER_KG_CENTS
    return None
