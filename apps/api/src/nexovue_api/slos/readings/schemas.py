"""What `/slo-readings/suggestion` sends: the suggestion and its arithmetic, as data, so
the form shows the working (FR7) rather than a bare number."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from nexovue_platform import slo
from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class CheckFacts:
    """A check target's check, from Postgres: what its interval expects."""

    id: uuid.UUID
    interval_seconds: int
    # Where it runs from: its own locations, or every location the deployment has.
    locations: int


class SuggestedWeek(BaseModel):
    """One 7-day window, newest first; `figure` is None with no events."""

    good: int
    total: int
    figure: Decimal | None


class CheckWindow(BaseModel):
    """What a check's interval expects over a window, and what running every minute
    would (FR8: "Run it every minute or choose 99.5%")."""

    window_days: int
    expected_runs: int
    fastest_expected_runs: int


class CheckAllowance(BaseModel):
    interval_seconds: int
    windows: list[CheckWindow]
    # FR16: slower than 2.4 hours a run, no tier can judge it.
    alerts_can_judge: bool


class EndpointChoice(BaseModel):
    name: str
    # Pre-ticked to leave out of a service availability target (FR34).
    health_check: bool


class SloSuggestion(BaseModel):
    outcome: slo.Suggested
    objective: Decimal | None
    weeks: list[SuggestedWeek]
    worst: Decimal | None
    typical: Decimal | None
    best: Decimal | None
    # Days since the first event in the last 28; under 7 offers the starter (FR8).
    days_of_data: int
    # A latency suggestion's boundary, each week's boundary holding 95%, and the highest
    # boundary a threshold may be (AC101).
    boundary_index: int | None
    boundary_ms: float | None
    week_boundaries_ms: list[float | None]
    highest_boundary_ms: float
    # Every boundary, lowest first, so the form previews what a typed threshold snaps
    # down to (FR3) from the platform's numbers rather than a copy of the formula.
    boundaries_ms: list[float]
    # The steps a suggestion rounds to, and the fewest failures a target may allow
    # before one moves it a lot, for "or choose 99.5%" (FR8, FR10).
    steps: list[Decimal]
    thin_allowed: int
    endpoints: list[EndpointChoice]
    # Too few requests: offer a check-based target where the project has a check (FR8).
    offer_check: bool
    check: CheckAllowance | None
