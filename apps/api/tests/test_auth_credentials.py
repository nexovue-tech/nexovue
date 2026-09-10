"""Passwords, addresses, and the cookie the session travels in."""

from __future__ import annotations

import pytest

from nexovue_api.auth.credentials import (
    hash_password,
    is_valid_email,
    normalize_email,
    verify_password,
)
from nexovue_api.auth.schemas import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from nexovue_api.auth.sessions import SESSION_COOKIE_NAME


def test_a_password_verifies_against_its_own_hash() -> None:
    assert verify_password(hash_password("correct-horse"), "correct-horse")


def test_a_wrong_password_does_not_verify() -> None:
    assert not verify_password(hash_password("correct-horse"), "wrong-horse")


def test_a_hash_is_never_the_plaintext() -> None:
    assert hash_password("correct-horse") != "correct-horse"


def test_two_hashes_of_one_password_differ() -> None:
    """Argon2 salts each hash, so a stolen table shows no repeated passwords."""
    assert hash_password("correct-horse") != hash_password("correct-horse")


def test_a_user_with_no_password_can_never_sign_in() -> None:
    """An invited user who never set one. The argon2 cost is still paid."""
    assert not verify_password(None, "anything")
    assert not verify_password(None, "")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Nexovue@Nexovue.Com  ", "nexovue@nexovue.com"),
        ("ALREADY@LOWER.COM", "already@lower.com"),
    ],
)
def test_an_address_is_stored_trimmed_and_lowercased(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("address", ["nexovue@nexovue.com", "a.b+tag@sub.example.co.uk"])
def test_a_usable_address_is_accepted(address: str) -> None:
    assert is_valid_email(address)


@pytest.mark.parametrize(
    "address",
    [
        "",
        "no-at-sign",
        "@nexovue.local",
        "dev@",
        "dev@localhost",
        "dev@.local",
        "dev@nexovue.",
        "dev name@nexovue.local",
        "nexovue@nexovue.com\n",
    ],
)
def test_an_unusable_address_is_rejected(address: str) -> None:
    assert not is_valid_email(address)


def test_an_address_longer_than_the_column_is_rejected() -> None:
    assert not is_valid_email("a" * 320 + "@nexovue.local")


def test_the_password_bounds_are_a_range_and_not_a_point() -> None:
    assert 0 < PASSWORD_MIN_LENGTH < PASSWORD_MAX_LENGTH


def test_the_cookie_is_the_one_the_contract_names() -> None:
    assert SESSION_COOKIE_NAME == "nexovue_session"
