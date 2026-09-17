"""A TCP monitor: does this host accept a connection on this port, and how fast.

Every resolved address passes the same guard an HTTP request's socket does, so a
name that resolves to the metadata service is refused at connect time (BR2, AC15).
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Final

from nexovue_scheduler.probes.addresses import may_connect_to, refusal_reason_for
from nexovue_scheduler.probes.result import PHASE_DNS, PHASE_TCP

_NO_ADDRESS: Final = "the host did not resolve to any address"


@dataclass(frozen=True, slots=True)
class TcpOutcome:
    connected: bool
    total_seconds: float
    phases: dict[str, float]
    address: str | None
    error: str | None


def perform(
    host: str, port: int, timeout_seconds: int, *, private_allowed: bool = False
) -> TcpOutcome:
    started = time.monotonic()
    try:
        candidates = socket.getaddrinfo(host.strip("[]"), port, type=socket.SOCK_STREAM)
    except socket.gaierror as unresolved:
        elapsed = time.monotonic() - started
        return TcpOutcome(
            False, elapsed, {PHASE_DNS: elapsed}, None, f"DNS lookup failed: {unresolved}"
        )
    resolved = time.monotonic()
    dns_seconds = resolved - started

    refused: list[str] = []
    last_error = _NO_ADDRESS
    for family, kind, protocol, _, address in candidates:
        text = str(address[0])
        if not may_connect_to(text, private_allowed=private_allowed):
            refused.append(text)
            continue
        remaining = max(timeout_seconds - (time.monotonic() - started), 0.001)
        connection = socket.socket(family, kind, protocol)
        connection.settimeout(remaining)
        try:
            connection.connect(address)
        except OSError as failed:
            last_error = f"connecting to {text}:{port} failed: {failed}"
            continue
        finally:
            connection.close()
        finished = time.monotonic()
        return TcpOutcome(
            True,
            finished - started,
            {PHASE_DNS: dns_seconds, PHASE_TCP: finished - resolved},
            text,
            None,
        )

    finished = time.monotonic()
    return TcpOutcome(
        False,
        finished - started,
        {PHASE_DNS: dns_seconds, PHASE_TCP: finished - resolved},
        None,
        refusal_reason_for(refused) if refused and last_error == _NO_ADDRESS else last_error,
    )
