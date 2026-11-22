"""Seed one case at D65's worst shape and time its timeline read (F22 plan T8.4; spec NFR3, D65).

The timeline is one Postgres read of a case's root and every finding joined to it, newest first,
at most 100 rows (`nexovue_api.findings.queries.timeline`). Its cost grows with joined findings,
which D65 accepted on condition of a measurement: over 200 ms at the worst shape (a guess) means
storing the root on each event instead.

**The worst shape** (`WORST`): a case kept alive for 90 days (BR13's limit) by a person's act,
on a service whose problem returns every 4 hours and joins each time (540 joined findings, each
with its opening, join, closing and one raised severity); the 500 notes a case may hold (D33), each
of 4,000 characters; and around it, the same organization's 2,000 other findings and two other
organizations of the same size, so the read has neighbours to skip. A real case is far smaller:
flapping writes one event per spell, and closed joined findings age out with retention.

    uv run python tools/detection-seed/timeline_seed.py seed      # writes it, prints the case
    uv run python tools/detection-seed/timeline_seed.py measure   # times the real read
    uv run python tools/detection-seed/timeline_seed.py remove    # deletes what it wrote

Dev stack only: it refuses any Postgres but this machine's, as `seed.py` does. Every row belongs
to organizations whose slug starts `timeline-seed-`, which `remove` deletes, cascading.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from typing import ClassVar, Final

from seed import require_local
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

DEFAULT_POSTGRES: Final = os.environ.get(
    "POSTGRES_DSN", "postgresql+asyncpg://nexovue:nexovue_dev@localhost:15432/nexovue"
)
SLUG_PREFIX: Final = "timeline-seed-"
SERVICE: Final = "checkout"
# Warm-up reads, then timed reads; the page read is the console's (100 rows).
WARM_UP_READS: Final = 5
TIMED_READS: Final = 50
GUESS_MS: Final = 200.0


@dataclass(frozen=True)
class Shape:
    """How many rows of each kind one seeded organization holds."""

    # The root's own: opened, case started, taken, two hand-overs, a link, its issue's creation
    # and close, a reopen, and one raised severity.
    ROOT_EVENTS: ClassVar[int] = 10

    joined_findings: int
    events_per_joined: int
    notes: int
    background_findings: int
    events_per_background: int
    note_characters: int
    other_organizations: int

    @property
    def case_events(self) -> int:
        return self.joined_findings * self.events_per_joined + self.notes + self.ROOT_EVENTS

    @property
    def organization_events(self) -> int:
        return self.case_events + self.background_findings * self.events_per_background

    @property
    def all_events(self) -> int:
        return self.organization_events + (
            self.other_organizations * self.background_findings * self.events_per_background
        )


WORST: Final = Shape(
    joined_findings=90 * 24 // 4,
    events_per_joined=4,
    notes=500,
    background_findings=2000,
    events_per_background=25,
    note_characters=4000,
    other_organizations=2,
)

_ORGANIZATION: Final = text(
    "INSERT INTO organizations (slug, name) VALUES (:slug, 'Timeline seed') RETURNING id"
)
_PERSON: Final = text(
    "INSERT INTO users (org_id, email, name, role, password_hash) "
    "VALUES (:org, :email, 'Seeded owner', 'owner', 'x') RETURNING id"
)
_PROJECT: Final = text(
    "INSERT INTO projects (org_id, slug, name) VALUES (:org, 'production', 'Production') "
    "RETURNING id"
)
# Background: closed findings on other services, each with its events, spread over 90 days.
_BACKGROUND: Final = text(
    """
    WITH made AS (
        INSERT INTO findings (org_id, project_id, scope, service_name, severity, state,
                              closed_reason, title, signals, started_at, closed_at)
        SELECT :org, :project, 'service', 'service-' || (n % 50), 'warning', 'closed',
               'recovered', 'seeded', '[]', now() - make_interval(hours => n),
               now() - make_interval(hours => n) + interval '30 minutes'
        FROM generate_series(1, :findings) AS n
        RETURNING id, started_at
    )
    INSERT INTO finding_events (org_id, finding_id, kind, actor_kind, detail, at)
    SELECT :org, made.id, 'signal_joined', 'system', '{}', made.started_at
                                                          + make_interval(secs => e)
    FROM made, generate_series(1, :events) AS e
    """
)
_ROOT: Final = text(
    """
    INSERT INTO findings (org_id, project_id, scope, service_name, severity, state, title,
                          signals, started_at, case_state, case_opened_at, case_activity_at,
                          assignee_id)
    VALUES (:org, :project, 'service', :service, 'critical', 'open', 'seeded root', '[]',
            now() - interval '90 days', 'investigating', now() - interval '90 days', now(),
            :person)
    RETURNING id
    """
)
_ROOT_EVENTS: Final = text(
    """
    INSERT INTO finding_events (org_id, finding_id, kind, actor_kind, actor_id, detail, at)
    SELECT :org, :root, kind, 'person', :person, '{}',
           now() - interval '90 days' + make_interval(mins => CAST(position AS integer))
    FROM unnest(CAST(:kinds AS text[])) WITH ORDINALITY AS listed(kind, position)
    """
)
_JOINED: Final = text(
    """
    WITH made AS (
        INSERT INTO findings (org_id, project_id, scope, service_name, severity, state,
                              closed_reason, title, signals, started_at, closed_at,
                              root_finding_id)
        SELECT :org, :project, 'service', :service, 'warning', 'closed', 'recovered',
               'seeded joined', '[]',
               now() - interval '90 days' + make_interval(hours => 4 * n),
               now() - interval '90 days' + make_interval(hours => 4 * n) + interval '1 hour',
               :root
        FROM generate_series(1, :findings) AS n
        RETURNING id, started_at
    )
    INSERT INTO finding_events (org_id, finding_id, kind, actor_kind, detail, at)
    SELECT :org, made.id, kinds.kind, 'system', '{}',
           made.started_at + make_interval(mins => CAST(kinds.position AS integer))
    FROM made, unnest(CAST(:kinds AS text[])) WITH ORDINALITY AS kinds(kind, position)
    """
)
_NOTES: Final = text(
    """
    WITH written AS (
        INSERT INTO finding_notes (org_id, finding_id, author_id, body, created_at)
        -- Hex from a hash per line, so the text does not compress away as one letter would.
        SELECT :org, :root, :person,
               left((SELECT string_agg(md5(n::text || ':' || line), '')
                     FROM generate_series(1, :characters / 32 + 1) AS line), :characters),
               now() - interval '90 days' + make_interval(hours => 4 * n, mins => 30)
        FROM generate_series(1, :notes) AS n
        RETURNING id, created_at
    )
    INSERT INTO finding_events (org_id, finding_id, kind, actor_kind, actor_id, detail, at)
    SELECT :org, :root, 'note_added', 'person', :person, jsonb_build_object('note_id', id),
           created_at
    FROM written
    """
)
_ROOT_KINDS: Final = (
    "finding_opened",
    "case_started",
    "assigned",
    "assigned",
    "assigned",
    "link_added",
    "issue_created",
    "issue_closed",
    "case_reopened",
    "severity_raised",
)
_JOINED_KINDS: Final = (
    "finding_opened",
    "finding_joined_case",
    "severity_raised",
    "finding_closed",
)
_SEEDED_ROOT: Final = text(
    "SELECT f.org_id, f.id FROM findings AS f JOIN organizations AS o ON o.id = f.org_id "
    "WHERE o.slug LIKE :prefix AND f.case_state = 'investigating' ORDER BY o.created_at LIMIT 1"
)
_REMOVE: Final = text("DELETE FROM organizations WHERE slug LIKE :prefix")


async def _organization(
    session: AsyncSession, shape: Shape
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    org = (
        await session.execute(_ORGANIZATION, {"slug": f"{SLUG_PREFIX}{uuid.uuid4().hex[:12]}"})
    ).scalar_one()
    person = (
        await session.execute(_PERSON, {"org": org, "email": f"seed-{org}@example.test"})
    ).scalar_one()
    project = (await session.execute(_PROJECT, {"org": org})).scalar_one()
    await session.execute(
        _BACKGROUND,
        {
            "org": org,
            "project": project,
            "findings": shape.background_findings,
            "events": shape.events_per_background,
        },
    )
    return org, person, project


async def seed(engine: AsyncEngine, shape: Shape) -> tuple[uuid.UUID, uuid.UUID]:
    """The neighbours first, then the case; returns its organization and root."""
    async with AsyncSession(engine) as session, session.begin():
        for _ in range(shape.other_organizations):
            await _organization(session, shape)
        org, person, project = await _organization(session, shape)
        root = (
            await session.execute(
                _ROOT, {"org": org, "project": project, "service": SERVICE, "person": person}
            )
        ).scalar_one()
        await session.execute(
            _ROOT_EVENTS, {"org": org, "root": root, "person": person, "kinds": list(_ROOT_KINDS)}
        )
        await session.execute(
            _JOINED,
            {
                "org": org,
                "project": project,
                "service": SERVICE,
                "root": root,
                "findings": shape.joined_findings,
                "kinds": list(_JOINED_KINDS),
            },
        )
        await session.execute(
            _NOTES,
            {
                "org": org,
                "root": root,
                "person": person,
                "notes": shape.notes,
                "characters": shape.note_characters,
            },
        )
    async with engine.connect() as connection:
        # As autovacuum would have by the time a case is this old.
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("ANALYZE findings, finding_events, finding_notes"))
    return org, root


async def measure(engine: AsyncEngine) -> dict[str, float]:
    """The real read, as the route calls it: the first page, and a page of the oldest rows
    (**Show earlier** at the case's start). Milliseconds, each over `TIMED_READS` reads."""
    from nexovue_api.findings import queries

    async with engine.connect() as connection:
        found = (
            await connection.execute(_SEEDED_ROOT, {"prefix": f"{SLUG_PREFIX}%"})
        ).one_or_none()
    if found is None:
        raise SystemExit("nothing seeded: run `seed` first")
    org, root = found
    results: dict[str, float] = {}
    async with AsyncSession(engine) as session:
        first = await queries.timeline(session, org_id=org, root_id=root, before=None)
        oldest = await queries.timeline(
            session, org_id=org, root_id=root, before=None, limit=10_000
        )
        cursor = (oldest[-100][0].at, oldest[-100][0].id) if len(oldest) > 100 else None
        results["matching_rows"] = float(len(oldest))
        results["first_page_rows"] = float(len(first))
        for name, before in (("first_page", None), ("earliest_page", cursor)):
            timings = []
            for attempt in range(WARM_UP_READS + TIMED_READS):
                started = time.perf_counter()
                await queries.timeline(session, org_id=org, root_id=root, before=before)
                elapsed = (time.perf_counter() - started) * 1000
                await session.rollback()
                if attempt >= WARM_UP_READS:
                    timings.append(elapsed)
            timings.sort()
            results[f"{name}_median_ms"] = statistics.median(timings)
            results[f"{name}_p95_ms"] = timings[int(0.95 * len(timings)) - 1]
            results[f"{name}_max_ms"] = timings[-1]
    return results


async def remove(engine: AsyncEngine) -> int:
    async with engine.begin() as connection:
        return (await connection.execute(_REMOVE, {"prefix": f"{SLUG_PREFIX}%"})).rowcount


async def _main(arguments: argparse.Namespace) -> None:
    require_local(arguments.postgres, "Postgres")
    engine = create_async_engine(arguments.postgres)
    try:
        if arguments.command == "seed":
            org, root = await seed(engine, WORST)
            print(
                f"seeded case {root} in organization {org}: {WORST.case_events} case events, "
                f"{WORST.all_events} events in all"
            )
        elif arguments.command == "measure":
            for name, value in (await measure(engine)).items():
                print(f"{name}: {value:.2f}")
            print(f"guess: {GUESS_MS:.0f} ms (D65)")
        else:
            print(f"removed {await remove(engine)} seeded organizations")
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("seed", "measure", "remove"))
    parser.add_argument("--postgres", default=DEFAULT_POSTGRES)
    asyncio.run(_main(parser.parse_args(argv if argv is not None else sys.argv[1:])))


if __name__ == "__main__":
    main()
