from checkout.handlers import quote


def test_ten_percent_off() -> None:
    assert quote([1000], 10) == 900
