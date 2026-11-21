from checkout.payments import charge


def test_a_normal_answer_is_not_a_timeout() -> None:
    assert charge(100, 0.4) == "charged 100"
