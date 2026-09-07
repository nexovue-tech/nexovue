"""What the RUM endpoints put on the wire."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class WebVitalSummary(BaseModel):
    """One Core Web Vital over the requested window."""

    # cls | fcp | inp | lcp | ttfb, as the browser SDK spells them.
    name: str
    # Milliseconds for fcp, inp, lcp and ttfb; a unitless ratio for cls. The
    # SDK passes each value through as the browser reported it.
    p75: float
    # What the percentile was computed from. A p75 of four readings is not a
    # measurement, and a screen showing one has to be able to say so.
    readings: int


class WebVitalsResponse(BaseModel):
    # Only the vitals with at least one reading in the window. A vital is
    # absent, not zero: CLS is never reported at all outside Chromium.
    vitals: list[WebVitalSummary] = Field(default_factory=list)


class BreakdownDimension(StrEnum):
    """What a breakdown splits the readings by.

    Two members, not three. `web_vitals.country` is a promoted column with no
    producer — there is no GeoIP anywhere in the system — so a `GEO` member
    would name a question the store cannot answer.
    """

    PAGE = "page"
    DEVICE = "device"


class WebVitalGroup(BaseModel):
    """One page or one device class, with the vitals measured inside it."""

    # The `url_path` or the `device_class` these readings share. Empty when the
    # browser reported none; that is a real group, not a missing one.
    key: str
    # Same shape as the unsplit summary, so one reader handles both. Each
    # carries its own `readings`, which is what decides whether its p75 means
    # anything — a split makes every count smaller than the screen's headline.
    vitals: list[WebVitalSummary] = Field(default_factory=list)


class WebVitalBreakdownResponse(BaseModel):
    dimension: BreakdownDimension
    # Busiest first, so a limit cuts the long tail rather than the answer.
    groups: list[WebVitalGroup] = Field(default_factory=list)
    # More groups exist than were returned. Reported rather than silently cut:
    # `url_path` is stored exactly as the browser sent it, ids and all, so a
    # site with an id in its paths has one group per order.
    truncated: bool = False
