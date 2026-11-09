"""The batch read's sequence, offline (F20, T2.12).

Postgres is replaced by the rows the batch would read, and ClickHouse by a repository
that answers the two statements Step 2's kinds run, so these run in `just check`. The
live suite, `test_dashboard_panels_live.py`, runs the same route against real stores.

AC15 (three panels of one service are one scan and one charge), AC19's offline paths
(the deadline passes mid-read; nothing starts with under a second left), AC20 (one
failing scan leaves the others `ok`, and `failed` carries no series), AC79 (a stored
definition over a lowered cap reads `over_limit` past it, with no retry promise), AC92
(under 1 s left), AC130 (two reads in one rounded minute name one window), AC140 (an
unset picker reads `needs_service` and runs no scan), FR8 (an unknown stored kind), FR16
(`not_in_project` against `empty`), NFR10 (a support view moves no "last opened"), the
commit before any scan (design section 5, step 4), and the batch's log line.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Final, cast

import pytest

from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.sessions import Impersonation
from nexovue_api.dashboardpanels import service
from nexovue_api.dashboardpanels.service import BatchRequest, DraftRequest, PanelReads
from nexovue_api.dashboards import queries as dashboard_queries
from nexovue_api.dashboards import ranges
from nexovue_api.dashboards import service as dashboards
from nexovue_api.dashboards.definition import Definition
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.ratelimit.charge import Refused
from nexovue_api.ratelimit.policy import PANEL_READ_DEADLINE_SECONDS
from nexovue_api.services import queries as service_queries
from nexovue_api.wire import to_iso_millis

NOW: Final = datetime(2026, 11, 8, 10, 0, 30, tzinfo=UTC)
ORG: Final = uuid.uuid4()
PROJECT: Final = uuid.uuid4()
DASHBOARD: Final = uuid.uuid4()
SERVICE_STATEMENT: Final = service_queries.trend_sql(one_service=True)


def _row(minute: int, service: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "bucket": NOW.replace(second=0) - timedelta(minutes=60 - minute),
        "request_count": 120,
        "error_count": 6,
        "latency_ms": [10.0, 40.0, 90.0],
    }
    if service is not None:
        row["service_name"] = service
    return row


class _Repository:
    """Answers the overview trend and one service's trend; counts every statement."""

    def __init__(self, *, failing: set[str] | None = None, hold: asyncio.Event | None = None):
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.bounds: list[int] = []
        self.committed_first: list[bool] = []
        self.session: _Session | None = None
        self._failing = failing or set()
        self._hold = hold

    def bounded_by(self, seconds: int) -> _Repository:
        self.bounds.append(seconds)
        return self

    async def query(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        self.statements.append((sql, parameters))
        self.committed_first.append(self.session is not None and self.session.commits > 0)
        if self._hold is not None:
            await self._hold.wait()
        service_name = parameters.get("service_name")
        if service_name in self._failing:
            raise RuntimeError("the store broke")
        if sql == SERVICE_STATEMENT:
            if service_name == "quiet":
                return []
            return [_row(minute, service_name) for minute in range(3)]
        return [_row(minute) for minute in range(5)]


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Charge:
    def __init__(self, places: int | None = None) -> None:
        self.claims = 0
        self._places = places

    async def claim(self) -> Refused | None:
        self.claims += 1
        if self._places is not None and self.claims > self._places:
            return Refused(policy="telemetry", retry_after_seconds=12)
        return None


def _panel(panel_id: str, kind: str = "service_trend", **settings: Any) -> dict[str, Any]:
    return {
        "id": panel_id,
        "kind": kind,
        "title": None,
        "width": 4,
        "height": "medium",
        "settings": settings,
    }


def _definition(*panels: dict[str, Any], picker: bool = False) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "refresh_seconds": 60,
        "default_range": None,
        "service_picker": picker,
        "links": [],
        "panels": list(panels),
    }


def _identity(support_view: bool = False) -> Identity:
    impersonation = (
        Impersonation(
            impersonation_id=str(uuid.uuid4()),
            operator_label="support",
            reason="test",
            grant_id=str(uuid.uuid4()),
            until=NOW + timedelta(hours=1),
        )
        if support_view
        else None
    )
    return cast(
        Identity,
        SimpleNamespace(
            org_id=ORG,
            user=SimpleNamespace(id=uuid.uuid4()),
            organization=SimpleNamespace(id=ORG),
            impersonation=impersonation,
        ),
    )


class _Batch:
    """One dashboard row, its project and its services' last-seen times, as Postgres has them."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        definition: dict[str, Any],
        *,
        last_seen: dict[str, datetime] | None = None,
        last_viewed_at: datetime | None = None,
    ) -> None:
        self.dashboard = SimpleNamespace(
            id=DASHBOARD, org_id=ORG, definition=definition, last_viewed_at=last_viewed_at
        )
        self.reads = PanelReads.for_install(shared_by_organizations=False)
        self.session = _Session()
        seen = last_seen if last_seen is not None else {"checkout": NOW, "payment": NOW}

        async def readable_dashboard(*_: Any, **__: Any) -> Any:
            return self.dashboard

        async def readable_project(*_: Any, **__: Any) -> Any:
            return SimpleNamespace(id=PROJECT)

        async def last_seen_of(*_: Any, services: list[str], **__: Any) -> dict[str, datetime]:
            return {name: seen[name] for name in services if name in seen}

        monkeypatch.setattr(dashboards, "readable_dashboard", readable_dashboard)
        monkeypatch.setattr(dashboards, "readable_project", readable_project)
        monkeypatch.setattr(dashboard_queries, "last_seen_of", last_seen_of)

    async def read(
        self,
        repository: _Repository,
        *,
        charge: _Charge | None = None,
        identity: Identity | None = None,
        arrived_at: float | None = None,
        service_name: str | None = None,
        panel_ids: frozenset[str] | None = None,
        now: datetime = NOW,
        disconnected: bool = False,
    ) -> dict[str, Any]:
        repository.session = self.session

        async def gone() -> bool:
            return disconnected

        answer = await service.read_panels(
            cast(Any, self.session),
            identity=identity or _identity(),
            repository=cast(TenantScopedRepository, repository),
            reads=self.reads,
            charge=charge or _Charge(),
            asked=BatchRequest(
                dashboard_id=DASHBOARD,
                project_id=PROJECT,
                range_id="1h",
                from_time=None,
                to_time=None,
                service=service_name,
                panel_ids=panel_ids,
            ),
            arrived_at=arrived_at,
            disconnected=gone,
            correlation_id="corr-1",
            now=now,
        )
        return answer.model_dump(mode="json")

    async def preview(
        self,
        repository: _Repository,
        definition: dict[str, Any],
        *,
        charge: _Charge | None = None,
        panel_ids: frozenset[str] | None = None,
        service_name: str | None = None,
    ) -> dict[str, Any]:
        """A draft, read through `POST /preview`'s service: no saved row is named."""
        repository.session = self.session

        async def present() -> bool:
            return False

        answer = await service.preview_panels(
            cast(Any, self.session),
            identity=_identity(),
            repository=cast(TenantScopedRepository, repository),
            reads=self.reads,
            charge=charge or _Charge(),
            asked=DraftRequest(
                project_id=PROJECT,
                range_id="1h",
                from_time=None,
                to_time=None,
                service=service_name,
                panel_ids=panel_ids,
                definition=Definition.model_validate(definition),
            ),
            arrived_at=None,
            disconnected=present,
            correlation_id="corr-2",
            now=NOW,
        )
        return answer.model_dump(mode="json")


# ----------------------------------------------------------------------- AC15


async def test_ac15_three_panels_on_one_service_run_one_scan_and_spend_one_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            *(
                _panel(f"p_{signal}", service="checkout", signal=signal)
                for signal in ("rate", "errors", "latency")
            )
        ),
    )
    repository, charge = _Repository(), _Charge()

    answer = await batch.read(repository, charge=charge)

    assert len(repository.statements) == 1
    assert charge.claims == 1
    assert {panel["state"] for panel in answer["panels"].values()} == {"ok"}
    latency = answer["panels"]["p_latency"]["series"]
    assert [line["label"] for line in latency] == ["p50_ms", "p95_ms", "p99_ms"]
    assert answer["panels"]["p_rate"]["series"][0]["points"][0][1] == pytest.approx(120.0)


async def test_every_statement_runs_under_the_fixed_shared_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(monkeypatch, _definition(_panel("p_1", "project_trend", signal="rate")))
    repository = _Repository()

    await batch.read(repository)

    assert repository.bounds == [15]


async def test_postgres_is_committed_before_any_scan_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_1", "project_trend", signal="rate"),
            _panel("p_2", service="checkout", signal="rate"),
        ),
    )
    repository = _Repository()

    await batch.read(repository)

    assert repository.committed_first == [True, True]


# ----------------------------------------------------------------- AC20, FR8


async def test_ac20_a_failing_scan_leaves_the_others_ok_and_carries_no_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_ok", service="checkout", signal="rate"),
            _panel("p_broken", service="payment", signal="rate"),
            _panel("p_project", "project_trend", signal="errors"),
        ),
    )

    answer = await batch.read(_Repository(failing={"payment"}))

    assert answer["panels"]["p_ok"]["state"] == "ok"
    assert answer["panels"]["p_project"]["state"] == "ok"
    assert answer["panels"]["p_broken"] == {"state": "failed", "kind": "service_trend"}


async def test_fr8_an_unknown_stored_kind_reads_unknown_and_the_rest_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            {"id": "p_gone", "kind": "retired", "width": 4, "height": "medium", "settings": {}},
            _panel("p_ok", service="checkout", signal="rate"),
        ),
    )

    answer = await batch.read(_Repository())

    assert answer["panels"]["p_gone"]["state"] == "unknown_kind"
    assert answer["panels"]["p_ok"]["state"] == "ok"


async def test_a_note_is_not_in_the_batch_the_console_draws_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition({**_panel("p_note", "note", text="Check the queue"), "height": "small"}),
    )
    repository = _Repository()

    answer = await batch.read(repository)

    assert answer["panels"] == {}
    assert repository.statements == []


# ----------------------------------------------------------------------- FR16


async def test_fr16_no_record_reads_not_in_project_and_a_quiet_known_service_reads_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = NOW - timedelta(hours=3)
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_quiet", service="quiet", signal="rate"),
            _panel("p_stranger", service="stranger", signal="rate"),
        ),
        last_seen={"quiet": seen},
    )

    class Silent(_Repository):
        async def query(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
            await super().query(sql, parameters)
            return []

    answer = await batch.read(Silent())

    assert answer["panels"]["p_quiet"]["state"] == "empty"
    assert answer["panels"]["p_quiet"]["last_seen_at"] == to_iso_millis(seen)
    assert answer["panels"]["p_stranger"] == {"state": "not_in_project", "kind": "service_trend"}


async def test_a_project_panel_with_nothing_reads_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _Batch(monkeypatch, _definition(_panel("p_1", "project_trend", signal="rate")))

    class Silent(_Repository):
        async def query(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
            return []

    answer = await batch.read(Silent())

    assert answer["panels"]["p_1"]["state"] == "empty"


# ---------------------------------------------------------------------- AC140


async def test_ac140_an_unset_picker_reads_needs_service_and_runs_no_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(_panel("p_1", service={"picker": True}, signal="rate"), picker=True),
    )
    repository = _Repository()

    unset = await batch.read(repository)
    picked = await batch.read(repository, service_name="checkout")

    assert unset["panels"]["p_1"] == {"state": "needs_service", "kind": "service_trend"}
    assert picked["panels"]["p_1"]["state"] == "ok"
    assert [parameters["service_name"] for _, parameters in repository.statements] == ["checkout"]


async def test_a_picked_service_and_a_named_one_share_a_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_picked", service={"picker": True}, signal="rate"),
            _panel("p_named", service="checkout", signal="errors"),
            picker=True,
        ),
    )
    repository = _Repository()

    await batch.read(repository, service_name="checkout")

    assert len(repository.statements) == 1


# ----------------------------------------------------------------------- AC79


async def test_ac79_past_a_lowered_cap_panels_read_over_limit_with_no_retry_promise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "MAX_SCANS_PER_READ", 2)
    batch = _Batch(
        monkeypatch,
        _definition(
            *(_panel(f"p_{index}", service=f"svc-{index}", signal="rate") for index in range(3))
        ),
        last_seen={f"svc-{index}": NOW for index in range(3)},
    )
    repository = _Repository()

    answer = await batch.read(repository)

    assert [answer["panels"][f"p_{index}"]["state"] for index in range(3)] == [
        "ok",
        "ok",
        "over_limit",
    ]
    assert answer["panels"]["p_2"] == {"state": "over_limit", "kind": "service_trend"}
    assert len(repository.statements) == 2


# ----------------------------------------------------------------- AC17 offline


async def test_refused_scans_are_the_lowest_panels_and_carry_their_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            *(_panel(f"p_{index}", service=f"svc-{index}", signal="rate") for index in range(6))
        ),
        last_seen={f"svc-{index}": NOW for index in range(6)},
    )

    answer = await batch.read(_Repository(), charge=_Charge(places=3))

    states = [answer["panels"][f"p_{index}"]["state"] for index in range(6)]
    assert states.count("ok") == 3 and states.count("refused") == 3
    assert {answer["panels"][f"p_{index}"].get("retry_after_seconds") for index in range(6)} == {
        None,
        12,
    }


# ----------------------------------------------------------------- AC19, AC92


async def test_ac92_with_under_a_second_left_nothing_starts_and_panels_read_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(monkeypatch, _definition(_panel("p_1", service="checkout", signal="rate")))
    repository = _Repository()
    arrived = batch.reads.shared.now() - (PANEL_READ_DEADLINE_SECONDS - 0.5)

    answer = await batch.read(repository, arrived_at=arrived)

    assert answer["panels"]["p_1"] == {"state": "timed_out", "kind": "service_trend"}
    assert repository.statements == []


async def test_ac19_when_the_deadline_passes_mid_read_the_batch_answers_with_what_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(_panel("p_slow", service="checkout", signal="rate")),
    )
    stuck = asyncio.Event()
    arrived = batch.reads.shared.now() - (PANEL_READ_DEADLINE_SECONDS - 1.3)

    answer = await asyncio.wait_for(
        batch.read(_Repository(hold=stuck), arrived_at=arrived), timeout=5
    )

    assert answer["panels"]["p_slow"]["state"] == "timed_out"
    stuck.set()


async def test_a_disconnected_reader_starts_no_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _Batch(monkeypatch, _definition(_panel("p_1", service="checkout", signal="rate")))
    repository = _Repository()

    await batch.read(repository, disconnected=True)

    assert repository.statements == []


# ---------------------------------------------------------------------- AC130


async def test_ac130_two_reads_in_one_rounded_minute_name_one_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(monkeypatch, _definition(_panel("p_1", "project_trend", signal="rate")))
    first = await batch.read(_Repository(), now=NOW.replace(second=5))
    second = await batch.read(_Repository(), now=NOW.replace(second=55))

    assert (first["window_start"], first["window_end"]) == (
        second["window_start"],
        second["window_end"],
    )
    assert first["window_end"] == "2026-11-08T10:01:00.000Z"


def test_a_live_range_ends_at_the_next_whole_minute_and_fixed_times_stand() -> None:
    on_the_minute = NOW.replace(second=0, microsecond=0)

    assert ranges.window_for("1h", None, None, now=on_the_minute) == (
        on_the_minute - timedelta(hours=1),
        on_the_minute,
    )
    fixed = (NOW - timedelta(hours=2), NOW - timedelta(hours=1))
    assert ranges.window_for(None, *fixed, now=NOW) == fixed


# ----------------------------------------------------------------- stamping


async def test_a_read_stamps_last_opened_at_most_hourly(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(_panel("p_1", "project_trend", signal="rate")),
        last_viewed_at=NOW - timedelta(minutes=30),
    )

    await batch.read(_Repository())
    assert batch.dashboard.last_viewed_at == NOW - timedelta(minutes=30)

    await batch.read(_Repository(), now=NOW + timedelta(minutes=31))
    assert batch.dashboard.last_viewed_at == NOW + timedelta(minutes=31)


async def test_nfr10_a_support_view_reads_and_moves_no_last_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(monkeypatch, _definition(_panel("p_1", "project_trend", signal="rate")))

    answer = await batch.read(_Repository(), identity=_identity(support_view=True))

    assert answer["panels"]["p_1"]["state"] == "ok"
    assert batch.dashboard.last_viewed_at is None


async def test_panels_names_the_only_panels_read(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_1", service="checkout", signal="rate"),
            _panel("p_2", service="payment", signal="rate"),
        ),
    )
    repository = _Repository()

    answer = await batch.read(repository, panel_ids=frozenset({"p_2"}))

    assert list(answer["panels"]) == ["p_2"]
    assert [parameters["service_name"] for _, parameters in repository.statements] == ["payment"]


# ------------------------------------------------------------------ the log


async def test_the_batch_logs_one_line_with_its_counts_and_no_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_rate", service="checkout", signal="rate"),
            _panel("p_errors", service="checkout", signal="errors"),
            _panel("p_broken", service="payment", signal="rate"),
        ),
    )
    lines: list[dict[str, Any]] = []

    class Recording:
        def info(self, event: str, **fields: Any) -> None:
            lines.append({"event": event, **fields})

    monkeypatch.setattr(service, "logger", Recording())
    await batch.read(_Repository(failing={"payment"}))

    [line] = [line for line in lines if line["event"] == "dashboard batch read"]
    assert {
        key: line[key]
        for key in (
            "dashboard_id",
            "panels",
            "scans_run",
            "merged",
            "kept_answers_used",
            "joined",
            "refused",
            "timed_out",
            "failed",
            "correlation_id",
        )
    } == {
        "dashboard_id": str(DASHBOARD),
        "panels": 3,
        "scans_run": 1,
        "merged": 1,
        "kept_answers_used": 0,
        "joined": 0,
        "refused": 0,
        "timed_out": 0,
        "failed": 1,
        "correlation_id": "corr-1",
    }
    assert isinstance(line["total_ms"], int)
    assert "checkout" not in str(line) and "payment" not in str(line)


# ------------------------------------------------------- T3.1, AC135: a draft


def _three_scans() -> dict[str, Any]:
    return _definition(
        _panel("p_project", "project_trend", signal="rate"),
        _panel("p_checkout", service="checkout", signal="rate"),
        _panel("p_checkout_errors", service="checkout", signal="errors"),
        _panel("p_payment", service="payment", signal="latency"),
    )


async def test_ac135_a_draft_charges_as_the_same_saved_read_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three distinct scans: the request's place pays for one, and each later one spends
    a place, whether the definition is saved or a draft (BR6)."""
    saved_charge, draft_charge = _Charge(), _Charge()
    saved, draft = _Repository(), _Repository()

    read = await _Batch(monkeypatch, _three_scans()).read(saved, charge=saved_charge)
    # A process of its own, so no answer is kept from the saved read.
    previewed = await _Batch(monkeypatch, _three_scans()).preview(
        draft, _three_scans(), charge=draft_charge
    )

    assert draft_charge.claims == saved_charge.claims == 3
    assert len(draft.statements) == len(saved.statements) == 3

    def drawn(answer: dict[str, Any]) -> dict[str, Any]:
        return {
            panel_id: {key: value for key, value in panel.items() if key != "read_at"}
            for panel_id, panel in answer["panels"].items()
        }

    assert drawn(previewed) == drawn(read)


async def test_a_draft_shares_the_answers_kept_from_a_saved_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One process: the draft's scans were just read for the saved dashboard, so they
    cost the store nothing, and the reader nothing (BR6)."""
    batch = _Batch(monkeypatch, _three_scans())
    repository, charge = _Repository(), _Charge()

    await batch.read(repository)
    await batch.preview(repository, _three_scans(), charge=charge)

    assert len(repository.statements) == 3
    assert charge.claims == 0


async def test_ac135_apply_reads_only_the_panel_it_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(monkeypatch, _definition())
    repository, charge = _Repository(), _Charge()

    answer = await batch.preview(
        repository, _three_scans(), charge=charge, panel_ids=frozenset({"p_payment"})
    )

    assert list(answer["panels"]) == ["p_payment"]
    assert answer["panels"]["p_payment"]["state"] == "ok"
    assert [parameters["service_name"] for _, parameters in repository.statements] == ["payment"]
    assert charge.claims == 1


async def test_a_preview_names_no_saved_dashboard_and_stamps_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(monkeypatch, _definition())

    async def no_dashboard(*_: Any, **__: Any) -> Any:
        raise AssertionError("a draft reads no saved dashboard")

    monkeypatch.setattr(dashboards, "readable_dashboard", no_dashboard)
    repository = _Repository()

    answer = await batch.preview(repository, _three_scans())

    assert batch.dashboard.last_viewed_at is None
    # The connection is still let go before ClickHouse is asked anything.
    assert repository.committed_first == [True, True, True]
    assert answer["correlation_id"] == "corr-2"


async def test_fr7_a_draft_past_the_scan_cap_is_refused_before_any_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same check a save makes, with no path around it (FR7): here with the cap
    lowered to 2, since Step 3's kinds cost at most one scan a panel."""
    from nexovue_api.dashboards import compile as compile_module

    monkeypatch.setattr(compile_module, "MAX_SCANS_PER_READ", 2)
    batch = _Batch(monkeypatch, _definition())
    repository, charge = _Repository(), _Charge()

    with pytest.raises(InvalidRequestError) as refused:
        await batch.preview(repository, _three_scans(), charge=charge)

    assert refused.value.extensions["scans"] == 3
    assert repository.statements == []
    assert charge.claims == 0


async def test_a_previewed_picker_reads_the_service_named_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(monkeypatch, _definition())
    draft = _definition(_panel("p_picked", service={"picker": True}, signal="rate"), picker=True)
    repository = _Repository()

    unset = await batch.preview(repository, draft)
    picked = await batch.preview(repository, draft, service_name="checkout")

    assert unset["panels"]["p_picked"]["state"] == "needs_service"
    assert picked["panels"]["p_picked"]["state"] == "ok"
    assert [parameters["service_name"] for _, parameters in repository.statements] == ["checkout"]


async def test_a_preview_logs_its_line_as_a_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _Batch(monkeypatch, _definition())
    lines: list[dict[str, Any]] = []

    class Recording:
        def info(self, event: str, **fields: Any) -> None:
            lines.append({"event": event, **fields})

    monkeypatch.setattr(service, "logger", Recording())
    await batch.preview(_Repository(), _three_scans())

    [line] = [line for line in lines if line["event"] == "dashboard batch read"]
    assert line["dashboard_id"] is None
    assert line["draft"] is True
    assert line["scans_run"] == 3
