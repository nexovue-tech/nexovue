"""`python -m nexovue_scheduler beat` or `... worker`: the scheduler's two roles.

    beat                                   schedule from Postgres while leader
    worker [--queues a,b] [--concurrency N]
    health                                 exit 0 if this container's process is alive

Either role answers /healthz, /readyz and /metrics on SCHEDULER_HEALTH_ADDR
(`health.py`); `health` is what the compose healthcheck runs, because the image
has no curl.

A worker with no `--queues` takes `light`, its own location's `light.<region>`
and `detection` — everything but `deliveries`, which has a worker of its own so a
slow receiver holds a delivery slot and never a probe's. That worker is
`scheduler-light`; it runs at most two detection jobs at once (`jobs/worker.py`).
`--queues` replaces the list, so naming `light` alone drops the region's probes.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import urllib.request
from typing import Final

from nexovue_platform.checks.task import SHARED_QUEUE, probe_queue
from nexovue_platform.telemetry.logging import configure_logging, get_logger
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import create_async_engine

from nexovue_scheduler import health, metrics
from nexovue_scheduler.beat import Beat
from nexovue_scheduler.detection import DETECTION_QUEUE
from nexovue_scheduler.jobs.worker import Worker
from nexovue_scheduler.leader import LeaderLock
from nexovue_scheduler.settings import SERVICE_NAME, Settings, get_settings

logger = get_logger(__name__)

DEFAULT_CONCURRENCY: Final = 8
HEALTH_CHECK_TIMEOUT_SECONDS: Final = 3


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nexovue_scheduler")
    roles = parser.add_subparsers(dest="role", required=True)
    roles.add_parser("beat")
    roles.add_parser("health")
    worker = roles.add_parser("worker")
    worker.add_argument(
        "--queues",
        default="",
        help="comma-separated; default light, light.<region> and detection",
    )
    worker.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    chosen = parser.parse_args(arguments)

    settings = get_settings()
    if chosen.role == "health":
        sys.exit(0 if _alive(settings.health_addr) else 1)
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        service=SERVICE_NAME,
        env=settings.env,
    )
    pulse = health.Pulse()
    metrics.LOOP_SILENT.set_function(pulse.silent_seconds)
    health.serve(settings.health_addr, pulse, REGISTRY)
    if chosen.role == "beat":
        asyncio.run(_beat(settings, pulse))
    else:
        queues = worker_queues(chosen.queues, region=settings.prober_region)
        asyncio.run(_worker(settings, queues, chosen.concurrency, pulse))


def worker_queues(named: str, *, region: str) -> list[str]:
    """The queues a worker claims from: those named, or the default."""
    return [name for name in named.split(",") if name] or [
        SHARED_QUEUE,
        probe_queue(region),
        DETECTION_QUEUE,
    ]


def _alive(address: str) -> bool:
    """Ask this container's own process, on the port it was configured with."""
    port = address.rpartition(":")[2]
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{health.LIVENESS_PATH}",
            timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
        ) as answer:
            return bool(answer.status == 200)
    except OSError:
        return False


async def _beat(settings: Settings, pulse: health.Pulse) -> None:
    engine = create_async_engine(
        str(settings.postgres_dsn),
        # Beat reads one small query on a timer and inserts what is due.
        pool_size=1,
        max_overflow=1,
        pool_pre_ping=True,
    )
    try:
        beat = Beat(engine, LeaderLock(_driver_dsn(settings)), settings, pulse=pulse)
        await beat.run(_stop_on_signal())
    finally:
        await engine.dispose()


async def _worker(
    settings: Settings, queues: list[str], concurrency: int, pulse: health.Pulse
) -> None:
    engine = create_async_engine(
        str(settings.postgres_dsn), pool_size=2, max_overflow=2, pool_pre_ping=True
    )
    try:
        worker = Worker(
            engine,
            listen_dsn=_driver_dsn(settings),
            queues=queues,
            concurrency=concurrency,
            pulse=pulse,
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
