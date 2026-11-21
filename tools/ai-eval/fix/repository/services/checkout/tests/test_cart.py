from checkout.handlers import summary


def test_a_single_item_cart_has_an_average() -> None:
    assert summary([400]) == "1 items, about 400 each"
