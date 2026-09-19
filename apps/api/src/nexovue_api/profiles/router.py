"""Continuous profiling reads (F17).

`project_id` arrives as a query parameter, a value the caller chose; it is proved
to belong to the caller's organization before it reaches a telemetry query, and
the repository is bound to the session's tenant, never to the URL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.profiles import service
from nexovue_api.profiles.schemas import (
    CompareResponse,
    FlameGraphResponse,
    LatestProfileResponse,
    ProfiledServicesResponse,
    ProfileFiltersResponse,
    ProfileTimelineResponse,
    SpanProfileResponse,
)
from nexovue_api.profiles.service import Selection, Window
from nexovue_api.projects.service import require_project
from nexovue_api.window import resolve_window

router = APIRouter(prefix="/profiles", tags=["profiles"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read profiles from")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]
Service = Annotated[str, Query(min_length=1, max_length=255, description="The service profiled")]
OptionalService = Annotated[str | None, Query(min_length=1, max_length=255)]
SampleType = Annotated[
    str, Query(alias="type", min_length=1, max_length=128, description="The profile type, e.g. cpu")
]
# An empty string is a real value here: samples that named no host or version.
Narrow = Annotated[str | None, Query(max_length=255)]
# Hex, as `spans` stores them — the two columns a span's profile is an equality on.
TraceId = Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")]
SpanId = Annotated[str | None, Query(pattern=r"^[0-9a-f]{16}$")]


@router.get("/services", summary="Services that sent profiles in a time window")
async def get_profiled_services(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> ProfiledServicesResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.profiled_services(
        repository, project_id=str(project.id), window=_window(from_time, to_time)
    )


@router.get("/latest", summary="The newest profile held, for a project or one service")
async def get_latest_profile(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    service_name: OptionalService = None,
) -> LatestProfileResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.latest_profile(
        repository, project_id=str(project.id), service=service_name
    )


@router.get("/filters", summary="The hosts, versions and environments a profile carries")
async def get_profile_filters(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    service_name: Service,
    sample_type: SampleType,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> ProfileFiltersResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.profile_filters(
        repository,
        project_id=str(project.id),
        selection=Selection(service=service_name, sample_type=sample_type),
        window=_window(from_time, to_time),
    )


@router.get("/timeline", summary="A profile type's value over time")
async def get_profile_timeline(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    service_name: Service,
    sample_type: SampleType,
    from_time: FromTime = None,
    to_time: ToTime = None,
    host: Narrow = None,
    version: Narrow = None,
    environment: Narrow = None,
) -> ProfileTimelineResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.profile_timeline(
        repository,
        project_id=str(project.id),
        selection=Selection(
            service=service_name,
            sample_type=sample_type,
            host=host,
            version=version,
            environment=environment,
        ),
        window=_window(from_time, to_time),
    )


@router.get("/graph", summary="A flame graph and functions table, for a service or one span")
async def get_flame_graph(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    service_name: Service,
    sample_type: SampleType,
    from_time: FromTime = None,
    to_time: ToTime = None,
    host: Narrow = None,
    version: Narrow = None,
    environment: Narrow = None,
    trace_id: TraceId = None,
    span_id: SpanId = None,
) -> FlameGraphResponse:
    if (trace_id is None) != (span_id is None):
        raise InvalidRequestError("A span is named by both `trace_id` and `span_id`.")
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.flame_graph(
        repository,
        project_id=str(project.id),
        selection=Selection(
            service=service_name,
            sample_type=sample_type,
            host=host,
            version=version,
            environment=environment,
            trace_id=trace_id,
            span_id=span_id,
        ),
        window=_window(from_time, to_time),
    )


@router.get("/span", summary="Whether a span has a profile of its own")
async def get_span_profile(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    service_name: Service,
    trace_id: Annotated[str, Query(pattern=r"^[0-9a-f]{32}$")],
    span_id: Annotated[str, Query(pattern=r"^[0-9a-f]{16}$")],
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> SpanProfileResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.span_profile(
        repository,
        project_id=str(project.id),
        service=service_name,
        trace_id=trace_id,
        span_id=span_id,
        span_window=_window(from_time, to_time),
    )


@router.get("/compare", summary="Two profiles of one service compared by share")
async def get_profile_comparison(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    service_name: Service,
    sample_type: SampleType,
    base_from: Annotated[datetime | None, Query()] = None,
    base_to: Annotated[datetime | None, Query()] = None,
    base_version: Narrow = None,
    from_time: FromTime = None,
    to_time: ToTime = None,
    version: Narrow = None,
    host: Narrow = None,
    environment: Narrow = None,
) -> CompareResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    comparison_window = _window(from_time, to_time)
    # Two versions are compared over the same window unless a baseline range is named.
    baseline_window = (
        _window(base_from, base_to)
        if base_from is not None or base_to is not None
        else comparison_window
    )

    def side(chosen_version: str | None) -> Selection:
        return Selection(
            service=service_name,
            sample_type=sample_type,
            host=host,
            version=chosen_version,
            environment=environment,
        )

    return await service.compare(
        repository,
        project_id=str(project.id),
        baseline=(side(base_version), baseline_window),
        comparison=(side(version), comparison_window),
    )


def _window(from_time: datetime | None, to_time: datetime | None) -> Window:
    start, end = resolve_window(from_time, to_time)
    return Window(start=start, end=end)
