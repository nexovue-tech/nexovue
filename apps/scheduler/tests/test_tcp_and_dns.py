"""TCP and DNS monitors: up, down, and refused (AC15, AC16)."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import dns.resolver
import pytest
from probe_fixtures import context, definition
from recording_site import RecordingSite, private_address

from nexovue_scheduler.probes import dns_check, runner, tcp_check
from nexovue_scheduler.probes.failures import MisconfiguredError


@pytest.fixture
def closed_port() -> Iterator[int]:
    """A port that was open a moment ago and is not now."""
    holder = socket.socket()
    holder.bind(("0.0.0.0", 0))
    port = int(holder.getsockname()[1])
    holder.close()
    yield port


def test_an_open_port_is_up_and_a_closed_one_is_down(
    recording_site: RecordingSite, closed_port: int
) -> None:
    host = private_address()
    open_run, _ = runner.run(
        definition(
            {"type": "tcp", "host": host, "port": recording_site.port, "timeout_seconds": 3}
        ),
        context(),
    )
    closed_run, _ = runner.run(
        definition({"type": "tcp", "host": host, "port": closed_port, "timeout_seconds": 3}),
        context(),
    )
    assert open_run.outcome == "up"
    assert closed_run.outcome == "down"
    assert closed_run.error is not None


def test_a_tcp_host_resolving_to_the_metadata_service_is_refused() -> None:
    outcome = tcp_check.perform("169.254.169.254", 80, 2, private_allowed=True)
    assert not outcome.connected
    assert outcome.error is not None and "not contacted" in outcome.error


class _Answer:
    def __init__(self, texts: list[str]) -> None:
        self._records = [_Record(text) for text in texts]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._records)


class _Record:
    def __init__(self, text: str) -> None:
        self._text = text

    def to_text(self) -> str:
        return self._text


def test_an_answer_that_differs_from_the_assertion_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dns.resolver.Resolver, "resolve", lambda self, *args, **kwargs: _Answer(["203.0.113.9"])
    )
    config = {
        "type": "dns",
        "name": "shop.example.com",
        "record_type": "A",
        "assertions": [{"comparison": "contains", "target": "198.51.100.7"}],
    }
    evidence, _ = runner.run(definition(config), context())

    assert evidence.outcome == "down"
    assert evidence.steps[0].answers == ["203.0.113.9"]


def test_a_name_that_does_not_exist_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def nxdomain(self: object, *args: object, **kwargs: object) -> None:
        raise dns.resolver.NXDOMAIN

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", nxdomain)
    evidence, _ = runner.run(definition({"type": "dns", "name": "gone.example.com"}), context())
    assert (evidence.outcome, evidence.error) == ("down", "gone.example.com does not exist")


def test_a_resolver_at_a_private_address_is_refused() -> None:
    """AC16. Refused even with the development escape on: loopback never opens."""
    with pytest.raises(MisconfiguredError):
        dns_check.perform(
            "example.com", "A", "127.0.0.1", 2, default_resolvers=[], private_allowed=True
        )
    with pytest.raises(MisconfiguredError):
        dns_check.perform(
            "example.com", "A", "10.0.0.2", 2, default_resolvers=[], private_allowed=False
        )


def test_a_name_only_our_network_could_answer_is_refused() -> None:
    with pytest.raises(MisconfiguredError):
        dns_check.perform("postgres", "A", None, 2, default_resolvers=["1.1.1.1"])


def test_without_a_chosen_resolver_the_public_ones_are_asked_never_our_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container's resolver answers `postgres.nexovue_default` with our network."""
    asked: list[list[str]] = []

    def resolve(self: dns.resolver.Resolver, *args: object, **kwargs: object) -> _Answer:
        asked.append([str(server) for server in self.nameservers])
        return _Answer(["203.0.113.9"])

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", resolve)
    dns_check.perform("shop.example.com", "A", None, 2, default_resolvers=["1.1.1.1"])
    assert asked == [["1.1.1.1"]]
