"""A DNS monitor: does a name resolve, and to what (FR18, AC16).

A resolver the customer chose is an address we send packets to, so it passes the
connection guard like any target. Without one the prober's own resolver answers,
which is why it is never used: without a customer's resolver the configured public
resolvers answer, and a name that only means something inside a network is refused
before anything is asked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

import dns.exception
import dns.rdatatype
import dns.resolver

from nexovue_scheduler.probes.addresses import may_connect_to, refusal_reason_for
from nexovue_scheduler.probes.failures import MisconfiguredError
from nexovue_scheduler.probes.result import PHASE_DNS

_PRIVATE_NAME_SUFFIXES: Final = (".internal", ".local", ".localhost", ".lan", ".home.arpa")


@dataclass(frozen=True, slots=True)
class DnsOutcome:
    answers: list[str]
    total_seconds: float
    error: str | None

    @property
    def phases(self) -> dict[str, float]:
        return {PHASE_DNS: self.total_seconds}


def perform(
    name: str,
    record_type: str,
    resolver_address: str | None,
    timeout_seconds: int,
    *,
    default_resolvers: list[str],
    private_allowed: bool = False,
) -> DnsOutcome:
    lowered = name.lower().rstrip(".")
    if "." not in lowered or lowered.endswith(_PRIVATE_NAME_SUFFIXES):
        raise MisconfiguredError(f"{name} is not a public domain name")

    resolver = dns.resolver.Resolver(configure=False)
    nameservers = [resolver_address] if resolver_address is not None else default_resolvers
    refused = [
        address
        for address in nameservers
        if not may_connect_to(address, private_allowed=private_allowed)
    ]
    if refused:
        raise MisconfiguredError(refusal_reason_for(refused) or "the resolver was refused")
    resolver.nameservers = nameservers

    started = time.monotonic()
    try:
        answer = resolver.resolve(
            name, dns.rdatatype.from_text(record_type), lifetime=timeout_seconds
        )
    except dns.resolver.NXDOMAIN:
        return DnsOutcome([], time.monotonic() - started, f"{name} does not exist")
    except dns.resolver.NoAnswer:
        return DnsOutcome([], time.monotonic() - started, f"{name} has no {record_type} record")
    except dns.exception.Timeout:
        return DnsOutcome([], time.monotonic() - started, "the resolver did not answer in time")
    except dns.exception.DNSException as failed:
        return DnsOutcome([], time.monotonic() - started, f"resolution failed: {failed}")
    return DnsOutcome([_text(record) for record in answer], time.monotonic() - started, None)


def _text(record: object) -> str:
    """An answer as a person writes it: an address, a target name, `10 mail.example.`,
    or a TXT record's strings joined."""
    strings = getattr(record, "strings", None)
    if strings is not None:
        return "".join(part.decode("utf-8", "replace") for part in strings)
    return record.to_text().rstrip(".") if hasattr(record, "to_text") else str(record)
