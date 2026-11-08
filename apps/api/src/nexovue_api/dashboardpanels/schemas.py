"""The batch read's answer: one state per panel, never one for the page (F20, FR14).

A model per state, so a `failed` panel cannot carry values and a drawing mistake
cannot paint zeros (BR5, AC20). Times are spelled as every telemetry screen spells them
(`wire.IsoTimestamp`). Notes are not here: the console draws them from the
definition, so they survive any ClickHouse failure. The states that later kinds need
(`ambiguous`, `series_changed`, `window_too_wide`, `paused_by_operator` and the rest)
arrive with those kinds.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp


class Series(BaseModel):
    """One line: a reading's name (`requests_per_minute`, `p95_ms`) and its points."""

    label: str
    points: list[tuple[IsoTimestamp, float]]


class OkPanel(BaseModel):
    state: Literal["ok"] = "ok"
    kind: str
    read_at: IsoTimestamp
    bucket_seconds: int
    # When the data starts, if later than the window's start (FR15, D13).
    data_starts_at: IsoTimestamp | None
    data_ends_at: IsoTimestamp | None
    series: list[Series]


class EmptyPanel(BaseModel):
    """Read, and nothing arrived in the window. A service's says when it was last seen."""

    state: Literal["empty"] = "empty"
    kind: str
    read_at: IsoTimestamp
    last_seen_at: IsoTimestamp | None = None


class NotInProjectPanel(BaseModel):
    """This project holds no record of the service, and the words claim only that (FR16)."""

    state: Literal["not_in_project"] = "not_in_project"
    kind: str


class NeedsServicePanel(BaseModel):
    """The panel follows the picker, and no service is picked. No scan ran (AC140)."""

    state: Literal["needs_service"] = "needs_service"
    kind: str


class OverLimitPanel(BaseModel):
    """Past the scan cap, so not read. A retry would not help (AC79)."""

    state: Literal["over_limit"] = "over_limit"
    kind: str


class RefusedPanel(BaseModel):
    """A real quota or cap refusal, with a real wait. Never used for a slow read."""

    state: Literal["refused"] = "refused"
    kind: str
    retry_after_seconds: int


class TimedOutPanel(BaseModel):
    state: Literal["timed_out"] = "timed_out"
    kind: str


class FailedPanel(BaseModel):
    state: Literal["failed"] = "failed"
    kind: str


class UnknownKindPanel(BaseModel):
    """A stored panel this code no longer knows; the rest of the page draws (FR8)."""

    state: Literal["unknown_kind"] = "unknown_kind"
    kind: Literal["unknown_kind"] = "unknown_kind"


PanelResult = Annotated[
    OkPanel
    | EmptyPanel
    | NotInProjectPanel
    | NeedsServicePanel
    | OverLimitPanel
    | RefusedPanel
    | TimedOutPanel
    | FailedPanel
    | UnknownKindPanel,
    Field(discriminator="state"),
]


class PanelReadResponse(BaseModel):
    # So a 200 with failed panels can still be quoted to support (FR60).
    correlation_id: str | None
    window_start: IsoTimestamp
    window_end: IsoTimestamp
    panels: dict[str, PanelResult]
