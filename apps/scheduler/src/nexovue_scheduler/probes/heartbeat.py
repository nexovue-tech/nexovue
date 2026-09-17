"""Whether a heartbeat is late (FR16, AC14).

A heartbeat is up while its last call is within its period plus its grace. One
that has never been called is given the same allowance from the moment it was
created, and until then it has no state at all — "not called yet" is not down.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def outcome_of(
    *,
    period_seconds: int,
    grace_seconds: int,
    last_ping_at: datetime | None,
    created_at: datetime,
    now: datetime,
) -> str | None:
    allowance = timedelta(seconds=period_seconds + grace_seconds)
    if last_ping_at is None:
        return "down" if now > created_at + allowance else None
    return "up" if now <= last_ping_at + allowance else "down"


def reason_for(
    *, period_seconds: int, grace_seconds: int, last_ping_at: datetime | None, now: datetime
) -> str:
    if last_ping_at is None:
        return f"never called; expected every {period_seconds}s with {grace_seconds}s grace"
    late = int((now - last_ping_at).total_seconds())
    return f"last called {late}s ago; expected every {period_seconds}s with {grace_seconds}s grace"
