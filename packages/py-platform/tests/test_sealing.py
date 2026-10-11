"""Sealing under a keyring: what opens, what is refused (readiness blocker 8)."""

from __future__ import annotations

import pytest
from nexovue_platform.checks.sealing import (
    FORMAT_VERSION,
    Keyring,
    SealedBox,
    SealError,
    decode_key,
    generate_key,
    key_id,
    keyring_from,
    sealed_key_id,
)

OLD = decode_key(generate_key())
NEW = decode_key(generate_key())


def _box(keyring: Keyring, purpose: str = "check-secret") -> SealedBox:
    return SealedBox(keyring, purpose=purpose)


def test_a_sealed_value_opens_only_for_the_owner_and_purpose_it_was_sealed_for() -> None:
    box = _box(Keyring(current=NEW))
    sealed = box.seal("hunter2", owner="project-a:TOKEN")

    assert box.open(sealed, owner="project-a:TOKEN") == "hunter2"
    with pytest.raises(SealError):
        box.open(sealed, owner="project-b:TOKEN")
    with pytest.raises(SealError):
        _box(Keyring(current=NEW), purpose="totp").open(sealed, owner="project-a:TOKEN")
    assert b"hunter2" not in sealed


def test_a_value_names_the_key_that_sealed_it() -> None:
    sealed = _box(Keyring(current=NEW)).seal("hunter2", owner="o")

    assert sealed[0] == FORMAT_VERSION
    assert sealed_key_id(sealed) == key_id(NEW)


def test_after_a_rotation_the_old_key_still_opens_and_the_new_one_seals() -> None:
    before = _box(Keyring(current=OLD)).seal("hunter2", owner="o")
    rotated = _box(Keyring(current=NEW, previous=(OLD,)))

    assert rotated.open(before, owner="o") == "hunter2"
    assert sealed_key_id(rotated.seal("hunter2", owner="o")) == key_id(NEW)


def test_a_value_under_a_key_the_keyring_dropped_is_refused() -> None:
    before = _box(Keyring(current=OLD)).seal("hunter2", owner="o")

    with pytest.raises(SealError):
        _box(Keyring(current=NEW)).open(before, owner="o")


def test_a_value_pointed_at_another_key_does_not_open() -> None:
    """The key id is authenticated: rewriting it cannot select a different key."""
    keyring = Keyring(current=NEW, previous=(OLD,))
    sealed = bytearray(_box(keyring).seal("hunter2", owner="o"))
    sealed[1 : 1 + len(key_id(OLD))] = key_id(OLD)

    with pytest.raises(SealError):
        _box(keyring).open(bytes(sealed), owner="o")


def test_the_format_before_key_ids_is_not_read() -> None:
    version_one = bytes([1]) + b"\x00" * 40

    assert sealed_key_id(version_one) is None
    with pytest.raises(SealError):
        _box(Keyring(current=NEW)).open(version_one, owner="o")


def test_a_keyring_reads_the_environment_and_refuses_a_repeated_key() -> None:
    current, previous = generate_key(), generate_key()

    keyring = keyring_from(current, f" {previous} ,")

    assert keyring.current == decode_key(current)
    assert keyring.previous == (decode_key(previous),)
    with pytest.raises(ValueError):
        keyring_from(current, current)
    with pytest.raises(ValueError):
        keyring_from(current, "not-a-key")
