from checkout.handlers import ship


def test_a_heavy_parcel_has_a_rate() -> None:
    assert ship("parcel", 8) == 1410
