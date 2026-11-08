"""How many dashboard scans one API process runs at once (F20, FR13, D26).

The API holds 8 connections to ClickHouse (`settings.py`), shared by every tenant and
every screen, so dashboards together may hold at most 5, and at most 2 on raw data,
which costs the most (design, *Protecting ClickHouse across batches*). A place belongs
to the task that reads and is held until its statement ends, whoever asked for it, so
no load goes uncounted when a requester leaves (AC59).

**A share per organization** where more than one can exist (hosted, or signup open):
one holds at most 3 of the 5 and 1 of the 2, so another's panels are not refused during
its incident by the first one's wall displays. On a single-organization install the
share is the whole cap, so a self-hosted customer loses nothing (AC94).

A wait polls, as the middleware's slot wait does (`middleware/ratelimit.py`): nothing
here awaits between checking a count and taking a place, so on one event loop each is
atomic. Every number is a *guess* until plan T5.7 measures it.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Final

DASHBOARD_SCANS_PER_PROCESS: Final = 5
RAW_DASHBOARD_SCANS_PER_PROCESS: Final = 2
ORGANIZATION_SCANS: Final = 3
ORGANIZATION_RAW_SCANS: Final = 1
# How long a scan waits for a place before it reads `refused` (design section 5).
CAP_WAIT_SECONDS: Final = 5.0
_POLL_SECONDS: Final = 0.05


@dataclass(slots=True)
class _Count:
    limit: int
    share: int
    held: int = 0
    by_organization: Counter[str] = field(default_factory=Counter)

    def fits(self, org_id: str) -> bool:
        return self.held < self.limit and self.by_organization[org_id] < self.share

    def take(self, org_id: str) -> None:
        self.held += 1
        self.by_organization[org_id] += 1

    def give_back(self, org_id: str) -> None:
        self.held -= 1
        self.by_organization[org_id] -= 1
        if self.by_organization[org_id] <= 0:
            del self.by_organization[org_id]


class Place:
    """One held place, and a raw one too for a raw scan. Released once, however often asked."""

    def __init__(self, caps: ScanCaps, org_id: str, raw: bool) -> None:
        self._caps = caps
        self._org_id = org_id
        self._raw = raw
        self._held = True

    def release(self) -> None:
        if self._held:
            self._held = False
            self._caps._give_back(self._org_id, raw=self._raw)


class ScanCaps:
    """The process's dashboard scan places. One per API process, built with the app."""

    def __init__(self, *, shared_by_organizations: bool) -> None:
        self._all = _Count(
            limit=DASHBOARD_SCANS_PER_PROCESS,
            share=ORGANIZATION_SCANS if shared_by_organizations else DASHBOARD_SCANS_PER_PROCESS,
        )
        self._raw = _Count(
            limit=RAW_DASHBOARD_SCANS_PER_PROCESS,
            share=(
                ORGANIZATION_RAW_SCANS
                if shared_by_organizations
                else RAW_DASHBOARD_SCANS_PER_PROCESS
            ),
        )

    async def acquire(self, org_id: str, *, raw: bool, wait_seconds: float) -> Place | None:
        """A place within `wait_seconds`, or None: the scan then reads `refused`."""
        deadline = time.monotonic() + wait_seconds
        while True:
            if self._all.fits(org_id) and (not raw or self._raw.fits(org_id)):
                self._all.take(org_id)
                if raw:
                    self._raw.take(org_id)
                return Place(self, org_id, raw)
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(_POLL_SECONDS)

    def _give_back(self, org_id: str, *, raw: bool) -> None:
        self._all.give_back(org_id)
        if raw:
            self._raw.give_back(org_id)
