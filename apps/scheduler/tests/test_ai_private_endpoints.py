"""`AI_PRIVATE_ENDPOINTS` at the connect-time guard (F3 plan T4.2; FR32, AC37). No network:
the guard is handed the address libcurl resolved, as `test_connect_guard.py` does, and this
suite needs no DNS, so it runs where that suite's live rebinding case cannot.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

import pytest

from nexovue_scheduler.probes.addresses import REFUSED, ConnectGuard, PrivateEndpoints
from nexovue_scheduler.settings import Settings


@dataclass
class FakeSockAddr:
    """The shape libcurl hands the callback: family, socktype, protocol, addr."""

    addr: tuple[Any, ...]
    family: int = socket.AF_INET
    socktype: int = socket.SOCK_STREAM
    protocol: int = 0


class TestAnOperatorsPrivateEndpoints:
    """`AI_PRIVATE_ENDPOINTS` (F3 FR32, AC37): a local model on the customer's network, by
    address and port, listed by the install operator. It widens nothing else."""

    LISTED = "10.0.0.5:11434,192.168.1.0/24:8000,[fd00::7]:11434"

    def test_a_listed_address_opens_only_on_its_port(self) -> None:
        endpoints = PrivateEndpoints.parse(self.LISTED)
        assert endpoints.allows("10.0.0.5", 11434)
        assert endpoints.allows("192.168.1.77", 8000)
        assert endpoints.allows("fd00::7", 11434)
        assert not endpoints.allows("10.0.0.5", 22)
        assert not endpoints.allows("10.0.0.6", 11434)
        assert not endpoints.allows("192.168.2.1", 8000)

    @pytest.mark.parametrize(
        "entry",
        [
            "127.0.0.1:11434",
            "127.0.0.0/8:11434",
            "169.254.169.254:80",
            "169.254.0.0/16:80",
            "[::1]:11434",
            "[fe80::1]:11434",
            "0.0.0.0:11434",
            "224.0.0.1:11434",
        ],
    )
    def test_loopback_link_local_and_the_unreachable_are_refused_even_when_listed(
        self, entry: str
    ) -> None:
        with pytest.raises(ValueError, match="not wholly private"):
            PrivateEndpoints.parse(entry)

    @pytest.mark.parametrize(
        ("entry", "words"),
        [
            ("10.0.0.5", "needs a port"),
            ("10.0.0.5:", "needs a port"),
            ("10.0.0.5:99999", "needs a port"),
            ("[fd00::7]", "needs a port"),
            ("8.8.8.8:443", "not wholly private"),
            ("100.64.0.1:11434", "not wholly private"),
            ("10.0.0.0/7:11434", "not wholly private"),
            ("10.0.0.0/8:11434", "wider than"),
            ("192.168.0.0/15:80", "not wholly private"),
            ("[fd00::/32]:11434", "wider than"),
            ("ollama.lan:11434", "not an address"),
            ("10.0.0.5/24:80", "not an address"),
        ],
    )
    def test_settings_refuse_a_bad_entry_saying_what_is_wrong(self, entry: str, words: str) -> None:
        """AC37: no port, a range not wholly private, a very wide range."""
        with pytest.raises(ValueError, match=words):
            PrivateEndpoints.parse(entry)

    def test_an_empty_setting_allows_nothing(self) -> None:
        assert PrivateEndpoints.parse("").entries == ()
        assert not PrivateEndpoints.parse(" , ").allows("10.0.0.5", 11434)

    def test_the_guard_opens_a_listed_address_and_no_other_private_one(self) -> None:
        guard = ConnectGuard(private_endpoints=PrivateEndpoints.parse("10.0.0.5:11434"))

        opened = guard.open_socket(1, FakeSockAddr(addr=("10.0.0.5", 11434)))
        assert opened != REFUSED
        opened.close()
        assert guard.open_socket(1, FakeSockAddr(addr=("10.0.0.6", 11434))) == REFUSED
        assert guard.open_socket(1, FakeSockAddr(addr=("10.0.0.5", 8080))) == REFUSED
        assert guard.open_socket(1, FakeSockAddr(addr=("169.254.169.254", 80))) == REFUSED

    def test_a_name_resolving_to_an_unlisted_private_address_is_refused(self) -> None:
        """A hostname is judged by what it resolved to, at connect time (AC37)."""
        guard = ConnectGuard(private_endpoints=PrivateEndpoints.parse("10.0.0.5:11434"))

        assert guard.open_socket(1, FakeSockAddr(addr=("10.9.9.9", 11434))) == REFUSED
        assert "not contacted" in (guard.refusal_reason() or "")

    def test_with_public_refused_only_a_listed_address_opens(self) -> None:
        """An AI call over plain `http` may reach a listed address and nothing public."""
        guard = ConnectGuard(
            private_endpoints=PrivateEndpoints.parse("10.0.0.5:11434"), public_allowed=False
        )

        assert guard.open_socket(1, FakeSockAddr(addr=("93.184.216.34", 80))) == REFUSED
        opened = guard.open_socket(1, FakeSockAddr(addr=("10.0.0.5", 11434)))
        assert opened != REFUSED
        opened.close()


def test_the_scheduler_will_not_start_on_a_bad_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC37: settings refuse it at boot, naming the variable."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql+asyncpg://u:p@db:5432/n")
    monkeypatch.setenv("SCHEDULER_ROLE", "insight")
    monkeypatch.setenv("AI_PRIVATE_ENDPOINTS", "10.0.0.0/8:11434")
    with pytest.raises(ValueError, match="wider than"):
        Settings()
    monkeypatch.setenv("AI_PRIVATE_ENDPOINTS", "10.0.0.5:11434")
    assert Settings().ai_endpoints.allows("10.0.0.5", 11434)
