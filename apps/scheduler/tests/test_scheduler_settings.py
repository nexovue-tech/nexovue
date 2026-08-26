"""Boot-time configuration for the scheduler."""

from __future__ import annotations

import pytest

from nexovue_scheduler.settings import Settings, SettingsError, get_settings

COMPLETE_ENVIRONMENT = {
    "ENV": "development",
    "POSTGRES_DSN": "postgresql://nexovue:secret@localhost:5432/nexovue",
    "REDIS_URL": "redis://localhost:6379/1",
    "KAFKA_BROKERS": "localhost:29092",
}

KNOWN_VARIABLES = (*COMPLETE_ENVIRONMENT, "LOG_LEVEL", "LOG_FORMAT", "SCHEDULER_HEALTH_PORT")


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in KNOWN_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def _apply(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_brokers_are_comma_separated_like_the_go_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _apply(monkeypatch, {**COMPLETE_ENVIRONMENT, "KAFKA_BROKERS": "a:9092, b:9092"})

    assert get_settings().kafka_brokers == ["a:9092", "b:9092"]


@pytest.mark.parametrize("missing", list(COMPLETE_ENVIRONMENT))
def test_missing_required_variable_is_named(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    _apply(monkeypatch, {k: v for k, v in COMPLETE_ENVIRONMENT.items() if k != missing})

    with pytest.raises(SettingsError) as failure:
        get_settings()

    assert missing in str(failure.value)


def test_no_secret_carries_a_default() -> None:
    for name in ("postgres_dsn", "redis_url", "kafka_brokers"):
        assert Settings.model_fields[name].is_required(), f"{name} must have no default"
