"""Close and open the ingest stop valve, from a terminal.

**This is the 3am path, and that is the whole design brief.** The valve is
`organizations.ingest_suspended_at` (P1-52, migration 0018): the receiver reads
it on the same row as the allowance and refuses every door while it is set. The
enforcement half is built and tested; this is the half a person uses.

Without it, closing the valve means finding the right organization id and
writing `UPDATE organizations SET …` by hand — at the moment one tenant is
hurting every other one, which is exactly when nobody should be typing a UUID
into a psql prompt.

**Deliberately not a screen and not an API.** Naming *who* closed a valve needs
an identity for platform operators, and Phase 1 is scoped single-operator (§9),
so an endpoint would have to invent an authorization model to protect it. This
runs where the DSN already is. The screen belongs with P1-76.

**A reason is required to close and impossible to record when open** — the CHECK
in 0018 enforces the second half, and this refuses the first rather than letting
the database do it, so the message names what is missing instead of quoting a
constraint. The first message after a suspension is the customer asking why.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres import postgres_dsn
from nexovue_api.data.postgres.models import Organization

EXIT_NO_SUCH_ORGANIZATION: Final = 2
EXIT_ALREADY_IN_THAT_STATE: Final = 3


async def close_valve(session: AsyncSession, slug: str, reason: str) -> str:
    """Suspends ingest, and refuses a second close rather than restamping it.

    Restamping would move the suspension's start time and overwrite the reason
    the customer is about to be told, which loses the only record of why.
    """
    organization = await _organization(session, slug)
    if organization.ingest_suspended_at is not None:
        raise _AlreadyInThatStateError(
            f"{slug} was already suspended at "
            f"{organization.ingest_suspended_at.isoformat()}: "
            f"{organization.ingest_suspended_reason}"
        )
    await session.execute(
        update(Organization)
        .where(Organization.id == organization.id)
        .values(ingest_suspended_at=datetime.now(UTC), ingest_suspended_reason=reason)
    )
    await session.commit()
    return f"{slug} suspended. Ingest is refused at every door within the receiver's cache TTL."


async def open_valve(session: AsyncSession, slug: str) -> str:
    """Resumes ingest. Clears the reason with it, because a reason outliving the
    suspension it explains is a note that will be read as current."""
    organization = await _organization(session, slug)
    if organization.ingest_suspended_at is None:
        raise _AlreadyInThatStateError(f"{slug} is not suspended")
    await session.execute(
        update(Organization)
        .where(Organization.id == organization.id)
        .values(ingest_suspended_at=None, ingest_suspended_reason=None)
    )
    await session.commit()
    return f"{slug} resumed. Data already refused while it was closed is not recoverable."


async def show_valves(session: AsyncSession) -> str:
    """Every closed valve, because "is anyone suspended right now" is the
    question asked before either of the others."""
    rows = (
        await session.execute(
            select(
                Organization.slug,
                Organization.ingest_suspended_at,
                Organization.ingest_suspended_reason,
            )
            .where(Organization.ingest_suspended_at.is_not(None))
            .order_by(Organization.ingest_suspended_at)
        )
    ).all()
    if not rows:
        return "No organization is suspended."
    return "\n".join(f"{slug}\t{at.isoformat()}\t{reason}" for slug, at, reason in rows)


class _AlreadyInThatStateError(RuntimeError):
    """The valve is already where the caller is asking to put it."""


async def _organization(session: AsyncSession, slug: str) -> Organization:
    organization = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if organization is None:
        raise _NoSuchOrganizationError(f"no organization with slug {slug!r}")
    return organization


class _NoSuchOrganizationError(RuntimeError):
    """The slug names nothing. A typo here would otherwise suspend nobody and
    report success."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nexovue_api.suspend",
        description="Close or open one organization's ingest stop valve (P1-52).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    close = commands.add_parser("close", help="suspend ingest for one organization")
    close.add_argument("slug")
    # Required, not optional-with-a-default: a default reason is a reason nobody
    # wrote, and it reads as fact to whoever answers the customer.
    close.add_argument("--reason", required=True, help="why, in your own words")

    commands.add_parser("open", help="resume ingest for one organization").add_argument("slug")
    commands.add_parser("show", help="list every suspended organization")
    return parser


async def _run(arguments: argparse.Namespace) -> str:
    # The engine is built here rather than taken from a dependency, for the
    # reason `engine.py` gives about alembic and the seed: this runs outside the
    # FastAPI app and has no request to hang a session on. Same shape as seed.py.
    engine = create_async_engine(postgres_dsn())
    try:
        async with AsyncSession(engine) as session:
            match arguments.command:
                case "close":
                    return await close_valve(session, arguments.slug, arguments.reason)
                case "open":
                    return await open_valve(session, arguments.slug)
                case _:
                    return await show_valves(session)
    finally:
        await engine.dispose()


def main() -> int:
    arguments = _parser().parse_args()
    try:
        print(asyncio.run(_run(arguments)))
    except _NoSuchOrganizationError as unknown:
        print(unknown, file=sys.stderr)
        return EXIT_NO_SUCH_ORGANIZATION
    except _AlreadyInThatStateError as unchanged:
        print(unchanged, file=sys.stderr)
        return EXIT_ALREADY_IN_THAT_STATE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
