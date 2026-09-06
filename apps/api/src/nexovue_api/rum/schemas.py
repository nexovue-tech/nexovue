"""What the RUM endpoints put on the wire."""

from __future__ import annotations

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
