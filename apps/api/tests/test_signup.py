"""Slugs, keys, and the shape of what signup returns."""

from __future__ import annotations

import pytest

from nexovue_api.data.postgres.models import API_KEY_PREFIX_LENGTH, SLUG_MAX_LENGTH
from nexovue_api.signup.apikeys import (
    API_KEY_PREFIX,
    API_KEY_SECRET_LENGTH,
    generate_api_key,
    hash_api_key,
)
from nexovue_api.signup.slugs import FALLBACK_SLUG, disambiguate, slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Acme Inc", "acme-inc"),
        ("  Acme   Inc  ", "acme-inc"),
        ("Acme, Inc.", "acme-inc"),
        ("ACME", "acme"),
        ("Ünïcôde Ltd", "unicode-ltd"),
        ("123 Industries", "123-industries"),
    ],
)
def test_a_name_becomes_a_url_safe_slug(name: str, expected: str) -> None:
    assert slugify(name) == expected


@pytest.mark.parametrize("name", ["###", "   ", "-", "!!!"])
def test_a_name_with_nothing_slugable_in_it_still_yields_a_slug(name: str) -> None:
    assert slugify(name) == FALLBACK_SLUG


def test_a_slug_never_exceeds_the_column() -> None:
    slug = slugify("A" * 200 + " Corporation")

    assert len(slug) <= SLUG_MAX_LENGTH
    assert not slug.endswith("-")


def test_the_first_organization_of_a_name_keeps_the_plain_slug() -> None:
    assert disambiguate("acme-inc", 0) == "acme-inc"


def test_a_later_organization_of_the_same_name_is_suffixed() -> None:
    assert disambiguate("acme-inc", 1) == "acme-inc-2"
    assert disambiguate("acme-inc", 2) == "acme-inc-3"


def test_a_suffix_never_pushes_a_slug_past_the_column() -> None:
    base = "a" * SLUG_MAX_LENGTH

    for attempt in range(1, 12):
        assert len(disambiguate(base, attempt)) <= SLUG_MAX_LENGTH


def test_a_key_has_the_documented_shape() -> None:
    key = generate_api_key()

    assert key.startswith(API_KEY_PREFIX)
    assert len(key) == len(API_KEY_PREFIX) + API_KEY_SECRET_LENGTH
    assert key[len(API_KEY_PREFIX) :].isalnum()


def test_the_stored_prefix_is_long_enough_to_look_a_key_up() -> None:
    assert len(API_KEY_PREFIX) < API_KEY_PREFIX_LENGTH


def test_two_keys_are_never_the_same() -> None:
    assert len({generate_api_key() for _ in range(200)}) == 200


def test_a_key_hash_is_stable_and_is_not_the_key() -> None:
    key = generate_api_key()

    assert hash_api_key(key) == hash_api_key(key)
    assert key not in hash_api_key(key)


def test_the_seed_key_matches_the_format_signup_produces() -> None:
    from nexovue_api.seed import DEV_API_KEY

    assert DEV_API_KEY.startswith(API_KEY_PREFIX)
    assert len(DEV_API_KEY) == len(API_KEY_PREFIX) + API_KEY_SECRET_LENGTH
