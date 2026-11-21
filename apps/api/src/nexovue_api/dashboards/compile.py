"""How each panel becomes a read, and what a definition costs at worst (F20, design section 4).

Each panel kind maps its settings to at most two `ScanKey`s: a frozen value naming a
reader and its arguments, and for a compared number a second one over the previous
window. Panels whose keys are equal share one scan, so a service's rate, errors and
latency are one read (FR11). `dashboardpanels/scans.py` holds the reader for each key;
this module reads no store, so saving can count a definition's worst case without
importing anything that reads ClickHouse (FR7, D31).

**The picked service.** A panel naming the dashboard's picker compiles to a key with
`picked=True`. At save, every such key counts as one service apart from any named one,
which is the worst case: *Service health*'s three picker trends are one scan, and a
named `checkout` beside them is a second (spec, *The templates*). At read time
`resolve` puts the picked name in, so a picked `checkout` and a named one share a scan.

**A check by name.** A `check_trend` key names the check; the read resolves it to one
`check_id` in the displayed project before any scan, and the scan's key carries that
id, so one name on two projects can never share a read.

**A reliability target by name is a pre-read alone.** A `slo_budget` panel compiles to no
key: F19's reader answers from Postgres before any scan (`slos.service.budgets_named`),
so thirty of them cost no read and change no worst case (spec: "0: a Postgres read
only"). `dashboardpanels/service.py` decides its state from that pre-read.

**A chosen metric series counts as two** (design, *`series_changed`*). Its own read, and,
only when that comes back empty, one further charged read asking whether the metric still
reports under other attributes. The second is the panel's `fallback`: it counts in the
worst case at save, and holds its place under the scan cap at read time, but runs only
when needed.

**Tables read their kind's fullest page.** A table key holds no row count: the store
returns the first `MAX_TABLE_ROWS` of its ranking, and each panel shows its own first
rows of it. The ranking is the store's, so a panel's first five are the route's own
five, and two tables of one ranking share one scan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from nexovue_api.dashboards.definition import (
    CheckTrendPanel,
    DatabaseStatementsTablePanel,
    Definition,
    DependenciesTablePanel,
    EndpointsTablePanel,
    EndpointTrendPanel,
    ErrorGroupsTablePanel,
    KnownPanel,
    MetricSeriesPanel,
    NodeRef,
    NodeTrendPanel,
    Picker,
    ProjectNumberPanel,
    ProjectTrendPanel,
    ServiceNumberPanel,
    ServicesTablePanel,
    ServiceTrendPanel,
    SloBudgetPanel,
    StatementTrendPanel,
    StoredDefinition,
    TopNodesTrendPanel,
    TopServicesTrendPanel,
    UnknownPanel,
    WebVitalsTablePanel,
    service_of,
    uses_picker,
)
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.ratelimit.policy import MAX_SCANS_PER_READ

# Reader names, one per existing service function a scan calls (`dashboardpanels/scans.py`).
PROJECT_TREND: Final = "project_trend"
SERVICE_TREND: Final = "service_trend"
TOP_SERVICES_TREND: Final = "top_services_trend"
NODE_TREND: Final = "node_trend"
TOP_NODES_TREND: Final = "top_nodes_trend"
ENDPOINT_TREND: Final = "endpoint_trend"
CHECK_TREND: Final = "check_trend"
STATEMENT_TREND: Final = "statement_trend"
PROJECT_TOTALS: Final = "project_totals"
SERVICE_TOTALS: Final = "service_totals"
SERVICES_RANKED: Final = "services_ranked"
ENDPOINTS_RANKED: Final = "endpoints_ranked"
DEPENDENCIES: Final = "dependencies"
ERROR_GROUPS: Final = "error_groups"
SLOW_STATEMENTS: Final = "slow_statements"
WEB_VITALS: Final = "web_vitals"
METRIC_SERIES: Final = "metric_series"
# Whether a metric reports under any attributes: a chosen series' second read (FR16).
METRIC_REPORTS: Final = "metric_reports"
# Readers that run only when their panel's first read asks for them.
FALLBACK_READERS: Final = frozenset({METRIC_REPORTS})

AnyPanel = KnownPanel | UnknownPanel


@dataclass(frozen=True, slots=True)
class NodeIdentity:
    """A node as `infrastructure/service.NodeKey` names it, hashable for a scan key."""

    host: str
    container: str
    reporter: str
    reporter_instance: str


@dataclass(frozen=True, slots=True)
class StatementIdentity:
    """A database statement as the database screen keys it: hash, server and port."""

    query_hash: str
    server_address: str
    server_port: int


@dataclass(frozen=True, slots=True)
class ScanKey:
    """What makes two panels want the same read: the reader and its arguments. The
    project and window are the request's, added when the read is shared."""

    reader: str
    service: str | None = None
    # The dashboard's picker, before `resolve` puts the picked service in.
    picked: bool = False
    # A ranking's order, and a top-N chart's count. A top-N chart's signal is not here:
    # one read answers rate, errors and latency alike.
    order: str | None = None
    count: int | None = None
    # A node chart's node, and a node, top-nodes or metric series chart's metric (FR6).
    node: NodeIdentity | None = None
    metric: str | None = None
    # A metric series' shape, its chosen series (the hash, as decimal digits), and how
    # its series combine into one line.
    shape: str | None = None
    series_hash: str | None = None
    aggregation: str | None = None
    # An endpoint chart's endpoint: its span kind and span name.
    span_kind: str | None = None
    endpoint: str | None = None
    # A check chart's check by name at save, and by id once a read has resolved it.
    check: str | None = None
    check_id: str | None = None
    statement: StatementIdentity | None = None
    failing_only: bool | None = None
    device_class: str | None = None
    # The window before the read's own, of the same length: a compared number (T5.3).
    previous: bool = False


@dataclass(frozen=True, slots=True)
class CompiledPanel:
    panel_id: str
    kind: str
    # None for a panel with no reader: a note, open findings, a reliability target (read
    # from Postgres before any scan), or an unknown kind.
    scan: ScanKey | None
    # A compared number's read of the previous window.
    compare: ScanKey | None = None
    # A chosen metric series' further read, run only when its own comes back empty.
    fallback: ScanKey | None = None

    @property
    def keys(self) -> tuple[ScanKey, ...]:
        """Every read this panel may need: what the worst case and the scan cap count."""
        return tuple(key for key in (self.scan, self.compare, self.fallback) if key is not None)


@dataclass(frozen=True, slots=True)
class CompiledDefinition:
    panels: tuple[CompiledPanel, ...]
    # Distinct keys in layout order: a refused or late scan lands on the lowest panels.
    scans: tuple[ScanKey, ...]
    panel_count: int
    # Literal services the panels name, for the list's "showing this service" (FR57).
    service_names: list[str]
    uses_picker: bool

    @property
    def worst_case_scans(self) -> int:
        return len(self.scans)


def _for_service(reader: str, panel: AnyPanel) -> ScanKey:
    """One service's read: the named service, the picker, or with none the project's."""
    service = service_of(panel)
    if isinstance(service, Picker):
        return ScanKey(reader=reader, picked=True)
    return ScanKey(reader=reader, service=service)


def _node(node: NodeRef) -> NodeIdentity:
    return NodeIdentity(
        host=node.host,
        container=node.container,
        reporter=node.reporter,
        reporter_instance=node.reporter_instance,
    )


def scan_of(panel: AnyPanel) -> ScanKey | None:
    """The one read a panel needs over its own window, if any."""
    match panel:
        case ProjectTrendPanel():
            return ScanKey(reader=PROJECT_TREND)
        case ServiceTrendPanel():
            return _for_service(SERVICE_TREND, panel)
        case TopServicesTrendPanel(settings=settings):
            return ScanKey(reader=TOP_SERVICES_TREND, order=settings.order, count=settings.count)
        case NodeTrendPanel(settings=settings):
            return ScanKey(reader=NODE_TREND, node=_node(settings.node), metric=settings.metric)
        case TopNodesTrendPanel(settings=settings):
            return ScanKey(reader=TOP_NODES_TREND, metric=settings.metric, count=settings.count)
        case EndpointTrendPanel(settings=settings):
            return replace(
                _for_service(ENDPOINT_TREND, panel),
                span_kind=settings.span_kind,
                endpoint=settings.endpoint,
            )
        case CheckTrendPanel(settings=settings):
            return ScanKey(reader=CHECK_TREND, check=settings.check)
        case StatementTrendPanel(settings=settings):
            return ScanKey(
                reader=STATEMENT_TREND,
                statement=StatementIdentity(
                    query_hash=settings.query_hash,
                    server_address=settings.server_address,
                    server_port=settings.server_port,
                ),
            )
        case ProjectNumberPanel():
            return ScanKey(reader=PROJECT_TOTALS)
        case ServiceNumberPanel():
            return _for_service(SERVICE_TOTALS, panel)
        case ServicesTablePanel(settings=settings):
            return ScanKey(reader=SERVICES_RANKED, order=settings.order)
        case EndpointsTablePanel(settings=settings):
            return replace(_for_service(ENDPOINTS_RANKED, panel), order=settings.order)
        case DependenciesTablePanel(settings=settings):
            return ScanKey(reader=DEPENDENCIES, failing_only=settings.failing_only)
        case ErrorGroupsTablePanel():
            return _for_service(ERROR_GROUPS, panel)
        case DatabaseStatementsTablePanel():
            return ScanKey(reader=SLOW_STATEMENTS)
        case WebVitalsTablePanel(settings=settings):
            return ScanKey(reader=WEB_VITALS, device_class=settings.device_class)
        case MetricSeriesPanel(settings=settings):
            return replace(
                _for_service(METRIC_SERIES, panel),
                metric=settings.metric,
                shape=settings.shape,
                series_hash=settings.series,
                aggregation=settings.aggregation,
            )
        case SloBudgetPanel():
            return None  # read from Postgres before any scan, never scanned
    return None


def compare_of(panel: AnyPanel, scan: ScanKey | None) -> ScanKey | None:
    """A compared number's second read: the same key over the previous window."""
    if scan is None or not isinstance(panel, ProjectNumberPanel | ServiceNumberPanel):
        return None
    return replace(scan, previous=True) if panel.settings.compare else None


def fallback_of(scan: ScanKey | None) -> ScanKey | None:
    """A chosen metric series' second read: does the metric report under any attributes,
    from the same service or picker? One key per metric, shape and service, so every
    chosen series of one metric shares it."""
    if scan is None or scan.reader != METRIC_SERIES or scan.series_hash is None:
        return None
    return ScanKey(
        reader=METRIC_REPORTS,
        service=scan.service,
        picked=scan.picked,
        metric=scan.metric,
        shape=scan.shape,
    )


def compile_panel(panel: AnyPanel) -> CompiledPanel:
    scan = scan_of(panel)
    return CompiledPanel(
        panel_id=panel.id,
        kind=panel.kind,
        scan=scan,
        compare=compare_of(panel, scan),
        fallback=fallback_of(scan),
    )


def compile_definition(definition: Definition | StoredDefinition) -> CompiledDefinition:
    panels = tuple(compile_panel(panel) for panel in definition.panels)
    scans = tuple(dict.fromkeys(key for panel in panels for key in panel.keys))
    named = {
        service for panel in definition.panels if isinstance(service := service_of(panel), str)
    }
    return CompiledDefinition(
        panels=panels,
        scans=scans,
        panel_count=len(panels),
        service_names=sorted(named),
        uses_picker=any(uses_picker(panel) for panel in definition.panels),
    )


def compile_for_save(
    definition: Definition, *, max_scans: int = MAX_SCANS_PER_READ
) -> CompiledDefinition:
    """The compiled definition, or a refusal naming the count and the panels past the cap.

    The same check for a save, a draft, a file and a template, with no path around it.
    `max_scans` is the install's own cap (`dashboards/limits.py`).
    """
    compiled = compile_definition(definition)
    if compiled.worst_case_scans <= max_scans:
        return compiled
    allowed = set(compiled.scans[:max_scans])
    past = [
        panel.panel_id for panel in compiled.panels if any(key not in allowed for key in panel.keys)
    ]
    raise InvalidRequestError(
        f"This dashboard would need {compiled.worst_case_scans} reads, and one page may make "
        f"{max_scans}. Remove panels or use the picker.",
        scans=compiled.worst_case_scans,
        max_scans=max_scans,
        panels_to_remove=past,
    )


def resolve(scan: ScanKey | None, *, picked: str | None) -> ScanKey | None:
    """A key with the picked service in place, or None when the picker is unset."""
    if scan is None or not scan.picked:
        return scan
    if picked is None:
        return None
    return replace(scan, service=picked, picked=False)


def resolve_check(scan: ScanKey, check_id: str) -> ScanKey:
    """A check chart's key once its name is one check in the displayed project."""
    return replace(scan, check=None, check_id=check_id)
