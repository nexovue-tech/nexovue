"""A scheduler process answers whether its loop is moving (readiness blocker 7).

A process that is running is not a scheduler that is scheduling: a beat whose
loop has hung keeps its container up while every customer's checks stop. So
liveness here is the loop's own pulse, and readiness is its last contact with
Postgres — the one store a scheduler loop talks to.

The server reads timestamps only, from its own thread, so a hung loop shows as
a stale pulse rather than as a probe that hangs with it.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from prometheus_client import CollectorRegistry, Counter

from nexovue_scheduler import __main__ as scheduler_main
from nexovue_scheduler import health
from nexovue_scheduler.health import LOOP_SILENCE_LIMIT_SECONDS, Pulse


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def pulse(clock: _Clock) -> Pulse:
    return Pulse(clock=clock)


@pytest.fixture
def base_url(pulse: Pulse) -> Iterator[str]:
    registry = CollectorRegistry()
    Counter("nexovue_scheduler_test_events", "A counter for the test.", registry=registry).inc()
    server = health.serve("127.0.0.1:0", pulse, registry)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_a_loop_that_has_just_started_is_alive_and_not_yet_ready(base_url: str) -> None:
    assert httpx.get(f"{base_url}/healthz").status_code == 200
    assert httpx.get(f"{base_url}/readyz").status_code == 503


def test_a_loop_silent_past_the_limit_is_not_alive(
    base_url: str, pulse: Pulse, clock: _Clock
) -> None:
    pulse.beat()
    clock.now += LOOP_SILENCE_LIMIT_SECONDS - 1
    assert httpx.get(f"{base_url}/healthz").status_code == 200

    clock.now += 2
    stale = httpx.get(f"{base_url}/healthz")

    assert stale.status_code == 503
    assert stale.json()["silent_seconds"] > LOOP_SILENCE_LIMIT_SECONDS


def test_ready_only_while_postgres_was_reached_recently(
    base_url: str, pulse: Pulse, clock: _Clock
) -> None:
    pulse.reached_postgres()
    assert httpx.get(f"{base_url}/readyz").status_code == 200

    clock.now += LOOP_SILENCE_LIMIT_SECONDS + 1

    assert httpx.get(f"{base_url}/readyz").status_code == 503


def test_metrics_are_served_in_the_prometheus_text_format(base_url: str) -> None:
    served = httpx.get(f"{base_url}/metrics")

    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/plain")
    assert "nexovue_scheduler_test_events_total 1.0" in served.text


def test_any_other_path_is_not_found(base_url: str) -> None:
    assert httpx.get(f"{base_url}/").status_code == 404


def test_the_container_healthcheck_follows_the_pulse(
    base_url: str, pulse: Pulse, clock: _Clock
) -> None:
    """`python -m nexovue_scheduler health`, which the compose healthcheck runs."""
    address = f":{base_url.rpartition(':')[2]}"
    assert scheduler_main._alive(address)

    clock.now += LOOP_SILENCE_LIMIT_SECONDS + 1

    assert not scheduler_main._alive(address)
    assert not scheduler_main._alive(":1")
