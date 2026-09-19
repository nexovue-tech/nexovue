"""Posting to a receiver, and what its answer means for the outbox (FR19).

Through the prober's own client — `probes.http_check.perform` — so every delivery
passes the same connect-time guard a check does: a hostname that resolves to a
private, loopback, link-local or metadata address is refused after DNS, whatever
it was written as (BR7, AC4). Redirects are not followed: a receiver that moves is
a configuration to fix, not a place to send an alert that may carry a credential.
"""

from __future__ import annotations

from typing import Final

from nexovue_scheduler.alerts.outbox import SendResult
from nexovue_scheduler.probes.addresses import REFUSAL_REASON
from nexovue_scheduler.probes.http_check import PreparedRequest, perform

TIMEOUT_SECONDS: Final = 10

# Stated without the address: the address a name resolved to inside our network
# is a fact about our network (AC4).
NOT_PUBLIC: Final = "the destination is not a public address"

# Enough of a refusal's body to say why — "invalid_token", "invalid routing key" —
# and no more; a receiver's body is its own and is not ours to keep.
REASON_BODY_LIMIT: Final = 200

_JSON: Final = ("Content-Type", "application/json")


def post(
    url: str,
    body: bytes,
    *,
    headers: list[tuple[str, str]] | None = None,
    private_allowed: bool = False,
    secret: str | None = None,
) -> SendResult:
    """Send one JSON body and classify the answer. `secret` is removed from any
    reason recorded, in case a receiver echoes it."""
    exchange = perform(
        PreparedRequest(
            method="POST",
            url=url,
            headers=[_JSON, *(headers or [])],
            body=body,
            follow_redirects=False,
            timeout_seconds=TIMEOUT_SECONDS,
        ),
        private_allowed=private_allowed,
    )
    if exchange.error is not None:
        if exchange.error == REFUSAL_REASON:
            return SendResult(outcome="failed", reason=NOT_PUBLIC)
        # The network, a timeout, a name that does not resolve: worth another try.
        return SendResult(outcome="retry", reason=_scrub(exchange.error, url, secret))

    status = exchange.status_code or 0
    if 200 <= status < 300:
        return SendResult(outcome="delivered", http_status=status)
    said = _said(exchange.body, url, secret)
    if status == 429 or status >= 500:
        return SendResult(
            outcome="retry",
            http_status=status,
            reason=f"the receiver answered {status}{said}",
            retry_after_seconds=_retry_after(exchange.headers),
        )
    if 300 <= status < 400:
        return SendResult(
            outcome="failed",
            http_status=status,
            reason=f"the receiver answered {status}, a redirect, which is not followed",
        )
    return SendResult(
        outcome="failed", http_status=status, reason=f"the receiver answered {status}{said}"
    )


def _said(body: bytes, url: str, secret: str | None) -> str:
    text = body[: REASON_BODY_LIMIT * 4].decode("utf-8", "replace").strip()
    if not text:
        return ""
    text = " ".join(text.split())[:REASON_BODY_LIMIT]
    return f": {_scrub(text, url, secret)}"


def _scrub(text: str, url: str, secret: str | None) -> str:
    """No reason may carry the destination's credential (AC2)."""
    for value in (url, secret):
        if value:
            text = text.replace(value, "[redacted]")
    return text


def _retry_after(headers: list[tuple[str, str]]) -> int | None:
    for name, value in headers:
        if name.lower() == "retry-after":
            try:
                return max(0, int(value.strip()))
            except ValueError:
                return None  # an HTTP date; the backoff is enough
    return None
