"""The batch read's answer: one state per panel, never one for the page (F20, FR14).

A model per state, so a `failed` panel cannot carry values and a drawing mistake
cannot paint zeros (BR5, AC20). Times are spelled as every telemetry screen spells them
(`wire.IsoTimestamp`). Notes and open findings are not here: the console draws them
from the definition and the panel context, so they survive any ClickHouse failure.
`series_changed` and `too_many_series` belong to metric series (plan Step 8).

**One `ok` shape for every kind**, since the state is the discriminator: a chart fills
`series`, a number `number`, a table `rows`, and a reliability target `budget` (F19's own
`SloBudget`), each row the model its own screen returns, so a table cell and the screen's
cell are one row-to-model step (BR3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from nexovue_api.dashboards.definition import MAX_PANELS, Definition, PanelId, ServiceName
from nexovue_api.dashboards.ranges import RangeId
from nexovue_api.dbstats.schemas import StatementCost
from nexovue_api.errorgroups.schemas import ErrorGroupSummary
from nexovue_api.metrics.schemas import MetricAttribute
from nexovue_api.rum.schemas import WebVitalSummary
from nexovue_api.servicemap.schemas import ServiceEdge
from nexovue_api.services.schemas import EndpointHealth, ServiceHealth
from nexovue_api.slos.schemas import SloBudget
from nexovue_api.wire import IsoTimestamp


class NodeName(BaseModel):
    """A top-nodes line's node, as the infrastructure screen names one."""

    host: str
    container: str
    reporter: str
    reporter_instance: str


class Series(BaseModel):
    """One line: a reading's name (`requests_per_minute`, `p95_ms`, or a node's metric
    name) and its points. A top-N chart's line also names its service, which the console
    draws as a link to that service (FR35)."""

    label: str
    service: str | None = None
    node: NodeName | None = None
    # A chosen metric series' attributes, its label at read time, shown as text (FR61).
    attributes: list[MetricAttribute] | None = None
    points: list[tuple[IsoTimestamp, float]]


class NumberReading(BaseModel):
    """A number panel's value, and the previous window's when it is compared (T5.3).

    `previous` is null unless the previous window is covered, from its start, by at least
    `COMPARE_MIN_COVERAGE`: a service ten minutes old has no earlier hour to compare
    with, and "Not enough history to compare" says so rather than an arrow (AC73).
    `previous_coverage` is the share of that window its data covers, null when nothing
    reported in it.
    """

    measure: str
    value: float
    previous: float | None = None
    previous_coverage: float | None = None


TableRow = (
    ServiceHealth
    | EndpointHealth
    | ServiceEdge
    | ErrorGroupSummary
    | StatementCost
    | WebVitalSummary
)


class OkPanel(BaseModel):
    state: Literal["ok"] = "ok"
    kind: str
    read_at: IsoTimestamp
    bucket_seconds: int
    # When the data starts, if later than the window's start (FR15, D13).
    data_starts_at: IsoTimestamp | None
    data_ends_at: IsoTimestamp | None
    # The newest bucket of a live window, still filling: a rate there is divided by the
    # whole bucket and would read as a collapse. The console leaves it off the line and
    # shows it as "so far" (FR66, AC114). Null on a fixed window, or when it is whole.
    incomplete_from: IsoTimestamp | None = None
    series: list[Series] = []
    number: NumberReading | None = None
    # A table's rows, already cut to the panel's own count, in the store's order.
    rows: list[TableRow] | None = None
    # A metric series' series combined into its one line (FR15, AC52), and every unit its
    # readings carried. More than one unit draws no line, since they cannot be added (AC51).
    series_combined: int | None = None
    units: list[str] | None = None
    # A reliability target's standing, from F19's Postgres-only reader (T10.2c).
    budget: SloBudget | None = None


class EmptyPanel(BaseModel):
    """Read, and nothing arrived in the window. A service's says when it was last seen."""

    state: Literal["empty"] = "empty"
    kind: str
    read_at: IsoTimestamp
    last_seen_at: IsoTimestamp | None = None


class NotInProjectPanel(BaseModel):
    """This project holds no record of the service, or no check or live reliability target
    by the panel's name, and the words claim only that (FR16, AC22)."""

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


class AmbiguousPanel(BaseModel):
    """Two checks in the displayed project share the panel's check name, so it cannot
    tell which to read, and reads neither (FR16, AC22)."""

    state: Literal["ambiguous"] = "ambiguous"
    kind: str


class SeriesChangedPanel(BaseModel):
    """The chosen series reported nothing in the window, and the metric still reports under
    other attributes: adding one attribute changes the hash, so "has not reported" would be
    false (FR16, AC115)."""

    state: Literal["series_changed"] = "series_changed"
    kind: str


class TooManySeriesPanel(BaseModel):
    """More series than one line can honestly combine; nothing is drawn (AC52)."""

    state: Literal["too_many_series"] = "too_many_series"
    kind: str
    limit: int


class WindowTooWidePanel(BaseModel):
    """A raw panel over a window wider than its reader may read; no scan ran (FR17)."""

    state: Literal["window_too_wide"] = "window_too_wide"
    kind: str
    limit_days: int


class PausedByOperatorPanel(BaseModel):
    """The operator turned dashboard scans off; no scan ran (FR69, AC116)."""

    state: Literal["paused_by_operator"] = "paused_by_operator"
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
    | AmbiguousPanel
    | SeriesChangedPanel
    | TooManySeriesPanel
    | WindowTooWidePanel
    | PausedByOperatorPanel
    | UnknownKindPanel,
    Field(discriminator="state"),
]


class PanelReadResponse(BaseModel):
    # So a 200 with failed panels can still be quoted to support (FR60).
    correlation_id: str | None
    window_start: IsoTimestamp
    window_end: IsoTimestamp
    panels: dict[str, PanelResult]


class PreviewBody(BaseModel):
    """A draft's read (FR10, FR24): what a saved read names in its address, and the draft
    itself, checked as a save is (FR7). `panels` names the one panel an Apply reads."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_id: uuid.UUID
    range_id: RangeId | None = Field(default=None, alias="range")
    from_time: datetime | None = Field(default=None, alias="from")
    to_time: datetime | None = Field(default=None, alias="to")
    service: ServiceName | None = None
    panels: Annotated[list[PanelId], Field(min_length=1, max_length=MAX_PANELS)] | None = None
    definition: Definition
