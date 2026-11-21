"""The written answer key: for each fault and service, what the true cause is and whether F3's rules
could see it at all (plan T2.15; AC62).

**Written before any run, by the authors, and the founder reads it with the report.** Nothing here
was observed: each row comes from a flag's description in the demo's `demo.flagd.json` (as
`tools/fault-bed/fault_model.py` does) and from what the rules read. The bed *stages* a signed
deploy of the faulted service just before a "deploy" fault (`deploy_scheduler.py`), so a right
answer on those rows proves the rule fires, not that it is accurate on a real install (AC62).

Reach says whether a rule could name the cause *in principle*:

- `deploy`: a signed deploy of the finding's own service sits in the 30 minutes before (rules 1, 3);
- `dependency`: a callee's calls moved, so an edge or the callee's own finding is the cause (2, 6);
- `invisible`: no rule reads it (CPU, memory, a queue, a lost share of traffic): a cause stated here
  is wrong, and no cause is an honest miss that no rule could have avoided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Truth = Literal["deploy", "dependency", "nothing"]
Reach = Literal["deploy", "dependency", "invisible"]
REACHES: Final[tuple[Reach, ...]] = ("deploy", "dependency", "invisible")


@dataclass(frozen=True, slots=True)
class Key:
    fault: str
    service: str
    truth: Truth
    reach: Reach
    # For `dependency`: the callee that is the cause.
    callee: str | None = None
    why: str = ""

    def __post_init__(self) -> None:
        if (self.truth == "dependency") != (self.callee is not None):
            raise ValueError("a dependency key names its callee, and no other key does")
        if (self.truth == "nothing") != (self.reach == "invisible"):
            raise ValueError("a fault no rule can see has no true cause the rules could name")


def _deploy(fault: str, service: str, why: str) -> Key:
    return Key(fault, service, "deploy", "deploy", why=why)


def _invisible(fault: str, service: str, why: str) -> Key:
    return Key(fault, service, "nothing", "invisible", why=why)


KEYS: Final[tuple[Key, ...]] = (
    _deploy("adFailure", "ad", "a bad release of ad, staged"),
    _invisible("adHighCpu", "ad", "CPU pressure: no rule reads hosts or CPU"),
    _invisible("adManualGc", "ad", "garbage collection pauses: no rule reads them"),
    _deploy("cartFailure", "cart", "a bad release of cart, staged"),
    _invisible("emailMemoryLeak", "email", "a memory leak: no rule reads memory"),
    _invisible("failedReadinessProbe", "cart", "a probe the demo's compose does not have"),
    _invisible("imageSlowLoad", "frontend-proxy", "a slow proxy route: no rule reads it"),
    _deploy("intlShippingSlowdown", "shipping", "a slow release of shipping, staged"),
    _invisible("kafkaQueueProblems", "fraud-detection", "a queue backlog: no rule reads queues"),
    _deploy("paymentFailure", "payment", "a bad release of payment, staged"),
    Key(
        "paymentFailure",
        "checkout",
        "dependency",
        "dependency",
        callee="payment",
        why="checkout fails because its call to payment fails",
    ),
    Key(
        "paymentUnreachable",
        "checkout",
        "dependency",
        "dependency",
        callee="payment",
        why="checkout cannot reach payment",
    ),
    _deploy("productCatalogFailure", "product-catalog", "a bad release of product-catalog, staged"),
    _deploy("recommendationCacheFailure", "recommendation", "a bad release, staged"),
    _invisible("trafficAt40", "frontend-proxy", "lost traffic: rule 7 reads a rise, never a drop"),
)


def key_for(fault: str, service: str) -> Key | None:
    """The key for this fault on this service, or None: a finding there has no written answer."""
    for key in KEYS:
        if key.fault == fault and key.service == service:
            return key
    return None


def staged_faults() -> tuple[Key, ...]:
    """The keys whose cause is a deploy the scheduler must stage before the fault."""
    return tuple(key for key in KEYS if key.truth == "deploy")


def cause_version(fault: str, round_number: int) -> str:
    """The version the scheduler gives the staged deploy, so a scorer finds it again."""
    return f"bed-{fault}-r{round_number}"
