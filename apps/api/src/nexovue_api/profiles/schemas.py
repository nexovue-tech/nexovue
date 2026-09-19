"""What the Profiles screen reads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from nexovue_api.wire import IsoTimestamp


class ProfileType(BaseModel):
    """A profile type a service sent: what the values count, and in what unit."""

    sample_type: str
    unit: str


class ProfiledService(BaseModel):
    service_name: str
    types: list[ProfileType]
    last_received: IsoTimestamp


class ProfiledServicesResponse(BaseModel):
    services: list[ProfiledService]


class FlameNode(BaseModel):
    """One box. `total` is where this frame was on the stack; `self_value` where it
    was the running frame. `merged` > 0 marks a node standing for that many frames
    folded for size; `unsymbolized` a native frame the profiler could not name."""

    name: str
    file: str
    unsymbolized: bool
    total: int
    self_value: int
    merged: int
    # The source line, when every frame merged into this box was on the same one.
    line: int | None = None
    children: list[FlameNode]


class FunctionShare(BaseModel):
    name: str
    file: str
    unsymbolized: bool
    self_value: int
    total: int


class FlameGraphResponse(BaseModel):
    """A service's profile over a window, as one tree and one table.

    `total` is the whole every share is of, in `unit`; `samples` how many samples
    it holds — zero means there was nothing in the window, which the screen says
    rather than drawing an empty graph (spec BR6). `merged_frames` is how many
    frames were folded to keep the graph readable (NFR4).
    """

    sample_type: str
    unit: str
    total: int
    samples: int
    merged_frames: int
    root: FlameNode
    functions: list[FunctionShare]


class LatestProfileResponse(BaseModel):
    """The newest sample held — `None` when there is none within retention."""

    last_received: IsoTimestamp | None


class FilterValue(BaseModel):
    """One value a filter can take, how many samples carry it, and the newest of them."""

    value: str
    samples: int
    last_seen: IsoTimestamp


class ProfileFiltersResponse(BaseModel):
    """The hosts, versions and environments a selection's samples carry, busiest first.
    An empty string is a sample that did not say — shown as such, never dropped."""

    hosts: list[FilterValue]
    versions: list[FilterValue]
    environments: list[FilterValue]


class TimelinePoint(BaseModel):
    """One bucket. `value` is `None` where no sample arrived: nothing was measured,
    which is not the same claim as zero."""

    bucket: IsoTimestamp
    value: int | None
    samples: int


class ProfileTimelineResponse(BaseModel):
    unit: str
    bucket_seconds: int
    points: list[TimelinePoint]


SpanProfileState = Literal["linked", "no_samples", "not_profiled"]


class SpanProfileResponse(BaseModel):
    """What a span's Profile section says (spec FR10): its own samples exist; the
    service was profiled but none landed in this span; or it was not profiled."""

    state: SpanProfileState
    linked_samples: int
    types: list[str]


class DiffNode(BaseModel):
    """One box of a comparison. `weight` is its width in parts of `SHARE_SCALE`;
    the shares are of each side's own whole, from 0 to 1."""

    name: str
    file: str
    unsymbolized: bool
    merged: int
    weight: int
    baseline_share: float
    comparison_share: float
    children: list[DiffNode]


class FunctionChangeRow(BaseModel):
    name: str
    file: str
    unsymbolized: bool
    baseline_self: float
    comparison_self: float
    baseline_total: float
    comparison_total: float
    change: float


class CompareResponse(BaseModel):
    """Two profiles of one service and type, compared by share (spec FR9).

    A side's `samples` of zero means that side held nothing; the screen says so
    rather than drawing everything as new or gone.
    """

    sample_type: str
    unit: str
    baseline_samples: int
    comparison_samples: int
    merged_frames: int
    root: DiffNode
    functions: list[FunctionChangeRow]
