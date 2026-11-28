"""NFR2's named timing test: how long a suggested fix click takes to answer with its new state,
on a running stack (F5 plan T9.2; spec NFR2).

**A figure is reported; there is no pass mark** (NFR2: "unmeasured, so a figure is reported and
it does not fail the gate"). The test fails only if a click does not answer with the state it
should, never on time.

It drives the real API over HTTP, as a person signed up through the public signup route, so the
session lookup, the routes and their Postgres transactions are all in the figure. The API is this
test's own, under uvicorn on the stack's stores (`assistant_served`), because the clicks need an
install the dev stack's API is not: self-hosted with AI available (a hosted install offers no
suggested fix), held causes shown and signup open, as `general_fix_rows.AI_ON` sets for the
in-process suites. What the API does not create by a route here (an
AI connection already agreed to, a Fix App reaching a repository, findings with their facts) is
written to Postgres first, as the route suites write it. Three clicks, each `SAMPLES` times on
its own finding:

- **Suggest a general fix** (rung 2b): `POST .../fixes/general`, then the section's read;
- **Ask for a suggested fix** with typed paths (rung 3): `POST .../fixes`, then its read;
- **Was this suggestion useful?** on a written suggestion: `POST .../verdict`.

No job it adds is run: each is held as the API commits it (`held_jobs.jobs_held`), so the
stack's `scheduler-insight` never takes it, moves the row the read checks, or reaches
`provider.example`; its organization is soft-deleted at the end and its jobs removed. Run it
with `-s` to see the figures:

    uv run pytest -s apps/api/tests/test_fix_click_timing_live.py

The first click of a run includes the API's first use of its connection pool.
"""

from __future__ import annotations

import asyncio
import socket
import statistics
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import Any, Final
from urllib.parse import urlsplit

import general_fix_rows as rows
import httpx
import pytest
import source_fix_rows as source
from assistant_served import running, served_settings
from held_jobs import jobs_held
from live import require_stack
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres.models import Organization, Project, User
from nexovue_api.main import create_app
from nexovue_api.settings import API_PREFIX

# The install the clicks need, on top of `served_settings`' self-hosted one with AI available.
TIMED_INSTALL: Final = {**rows.AI_ON, "SIGNUP_ENABLED": "true"}
SAMPLES: Final = 10
REQUEST_TIMEOUT_SECONDS: Final = 30.0
# NFR2's wish, printed beside the figure for the reader; never asserted.
NFR2_SECONDS: Final = 1.0
TYPED_PATH: Final = f"{source.FOLDER}/app/handler.py"


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        address = (parts.hostname or "localhost", parts.port or default_port)
        with socket.create_connection(address, 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(rows.POSTGRES_DSN, 5432),
    suite="apps/api/tests/test_fix_click_timing_live.py",
    remedy="Postgres is not running — start the stack with `just dev`",
)


@dataclass(frozen=True, slots=True)
class Timed:
    click: str
    seconds: list[float]

    def line(self) -> str:
        ordered = sorted(self.seconds)
        p95 = ordered[max(0, round(0.95 * len(ordered)) - 1)]
        return (
            f"{self.click}: n={len(ordered)}, median {statistics.median(ordered) * 1000:.0f} ms, "
            f"95th {p95 * 1000:.0f} ms, slowest {ordered[-1] * 1000:.0f} ms "
            f"(NFR2 wishes under {NFR2_SECONDS:.0f} s; not asserted)"
        )


def _sign_up(http: httpx.Client) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    response = http.post(
        f"{API_PREFIX}/signup",
        json={
            "organization_name": f"Click timing {suffix}",
            # .test is reserved by RFC 2606 and can never be a real inbox.
            "email": f"f5-timing-{suffix}@nexovue.test",
            "password": f"f5-timing-{suffix}-password",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _prepare(org_id: uuid.UUID, now: datetime) -> dict[str, list[Any]]:
    """AI on, a Fix App reaching one repository with a consent, and `SAMPLES` findings for
    each click, committed so the running API reads them."""
    engine = create_async_engine(rows.ASYNC_DSN)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            tenant = SimpleNamespace(
                organization=await db.get(Organization, org_id),
                project=await db.scalar(select(Project).where(Project.org_id == org_id)),
                user=await db.scalar(select(User).where(User.org_id == org_id)),
            )
            await rows.connect(db, tenant)  # type: ignore[arg-type]
            repository = await source.fix_app(db, tenant)  # type: ignore[arg-type]
            await source.consent(db, tenant, repository)  # type: ignore[arg-type]
            made: dict[str, list[Any]] = {"general": [], "source": [], "verdict": []}
            for index in range(SAMPLES):
                general = await rows.finding(db, tenant, now, service_name=f"general-{index}")  # type: ignore[arg-type]
                await rows.version(db, general, now)
                made["general"].append(general.id)
                service = f"source-{index}"
                await source.location(db, tenant, repository, service=service)  # type: ignore[arg-type]
                asked = await rows.finding(db, tenant, now, service_name=service)  # type: ignore[arg-type]
                version = await rows.version(db, asked, now)
                version.evidence = source.EVIDENCE
                made["source"].append(asked.id)
                judged = await rows.finding(db, tenant, now, service_name=f"verdict-{index}")  # type: ignore[arg-type]
                await rows.version(db, judged, now)
                written = await rows.fix(db, judged, now)
                made["verdict"].append((judged.id, written.id))
            await db.commit()
            return made
    finally:
        await engine.dispose()


async def _forget(org_id: uuid.UUID) -> None:
    engine = create_async_engine(rows.ASYNC_DSN)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM jobs WHERE org_id = :org"), {"org": org_id})
            await connection.execute(
                text("UPDATE organizations SET deleted_at = now() WHERE id = :org"), {"org": org_id}
            )
    finally:
        await engine.dispose()


def _time(click: Callable[[], None]) -> float:
    started = time.perf_counter()
    click()
    return time.perf_counter() - started


def test_nfr2_each_click_answers_with_its_new_state_and_its_time_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    app = create_app(served_settings(monkeypatch, TIMED_INSTALL))
    with (
        running(app) as origin,
        httpx.Client(base_url=origin, timeout=REQUEST_TIMEOUT_SECONDS) as http,
    ):
        signed_up = _sign_up(http)
        org_id = uuid.UUID(signed_up["organization"]["id"])
        try:
            with jobs_held(app.state.resources.engine, org_id):
                made = asyncio.run(_prepare(org_id, now))
                findings = f"{API_PREFIX}/findings"

                def general(finding_id: uuid.UUID) -> None:
                    asked = http.post(
                        f"{findings}/{finding_id}/fixes/general", json={"language": "python"}
                    )
                    assert asked.status_code == 202, asked.text
                    read = http.get(f"{findings}/{finding_id}/fixes/general")
                    assert read.status_code == 200 and read.json()["latest"]["state"] == "proposing"

                def ask(finding_id: uuid.UUID) -> None:
                    asked = http.post(
                        f"{findings}/{finding_id}/fixes", json={"paths": [TYPED_PATH]}
                    )
                    assert asked.status_code == 202, asked.text
                    read = http.get(f"{findings}/{finding_id}/fixes/source")
                    assert read.status_code == 200 and read.json()["latest"]["state"] == "gathering"

                def verdict(finding_id: uuid.UUID, fix_id: uuid.UUID) -> None:
                    said = http.post(
                        f"{findings}/{finding_id}/fixes/{fix_id}/verdict",
                        json={"verdict": "useful_start"},
                    )
                    assert said.status_code == 200, said.text
                    assert said.json()["chosen"] == "useful_start"

                timed = [
                    Timed(
                        "Suggest a general fix, then its read",
                        [_time(partial(general, each)) for each in made["general"]],
                    ),
                    Timed(
                        "Ask for a suggested fix (typed path), then its read",
                        [_time(partial(ask, each)) for each in made["source"]],
                    ),
                    Timed(
                        "Was this suggestion useful?",
                        [_time(partial(verdict, *each)) for each in made["verdict"]],
                    ),
                ]
        finally:
            asyncio.run(_forget(org_id))

    print("\nNFR2 click timing, against the API at", origin)  # noqa: T201  the figure is the deliverable
    for each in timed:
        print(" ", each.line())  # noqa: T201
    assert all(len(each.seconds) == SAMPLES for each in timed)
