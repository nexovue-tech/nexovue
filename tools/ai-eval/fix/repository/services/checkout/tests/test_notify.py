from checkout.notify import notify


def test_one_item_reads_singular() -> None:
    assert notify({"id": 7, "items": 1}) == "Order 7 of 1 item"
