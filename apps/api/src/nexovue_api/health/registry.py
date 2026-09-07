"""Running the readiness probes, each under its own deadline.

The deadline is enforced here rather than trusted to the client library. Drivers
differ on whether they honour a cancellation, and a probe that hangs must not
hang the endpoint an orchestrator uses to decide whether we are alive.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from nexovue_platform.telemetry.redaction import redact_credentials
from pydantic import BaseModel

from nexovue_api.buildinfo import BuildInfo, build_info

# Short on purpose: readiness must answer well inside an orchestrator's timeout.
DEFAULT_PROBE_TIMEOUT_SECONDS: Final = 2.0

STATUS_OK: Final = "ok"
STATUS_FAILED: Final = "failed"

MILLISECONDS_PER_SECOND: Final = 1000

Probe = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Check:
    """One named dependency probe and the deadline it must answer within."""

    name: str
    probe: Probe
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS


class CheckResult(BaseModel):
    """What one dependency reported."""

    status: str
    duration_ms: int
    error: str | None = None


class Report(BaseModel):
    """The body both probe endpoints return."""

    status: str
    build: BuildInfo
    # Absent on liveness: it probes nothing, and an empty map reads as
    # "probed and found nothing", which is a different claim.
    checks: dict[str, CheckResult] | None = None


class CheckRegistry:
    """The dependency checks readiness runs."""

    def __init__(self, checks: list[Check] | None = None) -> None:
        self._checks = list(checks or ())

    def add(self, check: Check) -> None:
        self._checks.append(check)

    async def run(self) -> dict[str, CheckResult]:
        """Probe every dependency at once, each on its own deadline."""
        results = await asyncio.gather(*(_run_check(check) for check in self._checks))
        return dict(zip((check.name for check in self._checks), results, strict=True))


async def _run_check(check: Check) -> CheckResult:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(check.timeout_seconds):
            await check.probe()
    except TimeoutError:
        return _failure(started, f"did not answer within {check.timeout_seconds}s")
    except Exception as error:
        return _failure(started, f"{type(error).__name__}: {error}")
    return CheckResult(status=STATUS_OK, duration_ms=_elapsed_ms(started))


def _failure(started: float, reason: str) -> CheckResult:
    # Driver errors quote the DSN often enough that redaction cannot be optional:
    # the readiness body is readable by anything that can reach the port.
    return CheckResult(
        status=STATUS_FAILED,
        duration_ms=_elapsed_ms(started),
        error=redact_credentials(reason),
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)


def liveness_report() -> Report:
    """Alive, and which build is answering. Nothing external is touched."""
    return Report(status=STATUS_OK, build=build_info())


def readiness_report(results: dict[str, CheckResult]) -> Report:
    """Ready only if every dependency is."""
    status = (
        STATUS_OK
        if all(result.status == STATUS_OK for result in results.values())
        else STATUS_FAILED
    )
    return Report(status=status, build=build_info(), checks=results)
