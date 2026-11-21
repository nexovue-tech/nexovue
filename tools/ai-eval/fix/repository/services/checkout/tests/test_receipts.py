from checkout.receipts import receipt_line


def test_cents_keep_two_digits() -> None:
    assert receipt_line(1205) == "Paid 12.05"
