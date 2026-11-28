"""The bed's deploy scheduler: signed deploy records posted through the real webhook, over at least
7 days, at varied rates per service, with decoys (plan T2.15; AC62).

Three kinds of deploy, each a `Planted` the scorer reads back:

- `cause`: the staged deploy of a "deploy" fault's service, 4 minutes before the injection;
- `decoy`: a deploy unrelated to the fault that follows it, 4 minutes before an injection of a
  fault no rule can see, on every other round, so the false-cause rate on a coincidence is measured;
- `background`: each service's own rate (some deploy rarely, some past daily), so the base rate
  and the frequent-deployer band have something to bite on (FR9).

**The deploy-to-fault link is staged.** The bed plants the cause, so a right score proves a rule
fires, not that it is accurate (AC62). Dev stack and test installs only: the secret is the
project's own deploy secret, passed on the command line, and it is never printed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from answer_keys import KEYS, cause_version
from score import Planted

from nexovue_api.deploys.schemas import MAX_ITEMS
from nexovue_api.deploys.signatures import SIGNATURE_HEADER

# A staged cause or decoy lands this long before the injection starts, inside the rules' 30.
LEAD: Final = timedelta(minutes=4)
MIN_DAYS: Final = 7
# Deploys a day per service, cycled across the services in name order: rare, daily, busy, and past
# the frequent-deployer line. A guess; the bed's report gives each service's band as measured.
RATES_PER_DAY: Final = (0.2, 1.0, 4.0, 24.0)
# A background deploy keeps this far from an injection's start, so only the staged and decoy
# deploys can line up with a fault.
KEEP_CLEAR: Final = timedelta(minutes=45)
WEBHOOK_PATH: Final = "/api/v1/deploys/webhook/{project_id}"

Post = Callable[[str, bytes, Mapping[str, str]], int]


@dataclass(frozen=True, slots=True)
class Window:
    """One injection the scheduler plans around: what the fault bed's plan says."""

    fault: str
    round: int
    start: datetime


def rate_of(service: str, services: Sequence[str]) -> float:
    return RATES_PER_DAY[sorted(services).index(service) % len(RATES_PER_DAY)]


def build_plan(
    *,
    services: Sequence[str],
    windows: Sequence[Window],
    start: datetime,
    days: int = MIN_DAYS,
    seed: int = 1,
) -> list[Planted]:
    """Every deploy of the run, in time order. Fixed by `seed`, so a rerun posts the same ones
    (their delivery ids repeat, and the webhook answers a repeat without storing it)."""
    if days < MIN_DAYS:
        raise ValueError(f"the scheduler runs at least {MIN_DAYS} days (AC62)")
    if start.tzinfo is None:
        raise ValueError("the plan's start carries its zone")
    rng = random.Random(seed)  # a plan, never a secret
    planned: list[Planted] = []
    for window in windows:
        at = window.start - LEAD
        for key in (each for each in KEYS if each.fault == window.fault):
            if key.truth == "deploy":
                version = cause_version(window.fault, window.round)
                planned.append(Planted(key.service, version, at, "cause"))
            elif key.truth == "nothing" and window.round % 2 == 0:
                version = f"bed-decoy-{window.fault}-r{window.round}"
                planned.append(Planted(key.service, version, at, "decoy"))
    clear_of = [w.start for w in windows]
    end = start + timedelta(days=days)
    for service in services:
        per_day = rate_of(service, services)
        count = max(1, round(per_day * days))
        for number in range(count):
            at = start + timedelta(seconds=rng.uniform(0, (end - start).total_seconds()))
            if any(abs(at - fault_start) < KEEP_CLEAR for fault_start in clear_of):
                continue
            planned.append(
                Planted(service, f"bed-{service}-{number}", at.replace(microsecond=0), "background")
            )
    return sorted(planned, key=lambda p: (p.at, p.service, p.version))


def delivery_id(planted: Planted) -> str:
    """Stable, so a resumed run posts the same id and the webhook counts a repeat."""
    text = f"{planted.service}|{planted.version}|{planted.at.isoformat()}"
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def batch_body(deploys: Iterable[Planted]) -> bytes:
    """F28's signed envelope: each item carries its own id and time (D6)."""
    items = [
        {
            "delivery_id": delivery_id(each),
            "service_name": each.service,
            "version": each.version,
            "deployed_at": each.at.isoformat(),
        }
        for each in deploys
    ]
    if not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f"a batch holds 1 to {MAX_ITEMS} deploys")
    return json.dumps({"deploys": items}, separators=(",", ":")).encode()


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_batch(
    base_url: str, project_id: str, secret: str, deploys: Sequence[Planted], post: Post
) -> None:
    body = batch_body(deploys)
    url = base_url.rstrip("/") + WEBHOOK_PATH.format(project_id=project_id)
    status = post(
        url, body, {"content-type": "application/json", SIGNATURE_HEADER: sign(body, secret)}
    )
    if status >= 300:
        raise RuntimeError(f"the webhook refused {len(deploys)} deploys: HTTP {status}")


def _sent(ledger: Path | None) -> set[str]:
    if ledger is None or not ledger.exists():
        return set()
    return {
        line.strip() for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()
    }


def run(
    plan: Sequence[Planted],
    *,
    base_url: str,
    project_id: str,
    secret: str,
    post: Post,
    ledger: Path | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 30.0,
) -> int:
    """Post each deploy when its time comes, until the plan is done; the number posted this
    run. `ledger` keeps what was posted, so a stopped run resumes without a repeat."""
    done = _sent(ledger)
    waiting = [each for each in plan if delivery_id(each) not in done]
    posted = 0
    while waiting:
        due = [each for each in waiting if each.at <= now()]
        for first in range(0, len(due), MAX_ITEMS):
            batch = due[first : first + MAX_ITEMS]
            post_batch(base_url, project_id, secret, batch, post)
            if ledger is not None:
                with ledger.open("a", encoding="utf-8") as kept:
                    kept.writelines(delivery_id(each) + "\n" for each in batch)
            posted += len(batch)
        waiting = [each for each in waiting if each not in due]
        if waiting:
            wait = (waiting[0].at - now()).total_seconds()
            sleep(max(0.0, min(poll_seconds, wait)))
    return posted
