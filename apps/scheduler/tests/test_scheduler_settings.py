"""Boot-time configuration for the scheduler."""

from __future__ import annotations

import pytest
from nexovue_platform.email import EMAIL_VARIABLES

from nexovue_scheduler.settings import Settings, SettingsError, get_settings

COMPLETE_ENVIRONMENT = {
    "ENV": "development",
    "POSTGRES_DSN": "postgresql://nexovue:secret@localhost:5432/nexovue",
    "KAFKA_BROKERS": "localhost:29092",
}

KNOWN_VARIABLES = (
    *COMPLETE_ENVIRONMENT,
    "LOG_LEVEL",
    "LOG_FORMAT",
    *EMAIL_VARIABLES,
    "APP_BASE_URL",
    "CLICKHOUSE_ERASER_DSN",
    "ERASURE_HOUR_UTC",
    "ERASURE_BATCH_SIZE",
)


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
    for name in ("postgres_dsn", "kafka_brokers"):
        assert Settings.model_fields[name].is_required(), f"{name} must have no default"


def test_alert_mail_reads_the_same_variables_the_api_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F21. One configuration for both processes' mail, judged by one rule."""
    _apply(
        monkeypatch,
        {
            **COMPLETE_ENVIRONMENT,
            "EMAIL_PROVIDER": "resend",
            "EMAIL_API_KEY": "re_test",
            "EMAIL_SENDING_DOMAIN": "mail.nexovue.test",
            "EMAIL_FROM_ADDRESS": "alerts@mail.nexovue.test",
            "EMAIL_REPLY_TO": "support@nexovue.test",
            "APP_BASE_URL": "https://app.nexovue.test",
        },
    )
    configured = get_settings()

    assert configured.email is not None
    assert configured.email.from_address == "alerts@mail.nexovue.test"
    assert configured.app_base_url == "https://app.nexovue.test"


def test_half_configured_mail_is_no_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, {**COMPLETE_ENVIRONMENT, "EMAIL_API_KEY": "re_test"})

    assert get_settings().email is None


def test_the_console_provider_needs_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, {**COMPLETE_ENVIRONMENT, "EMAIL_PROVIDER": "console"})

    configured = get_settings().email
    assert configured is not None
    assert configured.provider == "console"


def test_the_nightly_erasure_runs_at_two_utc_unless_told_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _apply(monkeypatch, COMPLETE_ENVIRONMENT)

    settings = get_settings()

    assert settings.erasure_hour_utc == 2
    assert settings.erasure_batch_size == 100
    assert settings.clickhouse_eraser_dsn == ""


@pytest.mark.parametrize(
    ("name", "value"), [("ERASURE_HOUR_UTC", "24"), ("ERASURE_BATCH_SIZE", "0")]
)
def test_an_impossible_erasure_setting_is_named(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _apply(monkeypatch, {**COMPLETE_ENVIRONMENT, name: value})

    with pytest.raises(SettingsError) as failure:
        get_settings()
    assert name in str(failure.value)
