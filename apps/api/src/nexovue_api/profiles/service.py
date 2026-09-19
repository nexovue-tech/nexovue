"""Profiled services, flame graphs and comparisons, read through the tenant-bound
repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any, Final

from nexovue_api import rollup
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.profiles import queries, tree
from nexovue_api.profiles.schemas import (
    CompareResponse,
    DiffNode,
    FilterValue,
    FlameGraphResponse,
    FlameNode,
    FunctionChangeRow,
    FunctionShare,
    LatestProfileResponse,
    ProfiledService,
    ProfiledServicesResponse,
    ProfileFiltersResponse,
    ProfileTimelineResponse,
    ProfileType,
    SpanProfileResponse,
    SpanProfileState,
    TimelinePoint,
)
from nexovue_api.window import receive_window

# How many rows of the functions table one response carries.
MAX_FUNCTIONS: Final = 500

# How many values one filter menu offers.
MAX_FILTER_VALUES: Final = 100

# The longest a profile is kept (spec D2): how far back "the newest profile" looks.
PROFILE_RETENTION: Final = timedelta(days=14)

# Around a span, how far the service's own samples are looked for when deciding
# whether it was profiled at all. A span is milliseconds long; a profiler reports
# every few seconds, so the span itself is too narrow a window to judge that by.
SPAN_NEIGHBOURHOOD: Final = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Selection:
    """One service's profile type, narrowed as the reader asked."""

    service: str
    sample_type: str
    host: str | None = None
    version: str | None = None
    environment: str | None = None
    trace_id: str | None = None
    span_id: str | None = None

    def narrowing(self) -> dict[str, bool]:
        return {
            "host": self.host is not None,
            "version": self.version is not None,
            "environment": self.environment is not None,
            "span": self.trace_id is not None and self.span_id is not None,
        }

    def parameters(self) -> dict[str, Any]:
        bound: dict[str, Any] = {"service": self.service, "sample_type": self.sample_type}
        optional = {
            "host": self.host,
            "version": self.version,
            "environment": self.environment,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }
        bound |= {name: value for name, value in optional.items() if value is not None}
        return bound


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime


async def profiled_services(
    repository: TenantScopedRepository, *, project_id: str, window: Window
) -> ProfiledServicesResponse:
    rows = await repository.query(queries.SERVICES_SQL, _scope(project_id, window))
    return ProfiledServicesResponse(
        services=[
            ProfiledService(
                service_name=row["service_name"],
                types=[
                    ProfileType(sample_type=kind, unit=unit) for kind, unit in sorted(row["types"])
                ],
                last_received=_utc(row["last_received"]),
            )
            for row in rows
        ]
    )


async def latest_profile(
    repository: TenantScopedRepository, *, project_id: str, service: str | None
) -> LatestProfileResponse:
    """The newest sample within retention — where the screen points an empty window."""
    now = datetime.now(UTC)
    parameters = _scope(project_id, Window(start=now - PROFILE_RETENTION, end=now))
    if service is not None:
        parameters["service"] = service
    row = await repository.query_one(
        queries.narrowed(queries.LATEST_SQL, service=service is not None), parameters
    )
    found = bool(row and int(row.get("samples") or 0) > 0)
    return LatestProfileResponse(
        last_received=_utc(row["last_received"]) if row and found else None
    )


async def profile_filters(
    repository: TenantScopedRepository, *, project_id: str, selection: Selection, window: Window
) -> ProfileFiltersResponse:
    """The values each filter can take, from the rows the selection covers."""
    rows = await repository.query(
        queries.FILTERS_SQL,
        _scope(project_id, window)
        | {"service": selection.service, "sample_type": selection.sample_type},
    )
    seen: dict[str, dict[str, _Seen]] = {
        column: {} for column in ("host_name", "service_version", "environment")
    }
    for row in rows:
        for column, found in seen.items():
            value = str(row[column])
            before = found.get(value)
            last_seen = _utc(row["last_seen"])
            found[value] = _Seen(
                samples=(before.samples if before else 0) + int(row["samples"]),
                last_seen=max(before.last_seen, last_seen) if before else last_seen,
            )
    return ProfileFiltersResponse(
        hosts=_values(seen["host_name"]),
        versions=_values(seen["service_version"]),
        environments=_values(seen["environment"]),
    )


async def profile_timeline(
    repository: TenantScopedRepository, *, project_id: str, selection: Selection, window: Window
) -> ProfileTimelineResponse:
    """The selection's value per bucket, every bucket present; `None` where nothing arrived."""
    bucket_seconds = rollup.trend_bucket_seconds(window.start, window.end)
    rows = await repository.query(
        queries.narrowed(queries.TIMELINE_SQL, **selection.narrowing()),
        _scope(project_id, window) | selection.parameters() | {"bucket_seconds": bucket_seconds},
    )
    found = {int(row["bucket_epoch"]): row for row in rows}
    return ProfileTimelineResponse(
        unit=str(rows[0]["unit"]) if rows else "",
        bucket_seconds=bucket_seconds,
        points=[
            TimelinePoint(
                bucket=datetime.fromtimestamp(epoch, tz=UTC),
                value=int(found[epoch]["value"]) if epoch in found else None,
                samples=int(found[epoch]["samples"]) if epoch in found else 0,
            )
            for epoch in _bucket_starts(window, bucket_seconds)
        ],
    )


async def flame_graph(
    repository: TenantScopedRepository, *, project_id: str, selection: Selection, window: Window
) -> FlameGraphResponse:
    """One selection's profile over the window, as a pruned tree and a functions table."""
    side, whole = await _side(repository, project_id=project_id, selection=selection, window=window)
    root = tree.build(side.stacks, side.grand_total)
    merged = tree.prune(root)
    return FlameGraphResponse(
        sample_type=selection.sample_type,
        unit=whole.unit,
        total=root.total,
        samples=whole.samples,
        merged_frames=merged,
        root=_node(root),
        functions=[
            FunctionShare(
                name=row.name,
                file=row.file,
                unsymbolized=row.unsymbolized,
                self_value=row.self_value,
                total=row.total,
            )
            for row in tree.functions(side.stacks)[:MAX_FUNCTIONS]
        ],
    )


async def span_profile(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    service: str,
    trace_id: str,
    span_id: str,
    span_window: Window,
) -> SpanProfileResponse:
    """Whether a span has samples of its own, and if not, whether its service was
    profiled at all around it — two different answers a reader acts on differently."""
    neighbourhood = Window(
        start=span_window.start - SPAN_NEIGHBOURHOOD, end=span_window.end + SPAN_NEIGHBOURHOOD
    )
    row = await repository.query_one(
        queries.SPAN_SQL,
        _scope(project_id, neighbourhood)
        | {"service": service, "trace_id": trace_id, "span_id": span_id},
    )
    samples = int((row or {}).get("samples") or 0)
    linked = int((row or {}).get("linked") or 0)
    state: SpanProfileState
    if linked > 0:
        state = "linked"
    elif samples > 0:
        state = "no_samples"
    else:
        state = "not_profiled"
    return SpanProfileResponse(
        state=state,
        linked_samples=linked,
        types=sorted((row or {}).get("linked_types") or []),
    )


async def compare(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    baseline: tuple[Selection, Window],
    comparison: tuple[Selection, Window],
) -> CompareResponse:
    """Two profiles of one service and type, merged by frame path and compared by share."""
    if baseline == comparison:
        raise InvalidRequestError("The baseline and the comparison are the same profile.")
    base_side, base_whole = await _side(
        repository, project_id=project_id, selection=baseline[0], window=baseline[1]
    )
    side, whole = await _side(
        repository, project_id=project_id, selection=comparison[0], window=comparison[1]
    )
    root = tree.difference(base_side, side)
    merged = tree.prune(root)
    return CompareResponse(
        sample_type=comparison[0].sample_type,
        unit=whole.unit or base_whole.unit,
        baseline_samples=base_whole.samples,
        comparison_samples=whole.samples,
        merged_frames=merged,
        root=_diff_node(root, base_side.grand_total, side.grand_total),
        functions=[
            FunctionChangeRow(
                name=row.name,
                file=row.file,
                unsymbolized=row.unsymbolized,
                baseline_self=row.baseline_self,
                comparison_self=row.comparison_self,
                baseline_total=row.baseline_total,
                comparison_total=row.comparison_total,
                change=row.change,
            )
            for row in tree.both_ends(tree.compare_functions(base_side, side), MAX_FUNCTIONS)
        ],
    )


@dataclass(frozen=True, slots=True)
class _Whole:
    total: int
    unit: str
    samples: int


async def _whole(
    repository: TenantScopedRepository, *, project_id: str, selection: Selection, window: Window
) -> _Whole:
    row = await repository.query_one(
        queries.narrowed(queries.TOTAL_SQL, **selection.narrowing()),
        _scope(project_id, window) | selection.parameters(),
    )
    row = row or {}
    return _Whole(
        total=int(row.get("total") or 0),
        unit=str(row.get("unit") or ""),
        samples=int(row.get("samples") or 0),
    )


async def _side(
    repository: TenantScopedRepository, *, project_id: str, selection: Selection, window: Window
) -> tuple[tree.Side, _Whole]:
    whole = await _whole(repository, project_id=project_id, selection=selection, window=window)
    parameters = _scope(project_id, window) | selection.parameters()
    narrowing = selection.narrowing()
    ranked = await repository.query(
        queries.narrowed(queries.RANKED_STACKS_SQL, **narrowing), parameters
    )
    if not ranked:
        return tree.Side(stacks=[], grand_total=whole.total), whole
    framed = {
        int(row["stack_hash"]): row
        for row in await repository.query(
            queries.narrowed(queries.STACK_FRAMES_SQL, **narrowing),
            parameters,
        )
    }
    stacks = [
        tree.Stack(
            functions=framed[int(row["stack_hash"])]["functions"],
            files=framed[int(row["stack_hash"])]["files"],
            lines=framed[int(row["stack_hash"])]["lines"],
            binaries=framed[int(row["stack_hash"])]["binaries"],
            value=int(row["value"]),
        )
        for row in ranked
        # A stack whose rows expired between the two reads is left to the remainder.
        if int(row["stack_hash"]) in framed
    ]
    # The whole was read first; rows arriving since can make the stacks account for
    # more than it. The larger is the whole, so no share passes 100 %.
    accounted = sum(stack.value for stack in stacks)
    return tree.Side(stacks=stacks, grand_total=max(whole.total, accounted)), whole


def _scope(project_id: str, window: Window) -> dict[str, Any]:
    received_from, received_to = receive_window(window.start, window.end)
    return {
        "project_id": project_id,
        "from_time": window.start,
        "to_time": window.end,
        "received_from": received_from,
        "received_to": received_to,
    }


def _utc(value: datetime) -> datetime:
    """ClickHouse returns its UTC instants without a zone; say which zone they are in."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _Seen:
    samples: int
    last_seen: datetime


def _values(seen: dict[str, _Seen]) -> list[FilterValue]:
    ranked = sorted(seen.items(), key=lambda item: (-item[1].samples, item[0]))
    return [
        FilterValue(value=value, samples=found.samples, last_seen=found.last_seen)
        for value, found in ranked[:MAX_FILTER_VALUES]
    ]


def _bucket_starts(window: Window, bucket_seconds: int) -> list[int]:
    """Every bucket the window touches, as epoch seconds, including the silent ones."""
    first = int(window.start.timestamp()) // bucket_seconds * bucket_seconds
    return list(range(first, ceil(window.end.timestamp()), bucket_seconds))


def _node(node: tree.Node) -> FlameNode:
    return FlameNode(
        name=node.name,
        file=node.file,
        unsymbolized=node.unsymbolized,
        total=node.total,
        self_value=node.self_value,
        merged=node.merged,
        line=node.line if node.line > 0 else None,
        children=[_node(child) for child in _heaviest_first(node)],
    )


def _diff_node(node: tree.Node, baseline_whole: int, comparison_whole: int) -> DiffNode:
    return DiffNode(
        name=node.name,
        file=node.file,
        unsymbolized=node.unsymbolized,
        merged=node.merged,
        weight=node.total,
        baseline_share=node.baseline / baseline_whole if baseline_whole > 0 else 0.0,
        comparison_share=node.comparison / comparison_whole if comparison_whole > 0 else 0.0,
        children=[
            _diff_node(child, baseline_whole, comparison_whole) for child in _heaviest_first(node)
        ],
    )


def _heaviest_first(node: tree.Node) -> list[tree.Node]:
    return sorted(node.children.values(), key=lambda child: child.total, reverse=True)
