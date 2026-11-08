"""How each panel becomes a read, and what a definition costs at worst (F20, design section 4).

Each panel kind maps its settings to at most one `ScanKey`: a frozen value naming a
reader and its arguments. Panels whose keys are equal share one scan, so a service's
rate, errors and latency are one read (FR11). `dashboardpanels/scans.py` holds the
reader for each key; this module reads no store, so saving can count a definition's
worst case without importing anything that reads ClickHouse (FR7, D31).

**The picked service.** A panel naming the dashboard's picker compiles to a key with
`picked=True`. At save, every such key counts as one service apart from any named one,
which is the worst case: *Service health*'s three picker trends are one scan, and a
named `checkout` beside them is a second (spec, *The templates*). At read time
`resolve` puts the picked name in, so a picked `checkout` and a named one share a scan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from nexovue_api.dashboards.definition import (
    Definition,
    NotePanel,
    Picker,
    ProjectTrendPanel,
    ServiceTrendPanel,
    StoredDefinition,
    UnknownPanel,
    uses_picker,
)
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.ratelimit.policy import MAX_SCANS_PER_READ

# Reader names, one per existing service function a scan calls (`dashboardpanels/scans.py`).
PROJECT_TREND: Final = "project_trend"
SERVICE_TREND: Final = "service_trend"

AnyPanel = ProjectTrendPanel | ServiceTrendPanel | NotePanel | UnknownPanel


@dataclass(frozen=True, slots=True)
class ScanKey:
    """What makes two panels want the same read: the reader and its arguments. The
    project and window are the request's, added when the read is shared."""

    reader: str
    service: str | None = None
    # The dashboard's picker, before `resolve` puts the picked service in.
    picked: bool = False


@dataclass(frozen=True, slots=True)
class CompiledPanel:
    panel_id: str
    kind: str
    # None for a panel with no reader: a note, or a kind this code cannot read.
    scan: ScanKey | None


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


def scan_of(panel: AnyPanel) -> ScanKey | None:
    """The one read a panel needs, if any."""
    if isinstance(panel, ProjectTrendPanel):
        return ScanKey(reader=PROJECT_TREND)
    if isinstance(panel, ServiceTrendPanel):
        service = panel.settings.service
        if isinstance(service, Picker):
            return ScanKey(reader=SERVICE_TREND, picked=True)
        return ScanKey(reader=SERVICE_TREND, service=service)
    return None


def compile_definition(definition: Definition | StoredDefinition) -> CompiledDefinition:
    panels = tuple(
        CompiledPanel(panel_id=panel.id, kind=panel.kind, scan=scan_of(panel))
        for panel in definition.panels
    )
    scans = tuple(dict.fromkeys(panel.scan for panel in panels if panel.scan is not None))
    named = {
        panel.settings.service
        for panel in definition.panels
        if isinstance(panel, ServiceTrendPanel) and isinstance(panel.settings.service, str)
    }
    return CompiledDefinition(
        panels=panels,
        scans=scans,
        panel_count=len(panels),
        service_names=sorted(named),
        uses_picker=any(uses_picker(panel) for panel in definition.panels),
    )


def compile_for_save(definition: Definition) -> CompiledDefinition:
    """The compiled definition, or a refusal naming the count and the panels past the cap.

    The same check for a save, a draft, a file and a template, with no path around it.
    """
    compiled = compile_definition(definition)
    if compiled.worst_case_scans <= MAX_SCANS_PER_READ:
        return compiled
    allowed = set(compiled.scans[:MAX_SCANS_PER_READ])
    past = [
        panel.panel_id
        for panel in compiled.panels
        if panel.scan is not None and panel.scan not in allowed
    ]
    raise InvalidRequestError(
        f"This dashboard would need {compiled.worst_case_scans} reads, and one page may make "
        f"{MAX_SCANS_PER_READ}. Remove panels or use the picker.",
        scans=compiled.worst_case_scans,
        max_scans=MAX_SCANS_PER_READ,
        panels_to_remove=past,
    )


def resolve(scan: ScanKey | None, *, picked: str | None) -> ScanKey | None:
    """A key with the picked service in place, or None when the picker is unset."""
    if scan is None or not scan.picked:
        return scan
    if picked is None:
        return None
    return replace(scan, service=picked, picked=False)
