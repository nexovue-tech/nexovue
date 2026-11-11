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
from nexovue_api.dashboards.limits import DashboardLimits
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.deploys import service as deploys
from nexovue_api.deploys.service import EMPTY_DEPLOY_HISTORY
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.ratelimit.charge import Refused
from nexovue_api.ratelimit.policy import PANEL_READ_DEADLINE_SECONDS
from nexovue_api.releases import service as releases
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
        limits: DashboardLimits | None = None,
        checks: dict[str, list[str]] | None = None,
    ) -> None:
        self.dashboard = SimpleNamespace(
            id=DASHBOARD, org_id=ORG, definition=definition, last_viewed_at=last_viewed_at
        )
        self.reads = PanelReads.for_install(shared_by_organizations=False, limits=limits)
        self.session = _Session()
        seen = last_seen if last_seen is not None else {"checkout": NOW, "payment": NOW}

        async def readable_dashboard(*_: Any, **__: Any) -> Any:
            return self.dashboard

        async def readable_project(*_: Any, **__: Any) -> Any:
            return SimpleNamespace(id=PROJECT)

        async def last_seen_of(*_: Any, services: list[str], **__: Any) -> dict[str, datetime]:
            return {name: seen[name] for name in services if name in seen}

        async def checks_named(*_: Any, names: list[str], **__: Any) -> dict[str, list[str]]:
            known = checks or {}
            return {name: known[name] for name in names if name in known}

        monkeypatch.setattr(dashboards, "readable_dashboard", readable_dashboard)
        monkeypatch.setattr(dashboards, "readable_project", readable_project)
        monkeypatch.setattr(dashboard_queries, "last_seen_of", last_seen_of)
        monkeypatch.setattr(dashboard_queries, "checks_named", checks_named)

        async def no_deploys(*_: Any, **__: Any) -> Any:
            return EMPTY_DEPLOY_HISTORY

        async def no_coverage(*_: Any, **__: Any) -> Any:
            return None

        # An errors table's rows carry the deploys and coverage Postgres holds.
        monkeypatch.setattr(deploys, "history_for_window", no_deploys)
        monkeypatch.setattr(releases, "coverage_book", no_coverage)

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

    async def read_window(self, repository: Any, *, days: int) -> dict[str, Any]:
        """A fixed window of `days`, ending an hour before the clock this suite pins."""

        async def present() -> bool:
            return False

        end = NOW.replace(second=0) - timedelta(hours=1)
        answer = await service.read_panels(
            cast(Any, self.session),
            identity=_identity(),
            repository=cast(TenantScopedRepository, repository),
            reads=self.reads,
            charge=_Charge(),
            asked=BatchRequest(
                dashboard_id=DASHBOARD,
                project_id=PROJECT,
                range_id=None,
                from_time=end - timedelta(days=days),
                to_time=end,
                service=None,
                panel_ids=None,
            ),
            arrived_at=None,
            disconnected=present,
            correlation_id="corr-w",
            now=NOW,
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
    batch = _Batch(
        monkeypatch,
        _definition(
            *(_panel(f"p_{index}", service=f"svc-{index}", signal="rate") for index in range(3))
        ),
        last_seen={f"svc-{index}": NOW for index in range(3)},
        limits=DashboardLimits(max_scans_per_read=2),
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
    """The same check a save makes, with no path around it (FR7): here at the install's
    own lowered cap of 2 (T5.6c)."""
    batch = _Batch(monkeypatch, _definition(), limits=DashboardLimits(max_scans_per_read=2))
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


# ------------------------------------------------- Step 4: the panel boundary, the filling bucket


def _served(value: Any) -> Any:
    from nexovue_api.dashboardpanels.shared_reads import Answer, Served

    return Served(answer=Answer(value=value, rows=1, read_at=datetime.now(UTC)), source="read")


def _stored_panel(kind: str, settings: dict[str, Any]) -> Any:
    from nexovue_api.dashboards.definition import parse_stored

    raw = {"id": "p_1", "kind": kind, "title": None, "width": 4, "height": "medium"}
    [panel] = parse_stored({"panels": [raw | {"settings": settings}]}).panels
    return panel


def test_an_answer_a_panel_cannot_draw_fails_that_panel_alone() -> None:
    """The panel boundary (FR14): a reader's answer of the wrong shape is that panel's
    `failed`, never a 500 for the page."""
    from nexovue_api.dashboardpanels.scans import NodeLine
    from nexovue_api.dashboards.compile import scan_of

    panel = _stored_panel("project_trend", {"signal": "rate"})
    key = scan_of(panel)
    assert key is not None, "a project trend panel reads one scan"
    entry = service._Entry(panel, cast(Any, SimpleNamespace(scan=key)), scan=key)
    plan = service._Plan(entries=[entry], scans=(key,), over_limit=frozenset(), window=(NOW, NOW))
    outcomes = {key: _served(NodeLine(bucket_seconds=60, points=[]))}

    result = service._result(entry, plan, outcomes, {}, None)

    assert result is not None and result.state == "failed"


def test_only_a_bucket_that_has_not_ended_is_marked_filling() -> None:
    newest = datetime(2026, 11, 9, 10, 3)  # noqa: DTZ001 - the store hands buckets back without a zone
    assert service._filling(newest, 60, datetime(2026, 11, 9, 10, 3, 30, tzinfo=UTC)) == (
        newest.replace(tzinfo=UTC)
    )
    assert service._filling(newest, 60, datetime(2026, 11, 9, 10, 4, tzinfo=UTC)) is None
    # A fixed window marks nothing.
    assert service._filling(newest, 60, None) is None


# ------------------------------------------------------------ Step 5's kinds offline

WINDOW_START: Final = datetime(2026, 11, 8, 9, 1, tzinfo=UTC)  # "1h" at NOW, rounded up
PREVIOUS_START: Final = WINDOW_START - timedelta(hours=1)


class _Store:
    """Answers any statement by name, through `query` and `query_one` alike, and records
    each statement with its parameters."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def bounded_by(self, seconds: int) -> _Store:
        return self

    async def query(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        self.statements.append((sql, parameters))
        answer = self.answers.get(sql, [])
        rows: list[dict[str, Any]] = answer(parameters) if callable(answer) else answer
        return rows

    async def query_one(self, sql: str, parameters: dict[str, Any]) -> dict[str, Any] | None:
        rows = await self.query(sql, parameters)
        return rows[0] if rows else None

    def ran(self, sql: str) -> list[dict[str, Any]]:
        return [parameters for statement, parameters in self.statements if statement == sql]


def _totals(*, first_minute: datetime, services: int = 3) -> dict[str, Any]:
    return {
        "service_count": services,
        "request_count": 6_000,
        "error_count": 60,
        "latency_ms": [10.0, 40.0, 90.0],
        "first_minute": first_minute.replace(tzinfo=None),
    }


def _project_number(**settings: Any) -> dict[str, Any]:
    panel = _panel(
        "p_number", "project_number", **({"measure": "p95_ms", "compare": True} | settings)
    )
    return panel | {"height": "small"}


def _overview_by_window(previous_first: datetime | None) -> Any:
    """The window's totals, and the previous window's starting at `previous_first`, or
    nothing reported there at all."""

    def answer(parameters: dict[str, Any]) -> list[dict[str, Any]]:
        if parameters["from_time"] == WINDOW_START:
            return [_totals(first_minute=WINDOW_START) | {"latency_ms": [10.0, 60.0, 90.0]}]
        if previous_first is None:
            return [_totals(first_minute=PREVIOUS_START, services=0)]
        return [_totals(first_minute=previous_first)]

    return answer


async def test_t5_3_a_covered_previous_window_compares(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _Batch(monkeypatch, _definition(_project_number()))
    store = _Store({service_queries.OVERVIEW_SQL: _overview_by_window(PREVIOUS_START)})

    number = (await batch.read(cast(Any, store)))["panels"]["p_number"]

    assert number["state"] == "ok"
    assert number["number"] == {
        "measure": "p95_ms",
        "value": 60.0,
        "previous": 40.0,
        "previous_coverage": 1.0,
    }
    # Two scans: the window's, and the hour before it.
    assert [read["from_time"] for read in store.ran(service_queries.OVERVIEW_SQL)] == [
        WINDOW_START,
        PREVIOUS_START,
    ]


@pytest.mark.parametrize(
    ("previous_first", "coverage"),
    [(None, None), (PREVIOUS_START + timedelta(minutes=30), 0.5)],
    ids=["no-earlier-window", "half-covered"],
)
async def test_ac73_a_partly_covered_previous_window_gives_no_comparison(
    monkeypatch: pytest.MonkeyPatch, previous_first: datetime | None, coverage: float | None
) -> None:
    """A service ten minutes old has no earlier hour; a half-covered one is not enough
    history either. The number still reads, and the console says "Not enough history to
    compare" with no arrow, because `previous` is null."""
    batch = _Batch(monkeypatch, _definition(_project_number()))
    store = _Store({service_queries.OVERVIEW_SQL: _overview_by_window(previous_first)})

    number = (await batch.read(cast(Any, store)))["panels"]["p_number"]["number"]

    assert number["value"] == 60.0
    assert number["previous"] is None
    assert number["previous_coverage"] == coverage


async def test_a_project_number_over_a_window_with_no_service_reads_empty_never_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(monkeypatch, _definition(_project_number(compare=False)))
    store = _Store({service_queries.OVERVIEW_SQL: [_totals(first_minute=WINDOW_START, services=0)]})

    number = (await batch.read(cast(Any, store)))["panels"]["p_number"]

    assert number["state"] == "empty"
    assert "number" not in number


async def test_a_service_number_tells_no_record_from_a_quiet_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_known", "service_number", service="checkout", measure="p95_ms", compare=False)
            | {"height": "small"},
            _panel("p_unknown", "service_number", service="ghost", measure="p95_ms", compare=False)
            | {"height": "small"},
        ),
        last_seen={"checkout": NOW - timedelta(hours=3)},
    )

    answer = await batch.read(cast(Any, _Store({})))

    assert answer["panels"]["p_known"]["state"] == "empty"
    assert answer["panels"]["p_known"]["last_seen_at"] is not None
    assert answer["panels"]["p_unknown"] == {"state": "not_in_project", "kind": "service_number"}


async def test_a_table_shows_its_own_first_rows_of_one_shared_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranked = [
        {
            "service_name": f"svc-{index}",
            "request_count": 100 - index,
            "error_count": 20 - index,
            "latency_ms": [1.0, 2.0, 3.0],
            "first_minute": WINDOW_START.replace(tzinfo=None),
        }
        for index in range(20)
    ]
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_five", "services_table", order="worst", rows=5),
            _panel("p_all", "services_table", order="worst", rows=20),
        ),
    )
    store = _Store({service_queries.services_sql(one_service=False): ranked})

    answer = await batch.read(cast(Any, store))

    assert len(store.statements) == 1
    assert store.statements[0][1]["limit"] == 20
    five = answer["panels"]["p_five"]["rows"]
    assert [row["service_name"] for row in five] == [f"svc-{index}" for index in range(5)]
    assert len(answer["panels"]["p_all"]["rows"]) == 20


async def test_ac99_a_service_errors_table_with_nothing_says_when_the_service_was_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console says "No errors in this window" only when the service was seen in it,
    and "checkout sent nothing in this window" otherwise: both from `last_seen_at`."""
    seen = NOW - timedelta(minutes=5)
    batch = _Batch(
        monkeypatch,
        _definition(_panel("p_errors", "error_groups_table", service="checkout", rows=10)),
        last_seen={"checkout": seen},
    )

    panel = (await batch.read(cast(Any, _Store({}))))["panels"]["p_errors"]

    assert panel["state"] == "empty"
    assert panel["last_seen_at"] == to_iso_millis(seen)


def _check_rows(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    start = int(WINDOW_START.timestamp()) * 1_000_000_000
    return [
        {
            "check_id": parameters["check_id"],
            "bucket_nanos": start + minute * 60_000_000_000,
            "runs": runs,
            "successful_runs": ok,
            "duration_points": ok,
            "mean_seconds": 0.25,
        }
        for minute, (runs, ok) in enumerate([(4, 4), (4, 3), (0, 0), (4, 0)])
    ]


async def test_ac22_a_check_not_in_the_project_reads_not_in_project_and_runs_no_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_missing", "check_trend", check="Login", signal="uptime"),
            _panel("p_twice", "check_trend", check="Checkout", signal="uptime"),
        ),
        checks={"Checkout": ["c-1", "c-2"]},
    )
    store = _Store({})

    answer = await batch.read(cast(Any, store))

    assert answer["panels"]["p_missing"] == {"state": "not_in_project", "kind": "check_trend"}
    assert answer["panels"]["p_twice"] == {"state": "ambiguous", "kind": "check_trend"}
    assert store.statements == []


async def test_ac95_a_check_runs_one_statement_bound_to_the_id_its_name_resolved_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexovue_api.synthetics import queries as synthetics_queries

    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_up", "check_trend", check="Login", signal="uptime"),
            _panel("p_time", "check_trend", check="Login", signal="duration"),
        ),
        checks={"Login": ["c-9"]},
    )
    store = _Store({synthetics_queries.CHECK_TREND_SQL: _check_rows})

    answer = await batch.read(cast(Any, store))

    assert [parameters["check_id"] for _, parameters in store.statements] == ["c-9"]
    uptime = answer["panels"]["p_up"]["series"][0]
    assert uptime["label"] == "uptime_percent"
    # A bucket with no run is a gap, never a zero; one whose runs all failed is 0%.
    assert [value for _, value in uptime["points"]] == [100.0, 75.0, 0.0]
    duration = answer["panels"]["p_time"]["series"][0]
    # Only buckets with a successful timed run have a duration.
    assert [value for _, value in duration["points"]] == [250.0, 250.0]


@pytest.mark.parametrize(
    ("kind", "settings", "limit"),
    [
        (
            "statement_trend",
            {"query_hash": "1", "server_address": "db", "server_port": 5432, "label": None},
            7,
        ),
        ("check_trend", {"check": "Login", "signal": "uptime"}, 30),
    ],
    ids=["seven-days", "thirty-days"],
)
async def test_ac23_a_raw_panel_over_its_window_reads_window_too_wide_and_runs_no_scan(
    monkeypatch: pytest.MonkeyPatch, kind: str, settings: dict[str, Any], limit: int
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_raw", kind, **settings), _panel("p_rollup", "project_trend", signal="rate")
        ),
        checks={"Login": ["c-9"]},
    )
    store = _Store({service_queries.OVERVIEW_TREND_SQL: [_row(minute) for minute in range(3)]})

    wide = await batch.read_window(cast(Any, store), days=limit + 1)
    fits = await batch.read_window(cast(Any, _Store({})), days=limit)

    assert wide["panels"]["p_raw"] == {
        "state": "window_too_wide",
        "kind": kind,
        "limit_days": limit,
    }
    assert wide["panels"]["p_rollup"]["state"] == "ok"
    assert [sql for sql, _ in store.statements] == [service_queries.OVERVIEW_TREND_SQL]
    assert fits["panels"]["p_raw"]["state"] == "empty"


async def test_fr17_a_plan_keeping_less_than_the_safety_limit_lowers_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The limit is the lesser of the reader's and the plan's raw retention (FR17)."""
    from nexovue_api.dashboards.schemas import PlanRetention

    batch = _Batch(
        monkeypatch,
        _definition(_panel("p_raw", "check_trend", check="Login", signal="uptime")),
        checks={"Login": ["c-9"]},
    )
    batch.reads = PanelReads.for_install(shared_by_organizations=True, hosted=True)

    async def plan_retention(*_: Any, **__: Any) -> PlanRetention:
        return PlanRetention(raw_days=3, derived_days=30)

    monkeypatch.setattr(dashboards, "plan_retention", plan_retention)

    answer = await batch.read_window(cast(Any, _Store({})), days=4)

    assert answer["panels"]["p_raw"] == {
        "state": "window_too_wide",
        "kind": "check_trend",
        "limit_days": 3,
    }


async def test_ac116_with_scans_off_every_scan_panel_is_paused_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _Batch(
        monkeypatch,
        _definition(
            _panel("p_chart", "project_trend", signal="rate"),
            _project_number(),
            _panel("p_note", "note", text="Check the queue first."),
            _panel("p_open", "open_findings", service=None) | {"height": "small"},
        ),
        limits=DashboardLimits(scans_enabled=False),
    )
    store, charge = _Store({}), _Charge()

    answer = await batch.read(cast(Any, store), charge=charge)

    assert answer["panels"] == {
        "p_chart": {"state": "paused_by_operator", "kind": "project_trend"},
        "p_number": {"state": "paused_by_operator", "kind": "project_number"},
    }
    assert store.statements == []
    assert charge.claims == 0


def _settings(**values: Any) -> Any:
    from environment import TEST_ENVIRONMENT

    from nexovue_api.settings import Settings

    return Settings.model_validate(
        {name.lower(): value for name, value in TEST_ENVIRONMENT.items()} | values
    )


def test_t5_6c_the_code_s_limits_fit_the_quota_and_hosted_keeps_them() -> None:
    from nexovue_api.dashboards.limits import CODE_LIMITS, DashboardLimitsError

    hosted = _settings(nexovue_deployment="hosted")
    assert DashboardLimits.from_settings(hosted) == CODE_LIMITS

    tuned = _settings(nexovue_deployment="hosted", dashboard_scans_at_once=2)
    with pytest.raises(DashboardLimitsError, match="DASHBOARD_SCANS_AT_ONCE"):
        DashboardLimits.from_settings(tuned)


@pytest.mark.parametrize(
    ("tuned", "refusal"),
    [
        ({"dashboard_max_scans_per_read": 46}, "reads a minute"),
        ({"dashboard_max_scans_per_read": 80, "dashboard_refresh_floor_seconds": 60}, None),
        (
            {"dashboard_max_scans_per_read": 91, "dashboard_refresh_floor_seconds": 60},
            "reads a minute",
        ),
        ({"dashboard_scans_at_once": 9}, "CLICKHOUSE_POOL_SIZE"),
        ({"dashboard_max_panels": 31}, "at most 30"),
        ({"dashboard_max_panels": 12, "dashboard_raw_window_days": 3}, None),
    ],
    ids=["scans-at-floor", "scans-at-slower-floor", "past-it", "connections", "panels", "lower"],
)
def test_t5_6c_a_self_hosted_set_that_breaks_the_quota_is_refused_at_start(
    tuned: dict[str, Any], refusal: str | None
) -> None:
    """At the 30 s floor, 45 scans is 90 a minute, one person's share at two processes;
    46 is past it. A slower floor admits more."""
    from nexovue_api.dashboards.limits import DashboardLimitsError

    settings = _settings(nexovue_deployment="self-hosted", **tuned)
    if refusal is None:
        DashboardLimits.from_settings(settings)
        return
    with pytest.raises(DashboardLimitsError, match=refusal):
        DashboardLimits.from_settings(settings)


def test_t5_6c_a_save_meets_the_install_s_panel_cap_and_refresh_floor() -> None:
    from nexovue_api.dashboards.limits import compile_within

    definition = Definition.model_validate(_three_scans())
    with pytest.raises(InvalidRequestError, match="holds 3 panels"):
        compile_within(definition, DashboardLimits(max_panels=3))
    with pytest.raises(InvalidRequestError, match="every 300 seconds"):
        compile_within(definition, DashboardLimits(refresh_floor_seconds=300))
    assert compile_within(definition, DashboardLimits()).worst_case_scans == 3
