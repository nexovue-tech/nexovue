"""What one install lets a dashboard do, and the check that keeps it within the quota (F20).

The values are the design's *guesses* until plan T5.7 measures them (design,
*Performance and scale*). The hosted service keeps them: its limits are product policy
(`ratelimit/policy.py`). A self-hosted operator may tune five through settings (T5.6c):
the panel cap, the scan cap, the refresh floor, the raw readers' windows and scans at
once. **Startup refuses a set that breaks the quota's arithmetic** (NFR3): a dashboard at
the scan cap refreshing at the floor must fit one person's share of the telemetry quota
at the largest supported number of API processes, or its own lower panels would be
refused every refresh.

A panel cap may only be lowered: the validator's 30 is the ceiling a stored definition
was checked against (`definition.MAX_PANELS`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from nexovue_api.dashboards.compile import CompiledDefinition, compile_for_save
from nexovue_api.dashboards.definition import MAX_PANELS, Definition
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.ratelimit.policy import (
    LARGEST_SUPPORTED_API_INSTANCES,
    MAX_SCANS_PER_READ,
    REFRESH_FLOOR_SECONDS,
    TELEMETRY_TIER,
)
from nexovue_api.settings import Settings

SECONDS_PER_MINUTE: Final = 60
# One batch's scans at once, below the process's 8 ClickHouse connections.
SCANS_AT_ONCE: Final = 4
# The widest window a raw reader reads (FR17): one node's or one check's line is one
# series, so it reads a month; a ranking or a statement reads a week.
SINGLE_SERIES_RAW_WINDOW_DAYS: Final = 30
RAW_WINDOW_DAYS: Final = 7


class DashboardLimitsError(ValueError):
    """A tuned set the install must not start with, named in words an operator can act on."""


@dataclass(frozen=True, slots=True)
class DashboardLimits:
    scans_enabled: bool = True
    max_panels: int = MAX_PANELS
    max_scans_per_read: int = MAX_SCANS_PER_READ
    refresh_floor_seconds: int = REFRESH_FLOOR_SECONDS
    scans_at_once: int = SCANS_AT_ONCE
    single_series_raw_window_days: int = SINGLE_SERIES_RAW_WINDOW_DAYS
    raw_window_days: int = RAW_WINDOW_DAYS

    @property
    def scans_a_minute_at_the_floor(self) -> float:
        return self.max_scans_per_read * SECONDS_PER_MINUTE / self.refresh_floor_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> DashboardLimits:
        """The install's limits, or a refusal at start (T5.6c)."""
        tuned = {
            "DASHBOARD_MAX_PANELS": settings.dashboard_max_panels,
            "DASHBOARD_MAX_SCANS_PER_READ": settings.dashboard_max_scans_per_read,
            "DASHBOARD_REFRESH_FLOOR_SECONDS": settings.dashboard_refresh_floor_seconds,
            "DASHBOARD_SCANS_AT_ONCE": settings.dashboard_scans_at_once,
            "DASHBOARD_SINGLE_SERIES_RAW_WINDOW_DAYS": (
                settings.dashboard_single_series_raw_window_days
            ),
            "DASHBOARD_RAW_WINDOW_DAYS": settings.dashboard_raw_window_days,
        }
        named = sorted(name for name, value in tuned.items() if value is not None)
        if settings.is_hosted and named:
            raise DashboardLimitsError(
                f"{', '.join(named)} may be set on a self-hosted install only: the hosted "
                "service's dashboard limits are product policy. Remove them."
            )
        defaults = cls()
        limits = cls(
            scans_enabled=settings.dashboard_scans_enabled,
            max_panels=_or(settings.dashboard_max_panels, defaults.max_panels),
            max_scans_per_read=_or(
                settings.dashboard_max_scans_per_read, defaults.max_scans_per_read
            ),
            refresh_floor_seconds=_or(
                settings.dashboard_refresh_floor_seconds, defaults.refresh_floor_seconds
            ),
            scans_at_once=_or(settings.dashboard_scans_at_once, defaults.scans_at_once),
            single_series_raw_window_days=_or(
                settings.dashboard_single_series_raw_window_days,
                defaults.single_series_raw_window_days,
            ),
            raw_window_days=_or(settings.dashboard_raw_window_days, defaults.raw_window_days),
        )
        limits.check(connections=settings.clickhouse_pool_size)
        return limits

    def check(self, *, connections: int) -> None:
        if self.max_panels > MAX_PANELS:
            raise DashboardLimitsError(
                f"DASHBOARD_MAX_PANELS is {self.max_panels}; a dashboard holds at most "
                f"{MAX_PANELS} panels. Lower it."
            )
        share = TELEMETRY_TIER.quota.limit // LARGEST_SUPPORTED_API_INSTANCES
        if self.scans_a_minute_at_the_floor > share:
            raise DashboardLimitsError(
                f"A dashboard of {self.max_scans_per_read} reads refreshing every "
                f"{self.refresh_floor_seconds} s would spend "
                f"{self.scans_a_minute_at_the_floor:g} reads a minute, and one person may "
                f"spend {share}. Lower DASHBOARD_MAX_SCANS_PER_READ or raise "
                "DASHBOARD_REFRESH_FLOOR_SECONDS."
            )
        if self.scans_at_once > connections:
            raise DashboardLimitsError(
                f"DASHBOARD_SCANS_AT_ONCE is {self.scans_at_once}, above the API's "
                f"{connections} ClickHouse connections (CLICKHOUSE_POOL_SIZE). Lower it."
            )

    def refreshes_too_often(self, refresh_seconds: int | None) -> bool:
        """Whether a saved refresh is faster than this install's floor (BR15)."""
        return refresh_seconds is not None and refresh_seconds < self.refresh_floor_seconds


# The code's own values: the hosted service's, and a self-hosted install's untouched.
CODE_LIMITS: Final = DashboardLimits()


def compile_within(definition: Definition, limits: DashboardLimits) -> CompiledDefinition:
    """A save's, a draft's and a file's one check against this install's limits (FR7)."""
    if len(definition.panels) > limits.max_panels:
        raise InvalidRequestError(
            f"A dashboard holds {limits.max_panels} panels here, because each one reads the "
            f"database; this one has {len(definition.panels)}. Split it into two.",
            panels=len(definition.panels),
            max_panels=limits.max_panels,
        )
    if limits.refreshes_too_often(definition.refresh_seconds):
        raise InvalidRequestError(
            f"A dashboard refreshes at most every {limits.refresh_floor_seconds} seconds here.",
            refresh_floor_seconds=limits.refresh_floor_seconds,
        )
    return compile_for_save(definition, max_scans=limits.max_scans_per_read)


def _or(value: int | None, default: int) -> int:
    return default if value is None else value
