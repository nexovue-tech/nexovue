"""`python -m nexovue_scheduler beat` or `... worker`: the scheduler's two roles.

    beat                                   schedule from Postgres while leader
    worker [--queues a,b] [--concurrency N]

A worker with no `--queues` takes `light` and its own location's `light.<region>`
— everything but `deliveries`, which has a worker of its own so a slow receiver
holds a delivery slot and never a probe's.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
from typing import Final

from nexovue_platform.checks.task import SHARED_QUEUE, probe_queue
from nexovue_platform.telemetry.logging import configure_logging, get_logger
from sqlalchemy.ext.asyncio import create_async_engine

from nexovue_scheduler.beat import Beat
from nexovue_scheduler.jobs.worker import Worker
from nexovue_scheduler.leader import LeaderLock
from nexovue_scheduler.settings import SERVICE_NAME, Settings, get_settings

logger = get_logger(__name__)

DEFAULT_CONCURRENCY: Final = 8


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nexovue_scheduler")
    roles = parser.add_subparsers(dest="role", required=True)
    roles.add_parser("beat")
    worker = roles.add_parser("worker")
    worker.add_argument(
        "--queues", default="", help="comma-separated; default light and light.<region>"
    )
    worker.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    chosen = parser.parse_args(arguments)

    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        service=SERVICE_NAME,
        env=settings.env,
    )
    if chosen.role == "beat":
        asyncio.run(_beat(settings))
    else:
        queues = [name for name in chosen.queues.split(",") if name] or [
            SHARED_QUEUE,
            probe_queue(settings.prober_region),
        ]
        asyncio.run(_worker(settings, queues, chosen.concurrency))


async def _beat(settings: Settings) -> None:
    engine = create_async_engine(
        str(settings.postgres_dsn),
        # Beat reads one small query on a timer and inserts what is due.
        pool_size=1,
        max_overflow=1,
        pool_pre_ping=True,
    )
    try:
        await Beat(engine, LeaderLock(_driver_dsn(settings)), settings).run(_stop_on_signal())
    finally:
        await engine.dispose()


async def _worker(settings: Settings, queues: list[str], concurrency: int) -> None:
    engine = create_async_engine(
        str(settings.postgres_dsn), pool_size=2, max_overflow=2, pool_pre_ping=True
    )
    try:
        worker = Worker(
            engine, listen_dsn=_driver_dsn(settings), queues=queues, concurrency=concurrency
        )
        await worker.run(_stop_on_signal())
    finally:
        await engine.dispose()


def _stop_on_signal() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(received, stop.set)
    return stop


def _driver_dsn(settings: Settings) -> str:
    """The DSN asyncpg reads itself, without SQLAlchemy's driver suffix."""
    dsn = str(settings.postgres_dsn)
    _, separator, rest = dsn.partition("://")
    return f"postgresql://{rest}" if separator else dsn


if __name__ == "__main__":
    main()
