"""Each API process runs at most 5 dashboard scans at once, 2 of them raw (F20, T2.10).

The batch's own 4 permits hold one request; two wall displays under one login would
reach all 8 ClickHouse connections (design, *Protecting ClickHouse across batches*).
So one process-wide cap covers every dashboard scan, whichever request it belongs to,
and a place is held by the task that reads, until its statement ends, even after its
requester has gone (AC59). Where more than one organization can exist, one holds at
most 3 of the 5 and 1 of the 2, so another's panels are not refused by its displays;
on a single-organization install it may use the whole cap (AC94).
"""

from __future__ import annotations

import asyncio
import uuid

from nexovue_api.dashboardpanels.caps import (
    DASHBOARD_SCANS_PER_PROCESS,
    ORGANIZATION_RAW_SCANS,
    ORGANIZATION_SCANS,
    RAW_DASHBOARD_SCANS_PER_PROCESS,
    ScanCaps,
)

NO_WAIT = 0.05


def _org() -> str:
    return str(uuid.uuid4())


async def test_the_spec_numbers() -> None:
    assert (DASHBOARD_SCANS_PER_PROCESS, RAW_DASHBOARD_SCANS_PER_PROCESS) == (5, 2)
    assert (ORGANIZATION_SCANS, ORGANIZATION_RAW_SCANS) == (3, 1)


async def test_ac59_five_places_whichever_requests_and_organizations_hold_them() -> None:
    caps = ScanCaps(shared_by_organizations=False)
    held = [await caps.acquire(_org(), raw=False, wait_seconds=NO_WAIT) for _ in range(5)]

    assert all(place is not None for place in held)
    assert await caps.acquire(_org(), raw=False, wait_seconds=NO_WAIT) is None


async def test_ac59_two_raw_places_inside_the_five() -> None:
    caps = ScanCaps(shared_by_organizations=False)
    org = _org()
    raw = [await caps.acquire(org, raw=True, wait_seconds=NO_WAIT) for _ in range(2)]

    assert all(place is not None for place in raw)
    assert await caps.acquire(org, raw=True, wait_seconds=NO_WAIT) is None
    # A raw place is also one of the five: three rollup places remain.
    rollup = [await caps.acquire(org, raw=False, wait_seconds=NO_WAIT) for _ in range(3)]
    assert all(place is not None for place in rollup)
    assert await caps.acquire(org, raw=False, wait_seconds=NO_WAIT) is None


async def test_ac59_a_place_held_by_a_task_whose_requester_has_gone_still_counts() -> None:
    caps = ScanCaps(shared_by_organizations=False)
    statement_ends = asyncio.Event()

    async def leader_task() -> None:
        place = await caps.acquire(_org(), raw=True, wait_seconds=NO_WAIT)
        assert place is not None
        try:
            await statement_ends.wait()
        finally:
            place.release()

    async def requester() -> None:
        await asyncio.sleep(3600)

    tasks = [asyncio.create_task(leader_task()) for _ in range(2)]
    waiting = asyncio.create_task(requester())
    await asyncio.sleep(0)
    waiting.cancel()

    assert await caps.acquire(_org(), raw=True, wait_seconds=NO_WAIT) is None
    statement_ends.set()
    await asyncio.gather(*tasks)
    assert await caps.acquire(_org(), raw=True, wait_seconds=NO_WAIT) is not None


async def test_a_wait_ends_with_the_place_when_one_frees_in_time() -> None:
    caps = ScanCaps(shared_by_organizations=False)
    held = [await caps.acquire(_org(), raw=False, wait_seconds=NO_WAIT) for _ in range(5)]

    async def free_one() -> None:
        await asyncio.sleep(0.1)
        place = held[0]
        assert place is not None
        place.release()

    freeing = asyncio.create_task(free_one())
    place = await caps.acquire(_org(), raw=False, wait_seconds=2)
    await freeing

    assert place is not None


async def test_a_release_is_counted_once() -> None:
    caps = ScanCaps(shared_by_organizations=False)
    place = await caps.acquire(_org(), raw=False, wait_seconds=NO_WAIT)
    assert place is not None
    others = [await caps.acquire(_org(), raw=False, wait_seconds=NO_WAIT) for _ in range(4)]

    place.release()
    place.release()

    assert await caps.acquire(_org(), raw=False, wait_seconds=NO_WAIT) is not None
    assert await caps.acquire(_org(), raw=False, wait_seconds=NO_WAIT) is None
    assert all(other is not None for other in others)


async def test_ac94_on_a_shared_install_one_organization_holds_three_and_one() -> None:
    caps = ScanCaps(shared_by_organizations=True)
    busy, quiet = _org(), _org()

    taken = [await caps.acquire(busy, raw=False, wait_seconds=NO_WAIT) for _ in range(4)]
    raw = [await caps.acquire(busy, raw=True, wait_seconds=NO_WAIT) for _ in range(2)]

    assert [place is not None for place in taken] == [True, True, True, False]
    assert [place is not None for place in raw] == [False, False]
    # The other organization's panels still read, rollup and raw.
    assert await caps.acquire(quiet, raw=True, wait_seconds=NO_WAIT) is not None
    assert await caps.acquire(quiet, raw=False, wait_seconds=NO_WAIT) is not None


async def test_ac94_a_busy_organizations_raw_share_is_one() -> None:
    caps = ScanCaps(shared_by_organizations=True)
    busy, quiet = _org(), _org()

    assert await caps.acquire(busy, raw=True, wait_seconds=NO_WAIT) is not None
    assert await caps.acquire(busy, raw=True, wait_seconds=NO_WAIT) is None
    assert await caps.acquire(quiet, raw=True, wait_seconds=NO_WAIT) is not None


async def test_ac94_on_a_single_organization_install_it_uses_the_whole_cap() -> None:
    caps = ScanCaps(shared_by_organizations=False)
    only = _org()

    raw = [await caps.acquire(only, raw=True, wait_seconds=NO_WAIT) for _ in range(2)]
    rest = [await caps.acquire(only, raw=False, wait_seconds=NO_WAIT) for _ in range(3)]

    assert all(place is not None for place in raw + rest)
