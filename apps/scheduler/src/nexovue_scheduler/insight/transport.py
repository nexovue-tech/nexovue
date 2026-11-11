"""Every provider call's one way out (foundation FD3; design *Provider interface*).

A wire format builds the bytes (`nexovue_platform.ai.formats`); this sends them through the
prober's guarded client, `probes.http_check.perform`, so the address is judged at connect
time, when it can no longer change (DNS rebinding), and:

- **redirects are never followed** (AC38): a provider answering 302 is a failed call;
- **plain `http` reaches only an address the operator listed** in `AI_PRIVATE_ENDPOINTS`, and
  never a public one; `https` reaches a public address, or a listed private one (FR32);
- loopback and link-local stay refused even when listed (`PrivateEndpoints`);
- a header holding a control character is never sent (AC34);
- the answer is capped at `perform`'s 1 MiB, and a longer one is a failed call.

A failure is a closed word, never the client's text, which can carry an address from inside
this network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol
from urllib.parse import urlsplit

from nexovue_platform.ai.contract import HttpCall

from nexovue_scheduler.probes.addresses import REFUSAL_REASON, PrivateEndpoints
from nexovue_scheduler.probes.http_check import HEADER_REFUSAL, PreparedRequest, perform

# Says who is calling in a provider's or a local server's logs.
AI_USER_AGENT: Final = "nexovue-ai/0.1 (+https://nexovue.com/ai)"

TransportFailure = Literal[
    "address_refused", "timed_out", "unreachable", "not_allowed", "too_large"
]


@dataclass(frozen=True, slots=True)
class Answer:
    """What came back: a status and body, or the one word for why nothing did."""

    status: int | None
    body: bytes
    failure: TransportFailure | None = None

    @property
    def reached(self) -> bool:
        """Whether the request may have reached the provider, and so may be billed."""
        return self.status is not None or self.failure in ("timed_out", "too_large")


class Send(Protocol):
    """`send`'s shape, so a tool or test can stand a fake provider in its place."""

    def __call__(
        self, call: HttpCall, *, timeout_seconds: int, private_endpoints: PrivateEndpoints
    ) -> Answer: ...


def send(call: HttpCall, *, timeout_seconds: int, private_endpoints: PrivateEndpoints) -> Answer:
    scheme = urlsplit(call.url).scheme
    if scheme not in ("https", "http"):
        return Answer(None, b"", "not_allowed")
    exchange = perform(
        PreparedRequest(
            method=call.method,
            url=call.url,
            headers=list(call.headers),
            body=call.body,
            follow_redirects=False,
            timeout_seconds=timeout_seconds,
            user_agent=AI_USER_AGENT,
        ),
        private_endpoints=private_endpoints,
        public_allowed=scheme == "https",
    )
    if exchange.error is not None:
        return Answer(None, b"", _failure_of(exchange.error))
    if exchange.body_truncated:
        return Answer(exchange.status_code, b"", "too_large")
    return Answer(exchange.status_code, exchange.body)


def _failure_of(error: str) -> TransportFailure:
    if error == HEADER_REFUSAL:
        return "not_allowed"
    if error == REFUSAL_REASON:
        return "address_refused"
    if "timed out" in error.lower() or "timeout" in error.lower():
        return "timed_out"
    return "unreachable"
