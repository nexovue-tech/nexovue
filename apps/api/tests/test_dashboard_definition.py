"""The dashboard validator, from the spec's allowed-values table (F20, T2.5).

`dashboards/definition.py` is the only way a definition enters (design section 3), and
its allowed values are the spec's table, which parametrizes these cases: AC4 (unknown
field, unknown kind, a setting outside the table, 31 panels, 65 KiB, a worst case over
the scan cap), AC5 (`"sql"`, a `query` kind), AC42 (a stored panel of a kind the code no
longer knows reads `unknown_kind`, and the rest still parse), AC77 (`javascript:`),
AC123 (control and invisible characters and NUL), AC124 (link credentials, more than 5
links, hosts only), AC141 (`default_range`'s list held to the console's).

Step 2 knew three kinds: `project_trend`, `service_trend` and `note` (plan, Step 2).
Step 4 adds `top_services_trend` and `node_trend` (T4.1, T4.2). Step 5 adds every other
kind but `metric_series` and `slo_budget` (T5.1a to T5.2), with the measures, the check
signals and the table rows of the spec's table, and the install's own limits (T5.6c).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import ValidationError

from nexovue_api.dashboardpanels.scans import RAW_READERS
from nexovue_api.dashboards import compile as compiling
from nexovue_api.dashboards import definition, ranges, templates
from nexovue_api.errors.exceptions import InvalidRequestError

REPOSITORY: Final = Path(__file__).resolve().parents[3]
SELECTION_TS: Final = REPOSITORY / "apps/web/src/shared/selection.ts"
MASK_RULES_GO: Final = REPOSITORY / "apps/workers/internal/pipeline/mask/rules.go"

# The spec's allowed-values table, for the kinds Step 2 builds.
SIGNALS: Final = ("rate", "errors", "latency")
WIDTHS: Final = (3, 4, 6, 8, 12)
HEIGHTS: Final = ("small", "medium", "large")
REFRESH: Final = (30, 60, 300, None)
DEFAULT_RANGES: Final = ("15m", "1h", "6h", "24h", "7d", "30d", None)
ORDERS: Final = ("worst", "slowest")
COUNTS: Final = (1, 2, 3, 4, 5)
A_HOST: Final = {"host": "web-01", "container": "", "reporter": "", "reporter_instance": ""}
MEASURES: Final = ("requests_per_minute", "error_rate", "p50_ms", "p95_ms", "p99_ms")
PROJECT_MEASURES: Final = (*MEASURES, "service_count")
CHECK_SIGNALS: Final = ("uptime", "duration")
ROWS: Final = (1, 10, 20)


def _panel(panel_id: str = "p_01", **changes: Any) -> dict[str, Any]:
    panel: dict[str, Any] = {
        "id": panel_id,
        "kind": "service_trend",
        "title": None,
        "width": 4,
        "height": "medium",
        "settings": {"service": "checkout", "signal": "latency"},
    }
    panel.update(changes)
    return panel


def _document(**definition_changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "refresh_seconds": 60,
        "default_range": None,
        "service_picker": False,
        "links": [],
        "panels": [_panel()],
    }
    body.update(definition_changes)
    return {"title": "Checkout on-call", "description": "", "definition": body}


def _accepted(document: dict[str, Any]) -> definition.DashboardDocument:
    return definition.parse_document(document)


def _refused(document: dict[str, Any]) -> str:
    with pytest.raises((ValidationError, InvalidRequestError)) as refusal:
        definition.parse_document(document)
    return str(refusal.value)


# ------------------------------------------------------------------- AC4, AC5


def test_a_plain_document_is_accepted() -> None:
    parsed = _accepted(_document())

    assert parsed.title == "Checkout on-call"
    assert parsed.definition.panels[0].kind == "service_trend"


@pytest.mark.parametrize(
    "path",
    [(), ("definition",), ("definition", "panels", 0), ("definition", "panels", 0, "settings")],
    ids=["document", "definition", "panel", "settings"],
)
def test_ac4_an_unknown_field_is_refused_at_every_level(path: tuple[Any, ...]) -> None:
    document = _document()
    target: Any = document
    for step in path:
        target = target[step]
    target["colour"] = "red"

    assert "colour" in _refused(document)


@pytest.mark.parametrize("kind", ["query", "sql", "metric_series_v9", ""])
def test_ac4_ac5_an_unknown_kind_is_refused(kind: str) -> None:
    _refused(_document(panels=[_panel(kind=kind)]))


def test_ac5_a_panel_holding_sql_is_refused() -> None:
    panel = _panel()
    panel["settings"]["sql"] = "SELECT * FROM spans"

    assert "sql" in _refused(_document(panels=[panel]))


@pytest.mark.parametrize("signal", SIGNALS)
@pytest.mark.parametrize("kind", ["project_trend", "service_trend"])
def test_every_allowed_signal_is_accepted(kind: str, signal: str) -> None:
    settings: dict[str, Any] = {"signal": signal}
    if kind == "service_trend":
        settings["service"] = "checkout"
    _accepted(_document(panels=[_panel(kind=kind, settings=settings)]))


@pytest.mark.parametrize("signal", ["uptime", "duration", "p95", "RATE", ""])
def test_ac4_a_signal_outside_the_table_is_refused(signal: str) -> None:
    _refused(_document(panels=[_panel(settings={"service": "checkout", "signal": signal})]))


@pytest.mark.parametrize("width", WIDTHS)
def test_every_allowed_width_is_accepted(width: int) -> None:
    _accepted(_document(panels=[_panel(width=width)]))


@pytest.mark.parametrize("width", [0, 1, 2, 5, 7, 9, 10, 11, 13, 24])
def test_ac4_a_width_outside_the_table_is_refused(width: int) -> None:
    _refused(_document(panels=[_panel(width=width)]))


@pytest.mark.parametrize("height", ["medium", "large"])
def test_a_time_panel_takes_medium_or_large(height: str) -> None:
    _accepted(_document(panels=[_panel(height=height)]))


@pytest.mark.parametrize("kind", ["project_trend", "service_trend"])
def test_ac67_a_time_panel_saved_small_is_refused(kind: str) -> None:
    settings: dict[str, Any] = {"signal": "rate"}
    if kind == "service_trend":
        settings["service"] = "checkout"

    assert "small" in _refused(
        _document(panels=[_panel(kind=kind, height="small", settings=settings)])
    )


@pytest.mark.parametrize("height", HEIGHTS)
def test_a_note_takes_any_height(height: str) -> None:
    _accepted(_document(panels=[_panel(kind="note", height=height, settings={"text": "hi"})]))


@pytest.mark.parametrize("refresh", REFRESH)
def test_every_allowed_refresh_is_accepted(refresh: int | None) -> None:
    _accepted(_document(refresh_seconds=refresh))


@pytest.mark.parametrize("refresh", [0, 5, 10, 15, 29, 120, 3600])
def test_ac4_a_refresh_outside_the_table_is_refused(refresh: int) -> None:
    _refused(_document(refresh_seconds=refresh))


@pytest.mark.parametrize("default_range", DEFAULT_RANGES)
def test_every_allowed_default_range_is_accepted(default_range: str | None) -> None:
    _accepted(_document(default_range=default_range))


@pytest.mark.parametrize("default_range", ["2h", "1d", "90d", "30D", ""])
def test_ac141_an_unknown_default_range_is_refused(default_range: str) -> None:
    _refused(_document(default_range=default_range))


def test_ac4_thirty_panels_are_accepted_and_thirty_one_are_refused_by_count() -> None:
    panels = [_panel(f"p_{index}", kind="note", settings={"text": "x"}) for index in range(31)]
    _accepted(_document(panels=panels[:30]))

    assert "30" in _refused(_document(panels=panels))


def test_ac4_a_document_over_64_kib_is_refused_naming_the_size() -> None:
    long_note = "x" * definition.NOTE_MAX_LENGTH
    panels = [
        _panel(f"p_{index}", kind="note", title="t" * 120, settings={"text": long_note})
        for index in range(30)
    ]
    document = _document(panels=panels) | {"description": "d" * 500}
    compact = json.dumps(document, separators=(",", ":")).encode()
    assert len(compact) > definition.MAX_DOCUMENT_BYTES

    with pytest.raises(InvalidRequestError, match="64 KiB"):
        definition.parse_document(document)


def test_ac4_a_worst_case_of_41_scans_is_refused_naming_count_and_panels() -> None:
    """A compared number costs two scans, so 21 of them on 21 services ask for 42 and 30
    panels can pass the cap of 40 (spec, FR6). The refusal names the count, the cap and
    the panels past it, in layout order."""
    panels = [
        _panel(
            f"p_{index}",
            kind="service_number",
            height="small",
            settings={"service": f"svc-{index}", "measure": "p95_ms", "compare": True},
        )
        for index in range(20)
    ]
    panels.append(_panel("p_last", settings={"service": "svc-last", "signal": "rate"}))
    parsed = _accepted(_document(panels=panels))

    with pytest.raises(InvalidRequestError) as refusal:
        compiling.compile_for_save(parsed.definition)

    assert "41 reads" in refusal.value.detail
    assert "40" in refusal.value.detail
    assert refusal.value.extensions["panels_to_remove"] == ["p_last"]


def test_ac4_a_lowered_cap_is_the_installs_own() -> None:
    """A self-hosted install's lower scan cap refuses as the code's does (T5.6c)."""
    panels = [
        _panel(f"p_{index}", settings={"service": f"svc-{index}", "signal": "rate"})
        for index in range(4)
    ]
    parsed = _accepted(_document(panels=panels))

    with pytest.raises(InvalidRequestError) as refusal:
        compiling.compile_for_save(parsed.definition, max_scans=3)

    assert "4 reads" in refusal.value.detail
    assert refusal.value.extensions["panels_to_remove"] == ["p_3"]


def test_ac41_a_newer_schema_version_is_refused_plainly() -> None:
    """Said as a newer install's file, not as "Input should be 1"."""
    refusal = _refused(_document(schema_version=2))

    assert "format 2, made by a newer version of nexovue" in refusal
    assert "this install reads format 1" in refusal
    assert "Input should be" not in refusal


@pytest.mark.parametrize("version", [0, "1", True, None])
def test_a_schema_version_that_is_not_a_version_is_refused(version: object) -> None:
    assert "newer version" not in _refused(_document(schema_version=version))


def test_panel_ids_are_unique_and_short() -> None:
    _refused(_document(panels=[_panel("p_1"), _panel("p_1")]))
    _refused(_document(panels=[_panel("has space")]))
    _refused(_document(panels=[_panel("x" * 33)]))


@pytest.mark.parametrize("service", ["", "x" * 256])
def test_a_service_name_is_one_to_255_characters(service: str) -> None:
    _refused(_document(panels=[_panel(settings={"service": service, "signal": "rate"})]))


def test_a_picker_reference_needs_the_dashboards_picker() -> None:
    picked = _panel(settings={"service": {"picker": True}, "signal": "rate"})

    _refused(_document(panels=[picked]))
    _accepted(_document(service_picker=True, panels=[picked]))
    _refused(
        _document(
            service_picker=True,
            panels=[_panel(settings={"service": {"picker": False}, "signal": "rate"})],
        )
    )


def test_a_note_is_at_most_2000_characters() -> None:
    note = _panel(kind="note", settings={"text": "x" * (definition.NOTE_MAX_LENGTH + 1)})

    _refused(_document(panels=[note]))


def test_a_title_is_one_to_120_characters_and_a_description_at_most_500() -> None:
    _refused(_document() | {"title": ""})
    _refused(_document() | {"title": "x" * 121})
    _refused(_document() | {"description": "x" * 501})


# ----------------------------------------------------------------------- AC123


INVISIBLE: Final = ["\u202e", "\u200b", "\u2066", "\U000e0041", "\x00", "\x07", "\n", "\u2028"]


@pytest.mark.parametrize("character", INVISIBLE, ids=lambda value: f"U+{ord(value):04X}")
def test_ac123_a_title_label_or_panel_title_holding_an_invisible_character_is_refused(
    character: str,
) -> None:
    _refused(_document() | {"title": f"check{character}out"})
    _refused(_document(links=[{"label": f"Run{character}book", "url": "https://wiki.example/"}]))
    _refused(_document(panels=[_panel(title=f"Late{character}ncy")]))


def test_ac123_nul_is_refused_in_free_text_too_because_postgres_cannot_store_it() -> None:
    _refused(_document() | {"description": "before\x00after"})
    _refused(_document(panels=[_panel(kind="note", settings={"text": "a\x00b"})]))


def test_ac36_free_text_may_hold_markup_which_is_only_ever_shown_as_text() -> None:
    note = _panel(kind="note", settings={"text": "**bold** <img src=x onerror=alert(1)> https://x"})

    _accepted(_document(panels=[note]) | {"description": "line one\nline two"})


# ------------------------------------------------------------- AC77, AC124


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://wiki.example/checkout",
        "https://user:secret@wiki.example/",
        "https://user@wiki.example/",
        "https://wiki.example@evil.test/",
        "https://wiki.example/?token=abc",
        "https://wiki.example/?access_token=abc",
        "https://wiki.example/?api_key=abc",
        "https://wiki.example/?APIKEY=abc",
        "https://wiki.example/?api-key=abc",
        "https://wiki.example/?db_password=x",
        "https://wiki.example/?client_secret=x",
        "https:///no-host",
        "data:text/html,hi",
        "https://wiki.example/\u202epath",
    ],
)
def test_ac77_ac124_a_link_that_is_not_plain_https_without_credentials_is_refused(
    url: str,
) -> None:
    _refused(_document(links=[{"label": "Runbook", "url": url}]))


def test_ac124_a_query_counting_tokens_is_not_a_credential() -> None:
    """The masking rule's own exception: `token` before a trailing `s` is a count."""
    _accepted(_document(links=[{"label": "Usage", "url": "https://wiki.example/?tokens=3"}]))


def test_ac124_at_most_five_links() -> None:
    links = [
        {"label": f"Link {index}", "url": f"https://wiki.example/{index}"} for index in range(6)
    ]

    _accepted(_document(links=links[:5]))
    _refused(_document(links=links))


def test_ac124_link_hosts_are_what_the_audit_keeps() -> None:
    parsed = _accepted(
        _document(
            links=[
                {"label": "Runbook", "url": "https://Wiki.Example/checkout?x=1"},
                {"label": "Board", "url": "https://grafana.example:8443/d/abc"},
            ]
        )
    )

    assert definition.link_hosts(parsed.definition) == ["grafana.example", "wiki.example"]


def test_the_credential_markers_are_the_masking_rules_own() -> None:
    """FR56 names `mask/rules.go`'s markers; a second copy that drifted would let a
    credential-shaped link through that telemetry masking would have blanked."""
    source = MASK_RULES_GO.read_text(encoding="utf-8")
    block = re.search(r"credentialKeyMarkers = \[\]string\{(?P<body>[^}]*)\}", source)
    token = re.search(r'tokenMarker = "(?P<marker>\w+)"', source)
    assert block is not None and token is not None

    assert set(re.findall(r'"([^"]+)"', block.group("body"))) == set(
        definition.CREDENTIAL_KEY_MARKERS
    )
    assert token.group("marker") == definition.TOKEN_MARKER


def test_ac141_the_range_list_is_the_consoles_and_thirty_days() -> None:
    source = SELECTION_TS.read_text(encoding="utf-8")
    block = re.search(r"TIME_RANGES[^=]*=\s*\[(?P<body>.*?)\];", source, re.DOTALL)
    assert block is not None
    console = re.findall(r'id:\s*"([^"]+)"', block.group("body"))

    assert list(ranges.RANGES) == [*console, "30d"]


# ----------------------------------------------------------------------- AC42


def test_ac42_a_stored_panel_of_an_unknown_kind_reads_unknown_and_the_rest_parse() -> None:
    stored = _document(
        panels=[
            _panel("p_1"),
            {"id": "p_2", "kind": "retired_kind", "width": 6, "height": "medium", "settings": {}},
            _panel("p_3", kind="note", settings={"text": "hello"}),
            {"id": 7, "kind": "service_trend"},
        ]
    )["definition"]

    parsed = definition.parse_stored(stored)

    kinds = [panel.kind for panel in parsed.panels]
    assert kinds == ["service_trend", definition.UNKNOWN_KIND, "note", definition.UNKNOWN_KIND]
    assert parsed.panels[1].id == "p_2"
    assert parsed.panels[3].id == "panel_4"


def test_a_stored_definition_is_not_changed_by_reading_it() -> None:
    stored = _document()["definition"]
    before = copy.deepcopy(stored)

    definition.parse_stored(stored)

    assert stored == before


# ------------------------------------------------------------------- compile


def test_three_panels_on_one_service_compile_to_one_scan() -> None:
    panels = [
        _panel(f"p_{signal}", settings={"service": "checkout", "signal": signal})
        for signal in SIGNALS
    ]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    assert compiled.worst_case_scans == 1
    assert {panel.scan for panel in compiled.panels} == {
        compiling.ScanKey(reader=compiling.SERVICE_TREND, service="checkout")
    }


def test_project_trends_share_one_scan_and_a_note_has_none() -> None:
    panels = [
        _panel(f"p_{signal}", kind="project_trend", settings={"signal": signal})
        for signal in SIGNALS
    ] + [_panel("p_note", kind="note", settings={"text": "hi"})]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    assert compiled.worst_case_scans == 1
    assert compiled.panels[-1].scan is None


def test_the_picker_counts_as_one_service_apart_from_any_named_one() -> None:
    """The worst case treats the picked service as distinct from every literal one (FR7);
    *Service health*'s count of 5 reads its three picker trends as one scan."""
    panels = [
        _panel("p_1", settings={"service": {"picker": True}, "signal": "rate"}),
        _panel("p_2", settings={"service": {"picker": True}, "signal": "errors"}),
        _panel("p_3", settings={"service": "checkout", "signal": "rate"}),
    ]
    compiled = compiling.compile_definition(
        _accepted(_document(service_picker=True, panels=panels)).definition
    )

    assert compiled.worst_case_scans == 2
    assert compiled.uses_picker is True
    assert compiled.service_names == ["checkout"]
    assert compiled.panel_count == 3


def test_a_picked_service_resolves_to_the_same_scan_as_a_named_one() -> None:
    panels = [
        _panel("p_1", settings={"service": {"picker": True}, "signal": "rate"}),
        _panel("p_2", settings={"service": "checkout", "signal": "errors"}),
    ]
    compiled = compiling.compile_definition(
        _accepted(_document(service_picker=True, panels=panels)).definition
    )

    resolved = {compiling.resolve(panel.scan, picked="checkout") for panel in compiled.panels}

    assert resolved == {compiling.ScanKey(reader=compiling.SERVICE_TREND, service="checkout")}


# ------------------------------------------------------- Step 4's kinds (T4.1, T4.2)


def _top(**settings: Any) -> dict[str, Any]:
    return _panel(
        "p_top",
        kind="top_services_trend",
        settings={"signal": "errors", "order": "worst", "count": 5} | settings,
    )


def _node(**settings: Any) -> dict[str, Any]:
    return _panel(
        "p_node",
        kind="node_trend",
        settings={"node": A_HOST, "metric": "system.cpu.utilization"} | settings,
    )


@pytest.mark.parametrize("count", COUNTS)
@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("signal", SIGNALS)
def test_every_allowed_top_services_setting_is_accepted(
    signal: str, order: str, count: int
) -> None:
    _accepted(_document(panels=[_top(signal=signal, order=order, count=count)]))


@pytest.mark.parametrize(
    "settings",
    [{"count": 0}, {"count": 6}, {"order": "busiest"}, {"signal": "uptime"}, {"service": "x"}],
    ids=["count-0", "count-6", "order", "signal", "extra-field"],
)
def test_ac4_a_top_services_setting_outside_the_table_is_refused(settings: dict[str, Any]) -> None:
    _refused(_document(panels=[_top(**settings)]))


def test_every_infrastructure_metric_name_is_accepted_on_a_node_chart() -> None:
    from nexovue_api.infrastructure.conventions import INFRASTRUCTURE_METRIC_NAMES

    for metric in INFRASTRUCTURE_METRIC_NAMES:
        _accepted(_document(panels=[_node(metric=metric)]))


@pytest.mark.parametrize(
    "settings",
    [
        {"metric": "orders_processed"},
        {"node": {"host": "", "container": "", "reporter": "", "reporter_instance": "i-1"}},
        {"node": A_HOST | {"host": "x" * 256}},
        {"node": A_HOST | {"host": "web\x0001"}},
        {"node": {"host": "web-01"}},
        {"node": "web-01"},
    ],
    ids=["unknown-metric", "names-nothing", "too-long", "nul", "missing-parts", "plain-name"],
)
def test_ac4_a_node_setting_outside_the_table_is_refused(settings: dict[str, Any]) -> None:
    _refused(_document(panels=[_node(**settings)]))


@pytest.mark.parametrize("kind", ["top_services_trend", "node_trend"])
def test_ac67_step_4_charts_saved_small_are_refused(kind: str) -> None:
    panel = _top() if kind == "top_services_trend" else _node()
    assert "cannot be small" in _refused(_document(panels=[panel | {"height": "small"}]))


def test_a_top_services_chart_is_one_scan_per_ordering_and_count_whatever_its_signal() -> None:
    panels = [_top(signal=signal) | {"id": f"p_{signal}"} for signal in SIGNALS] + [
        _top(order="slowest") | {"id": "p_slow"}
    ]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    assert compiled.worst_case_scans == 2
    assert compiled.scans[0] == compiling.ScanKey(
        reader=compiling.TOP_SERVICES_TREND, order="worst", count=5
    )
    assert compiled.service_names == []


def test_a_node_chart_is_one_scan_per_node_and_metric() -> None:
    panels = [
        _node() | {"id": "p_cpu"},
        _node() | {"id": "p_cpu_again"},
        _node(metric="system.memory.usage") | {"id": "p_memory"},
        _node(node=A_HOST | {"host": "web-02"}) | {"id": "p_other"},
    ]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    assert compiled.worst_case_scans == 3
    assert compiled.scans[0] == compiling.ScanKey(
        reader=compiling.NODE_TREND,
        node=compiling.NodeIdentity(host="web-01", container="", reporter="", reporter_instance=""),
        metric="system.cpu.utilization",
    )


def test_a_stored_step_4_chart_reads_back_as_itself() -> None:
    stored = _document(panels=[_top(), _node()])["definition"]
    kinds = [panel.kind for panel in definition.parse_stored(stored).panels]
    assert kinds == ["top_services_trend", "node_trend"]


# ----------------------------------------------------------------- Step 5's kinds

A_STATEMENT: Final = {
    "query_hash": "-6789",
    "server_address": "db-1",
    "server_port": 5432,
    "label": "Orders by customer",
}

# One valid panel of each Step 5 kind, the settings the spec's table allows.
STEP_5: Final[dict[str, dict[str, Any]]] = {
    "endpoint_trend": {
        "service": "checkout",
        "span_kind": "SERVER",
        "endpoint": "GET /orders",
        "signal": "latency",
    },
    "top_nodes_trend": {"metric": "system.cpu.utilization", "count": 5},
    "check_trend": {"check": "Login", "signal": "uptime"},
    "statement_trend": A_STATEMENT,
    "project_number": {"measure": "service_count", "compare": False},
    "service_number": {"service": "checkout", "measure": "p95_ms", "compare": True},
    "open_findings": {"service": None},
    "services_table": {"order": "worst", "rows": 10},
    "endpoints_table": {"service": {"picker": True}, "order": "slowest", "rows": 10},
    "dependencies_table": {"failing_only": True, "rows": 20},
    "error_groups_table": {"service": None, "rows": 10},
    "database_statements_table": {"rows": 5},
    "web_vitals_table": {"device_class": None},
}
NUMBERS: Final = ("project_number", "service_number", "open_findings")
CHARTS: Final = ("endpoint_trend", "top_nodes_trend", "check_trend", "statement_trend")
TABLES: Final = tuple(kind for kind in STEP_5 if kind.endswith("_table"))


def _of(kind: str, height: str = "medium", **settings: Any) -> dict[str, Any]:
    return _panel(f"p_{kind}", kind=kind, height=height, settings=STEP_5[kind] | settings)


@pytest.mark.parametrize("kind", STEP_5)
def test_every_step_5_kind_is_accepted_and_reads_back_as_itself(kind: str) -> None:
    document = _document(panels=[_of(kind)], service_picker=True)
    _accepted(document)

    [panel] = definition.parse_stored(document["definition"]).panels
    assert panel.kind == kind


@pytest.mark.parametrize("kind", (*CHARTS, *TABLES))
def test_ac67_a_step_5_chart_or_table_saved_small_is_refused(kind: str) -> None:
    assert "cannot be small" in _refused(
        _document(panels=[_of(kind, height="small")], service_picker=True)
    )


@pytest.mark.parametrize("kind", NUMBERS)
@pytest.mark.parametrize("height", HEIGHTS)
def test_a_number_takes_any_height(kind: str, height: str) -> None:
    _accepted(_document(panels=[_of(kind, height=height)]))


@pytest.mark.parametrize("measure", PROJECT_MEASURES)
def test_every_project_measure_is_accepted(measure: str) -> None:
    _accepted(_document(panels=[_of("project_number", measure=measure, compare=True)]))


@pytest.mark.parametrize("measure", MEASURES)
def test_every_service_measure_is_accepted(measure: str) -> None:
    _accepted(_document(panels=[_of("service_number", measure=measure)]))


@pytest.mark.parametrize("signal", CHECK_SIGNALS)
def test_every_check_signal_is_accepted(signal: str) -> None:
    _accepted(_document(panels=[_of("check_trend", signal=signal)]))


@pytest.mark.parametrize("rows", ROWS)
@pytest.mark.parametrize("kind", ("services_table", "dependencies_table", "error_groups_table"))
def test_every_table_row_count_is_accepted(kind: str, rows: int) -> None:
    _accepted(_document(panels=[_of(kind, rows=rows)]))


@pytest.mark.parametrize(
    ("kind", "settings"),
    [
        ("service_number", {"measure": "service_count"}),
        ("project_number", {"measure": "p90_ms"}),
        ("project_number", {"compare": "maybe"}),
        ("check_trend", {"signal": "rate"}),
        ("check_trend", {"check": ""}),
        ("check_trend", {"check": "x" * 121}),
        ("endpoint_trend", {"span_kind": "CLIENT"}),
        ("endpoint_trend", {"signal": "uptime"}),
        ("top_nodes_trend", {"count": 6}),
        ("top_nodes_trend", {"metric": "orders_processed"}),
        ("statement_trend", {"server_port": 70_000}),
        ("statement_trend", {"label": "bad\u202eone"}),
        ("services_table", {"rows": 0}),
        ("services_table", {"rows": 21}),
        ("services_table", {"order": "busiest"}),
        ("dependencies_table", {"failing_only": "sometimes"}),
        ("web_vitals_table", {"device_class": "x" * 256}),
        ("open_findings", {"service": ""}),
        ("database_statements_table", {"server": "db-1"}),
    ],
    ids=[
        "service-count-on-a-service",
        "unknown-measure",
        "compare-not-bool",
        "check-signal",
        "check-empty",
        "check-too-long",
        "span-kind",
        "endpoint-signal",
        "top-nodes-6",
        "top-nodes-metric",
        "port",
        "statement-label-escape",
        "rows-0",
        "rows-21",
        "order",
        "failing-only",
        "device-too-long",
        "service-empty",
        "extra-field",
    ],
)
def test_ac4_a_step_5_setting_outside_the_table_is_refused(
    kind: str, settings: dict[str, Any]
) -> None:
    _refused(_document(panels=[_of(kind, **settings)]))


@pytest.mark.parametrize("kind", ("service_number", "endpoint_trend", "endpoints_table"))
def test_a_step_5_picker_reference_needs_the_picker_on(kind: str) -> None:
    panel = _of(kind, service={"picker": True})

    _accepted(_document(panels=[panel], service_picker=True))
    assert "picker" in _refused(_document(panels=[panel], service_picker=False))


def test_a_compared_number_counts_two_scans_and_a_plain_one_counts_one() -> None:
    panels = [
        _of("project_number", compare=True) | {"id": "p_compared"},
        _of("project_number", compare=False, measure="error_rate") | {"id": "p_plain"},
        _of("service_number") | {"id": "p_service"},
    ]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    # The two project numbers share the window's read; the comparison is the second scan.
    assert compiled.worst_case_scans == 4
    assert compiling.ScanKey(reader=compiling.PROJECT_TOTALS, previous=True) in compiled.scans


def test_tables_of_one_ranking_share_a_scan_whatever_their_row_count() -> None:
    panels = [
        _of("services_table", rows=5) | {"id": "p_five"},
        _of("services_table", rows=20) | {"id": "p_twenty"},
        _of("services_table", order="slowest") | {"id": "p_slow"},
    ]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    assert compiled.worst_case_scans == 2


def test_open_findings_and_notes_cost_no_scan() -> None:
    panels = [
        _of("open_findings"),
        _panel("p_note", kind="note", settings={"text": "hi"}),
    ]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    assert compiled.worst_case_scans == 0


def test_a_check_is_one_scan_per_name_until_the_read_resolves_it() -> None:
    panels = [
        _of("check_trend") | {"id": "p_up"},
        _of("check_trend", signal="duration") | {"id": "p_time"},
        _of("check_trend", check="Checkout") | {"id": "p_other"},
    ]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    assert compiled.worst_case_scans == 2
    assert compiling.resolve_check(compiled.scans[0], "c-1") == compiling.ScanKey(
        reader=compiling.CHECK_TREND, check_id="c-1"
    )


def test_every_kind_naming_a_service_lists_it_for_showing_this_service() -> None:
    panels = [
        _of("endpoint_trend", service="payments") | {"id": "p_1"},
        _of("service_number", service="cart") | {"id": "p_2"},
        _of("error_groups_table", service="checkout") | {"id": "p_3"},
        _of("endpoints_table") | {"id": "p_4"},
    ]
    compiled = compiling.compile_definition(
        _accepted(_document(panels=panels, service_picker=True)).definition
    )

    assert compiled.service_names == ["cart", "checkout", "payments"]
    assert compiled.uses_picker is True


def test_every_kind_in_the_spec_s_table_is_known_but_slo_budget() -> None:
    """FR6's list, less `slo_budget` (plan Step 10)."""
    known = {panel.model_fields["kind"].annotation.__args__[0] for panel in _known_panels()}
    assert known == {
        "project_trend",
        "service_trend",
        "top_services_trend",
        "node_trend",
        "note",
        *STEP_5,
        "metric_series",
    }


# ---------------------------------------------------------------- Step 8's metric series

# A hash past 2**53, which a browser's number would round: why it travels as digits.
A_HASH: Final = "13040248669695784898"
A_METRIC_SERIES: Final = {
    "metric": "orders_processed",
    "shape": "sum",
    "service": "checkout",
    "series": A_HASH,
    "aggregation": "sum",
}


def _series(**settings: Any) -> dict[str, Any]:
    return _panel("p_series", kind="metric_series", settings=A_METRIC_SERIES | settings)


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"shape": "gauge", "aggregation": "mean"},
        {"service": None, "series": None},
        {"service": {"picker": True}},
        {"series": "0"},
        {"series": "18446744073709551615"},
        {"aggregation": "min"},
        {"aggregation": "max"},
        {"metric": "x" * 255},
    ],
    ids=["chosen", "gauge", "all", "picker", "zero", "uint64-max", "min", "max", "long-name"],
)
def test_every_allowed_metric_series_setting_is_accepted(settings: dict[str, Any]) -> None:
    document = _document(panels=[_series(**settings)], service_picker=True)
    _accepted(document)

    [panel] = definition.parse_stored(document["definition"]).panels
    assert panel.kind == "metric_series"


@pytest.mark.parametrize(
    "settings",
    [
        {"shape": "histogram"},
        {"aggregation": "median"},
        {"series": "18446744073709551616"},
        {"series": "-1"},
        {"series": "0x1f"},
        {"series": 13040248669695784898},
        {"metric": ""},
        {"metric": "x" * 256},
        {"metric": "nul\x00"},
        {"attributes": {"region": "eu"}},
    ],
    ids=[
        "histogram",
        "aggregation",
        "past-uint64",
        "negative",
        "hex",
        "a-number",
        "metric-empty",
        "metric-too-long",
        "metric-nul",
        "attribute-values",
    ],
)
def test_ac4_ac121_a_metric_series_setting_outside_the_table_is_refused(
    settings: dict[str, Any],
) -> None:
    """A file holds the hash and never the attributes (AC121); a hash is digits only."""
    _refused(_document(panels=[_series(**settings)]))


def test_ac67_a_metric_series_saved_small_is_refused() -> None:
    panel = _series() | {"height": "small"}
    assert "cannot be small" in _refused(_document(panels=[panel]))


def test_a_chosen_series_counts_two_scans_and_all_series_one() -> None:
    """The chosen series' second read counts at save (design, *`series_changed`*), and is
    one per metric and service, whichever series of it a panel chose."""
    panels = [
        _series() | {"id": "p_eu"},
        _series(series="42") | {"id": "p_us"},
        _series(series=None) | {"id": "p_all"},
        _series(series=None, aggregation="mean") | {"id": "p_mean"},
    ]
    compiled = compiling.compile_definition(_accepted(_document(panels=panels)).definition)

    reports = compiling.ScanKey(
        reader=compiling.METRIC_REPORTS, service="checkout", metric="orders_processed", shape="sum"
    )
    assert compiled.worst_case_scans == 5
    assert compiled.scans.count(reports) == 1
    assert [panel.fallback for panel in compiled.panels] == [reports, reports, None, None]


def test_a_picked_metric_series_and_its_second_read_resolve_together() -> None:
    panels = [_series(service={"picker": True})]
    compiled = compiling.compile_definition(
        _accepted(_document(panels=panels, service_picker=True)).definition
    )
    [panel] = compiled.panels

    scan = compiling.resolve(panel.scan, picked="cart")
    fallback = compiling.resolve(panel.fallback, picked="cart")
    assert scan is not None and fallback is not None
    assert (scan.service, fallback.service, scan.series_hash) == ("cart", "cart", A_HASH)
    assert compiling.resolve(panel.fallback, picked=None) is None
    assert compiled.uses_picker is True


def test_a_metric_series_naming_a_service_lists_it_for_showing_this_service() -> None:
    compiled = compiling.compile_definition(
        _accepted(_document(panels=[_series(service="payments")])).definition
    )
    assert compiled.service_names == ["payments"]


def _known_panels() -> list[Any]:
    return list(definition.KnownPanel.__args__)


# --------------------------------------------------------------- AC38, AC80

PICKED: Final = {"picker": True}
HOST_METRICS: Final = (
    "system.cpu.utilization",
    "system.memory.utilization",
    "system.filesystem.usage",
)
# The spec's *The templates*, written out here rather than read from `templates.py`: each
# panel as its kind, width and the settings the table names, in order, then the worst
# case of reads and how many of them are raw.
SPEC_TEMPLATES: Final[dict[str, tuple[str, list[tuple[str, int, dict[str, Any]]], int, int]]] = {
    templates.START_FROM_OVERVIEW: (
        "Start from Overview",
        [
            *(("project_trend", 4, {"signal": signal}) for signal in SIGNALS),
            ("project_number", 3, {"measure": "service_count"}),
            ("project_number", 3, {"measure": "requests_per_minute"}),
            ("project_number", 3, {"measure": "error_rate"}),
            ("project_number", 3, {"measure": "p95_ms"}),
            ("services_table", 12, {"order": "worst", "rows": 10}),
        ],
        3,
        0,
    ),
    templates.SERVICE_HEALTH: (
        "Service health",
        [
            *(("service_trend", 4, {"service": PICKED, "signal": signal}) for signal in SIGNALS),
            ("service_number", 3, {"service": PICKED, "measure": "p95_ms", "compare": True}),
            ("open_findings", 3, {"service": PICKED}),
            ("endpoints_table", 6, {"service": PICKED, "order": "slowest", "rows": 10}),
            ("error_groups_table", 12, {"service": PICKED, "rows": 10}),
        ],
        5,
        1,
    ),
    templates.INFRASTRUCTURE: (
        "Infrastructure",
        [("top_nodes_trend", 4, {"metric": metric, "count": 5}) for metric in HOST_METRICS],
        3,
        3,
    ),
}


def test_fr39_the_templates_are_offered_in_the_specs_order() -> None:
    assert [(key, name) for key, (name, _) in templates.TEMPLATES.items()] == [
        (key, name) for key, (name, *_) in SPEC_TEMPLATES.items()
    ]


@pytest.mark.parametrize("template_id", list(SPEC_TEMPLATES))
def test_ac80_each_template_holds_exactly_the_specs_panels_and_passes_the_validator(
    template_id: str,
) -> None:
    _, panels, scans, raw = SPEC_TEMPLATES[template_id]
    _, document = templates.TEMPLATES[template_id]

    # Through the validator as a file would come: the same path, no way around it (FR7).
    parsed = _accepted(document.model_dump(mode="json"))
    held = [panel.model_dump(mode="json") for panel in parsed.definition.panels]
    assert [(panel["kind"], panel["width"]) for panel in held] == [
        (kind, width) for kind, width, _ in panels
    ]
    for panel, (_, _, settings) in zip(held, panels, strict=True):
        assert {name: panel["settings"][name] for name in settings} == settings, panel["id"]
    compiled = compiling.compile_for_save(parsed.definition)
    assert compiled.worst_case_scans == scans
    assert sum(key.reader in RAW_READERS for key in compiled.scans) == raw


def test_only_service_health_offers_the_picker() -> None:
    """*Service health* is one dashboard for every service (D7), and the others name none."""
    assert {
        key: document.definition.service_picker
        for key, (_, document) in templates.TEMPLATES.items()
    } == {
        templates.START_FROM_OVERVIEW: False,
        templates.SERVICE_HEALTH: True,
        templates.INFRASTRUCTURE: False,
    }
