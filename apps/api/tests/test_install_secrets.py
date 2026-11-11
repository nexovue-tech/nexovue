"""The install kit's F22 settings: `CONNECTION_SECRET_KEY` (T3.3) and `NEXOVUE_CONNECTIONS`
(T3.12, AC118).

Compose demands the key (`${CONNECTION_SECRET_KEY:?…}`), so an install upgraded from a
version without it would refuse to start unless the upgrade adds one. A new key guards
nothing yet, unlike every other secret the upgrade leaves alone, so adding it is safe;
replacing an existing one would lock every sealed connection away. These run the
script's own shell functions on a temporary settings file.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Final

import pytest
from install_kit import run_kit_function

KEY: Final = "CONNECTION_SECRET_KEY"
# Sealing keys an upgrade adds: F22's, and F3's for AI provider keys.
KEYS_ADDED_LATER: Final = (KEY, "AI_SECRET_KEY")
# F3: the AI role's Postgres account, which `setup` sets at every start, so an upgrade may add it.
INSIGHT_PASSWORD: Final = "POSTGRES_INSIGHT_PASSWORD"


def _run(function: str, settings: Path, environment: dict[str, str] | None = None) -> str:
    finished = run_kit_function(settings.parent, function, str(settings), environment=environment)
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


def _settings(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    return {
        key: value.strip("'") for key, _, value in (line.partition("=") for line in lines) if key
    }


@pytest.mark.parametrize("key", KEYS_ADDED_LATER)
def test_a_new_install_writes_a_sealing_key(tmp_path: Path, key: str) -> None:
    settings = tmp_path / "nexovue.env"
    settings.touch()

    _run("write_secrets", settings)

    written = _settings(settings)[key]
    assert len(base64.urlsafe_b64decode(written)) == 32


@pytest.mark.parametrize("key", KEYS_ADDED_LATER)
def test_an_upgrade_adds_the_key_to_settings_written_before_it(tmp_path: Path, key: str) -> None:
    settings = tmp_path / "nexovue.env"
    settings.write_text("CHECK_SECRET_KEY='kept-check-key'\nPOSTGRES_PASSWORD='kept-password'\n")

    _run("add_missing_secrets", settings)

    upgraded = _settings(settings)
    assert len(base64.urlsafe_b64decode(upgraded[key])) == 32
    assert upgraded["CHECK_SECRET_KEY"] == "kept-check-key"
    assert upgraded["POSTGRES_PASSWORD"] == "kept-password"


@pytest.mark.parametrize("key", KEYS_ADDED_LATER)
def test_an_upgrade_never_replaces_a_key_that_exists(tmp_path: Path, key: str) -> None:
    """A new value would leave every sealed connection unopenable."""
    settings = tmp_path / "nexovue.env"
    settings.write_text(f"{key}='existing-key'\n")

    _run("add_missing_secrets", settings)

    assert _settings(settings)[key] == "existing-key"


@pytest.mark.parametrize("function", ["write_secrets", "add_missing_secrets"])
def test_the_ai_account_gets_a_password_at_install_and_at_upgrade(
    tmp_path: Path, function: str
) -> None:
    """F3: `setup` creates `nexovue_insight` with it; an install upgraded from before F3 gets
    one, and an existing one is kept, since `setup` sets it again at every start anyway."""
    settings = tmp_path / "nexovue.env"
    settings.write_text("POSTGRES_PASSWORD='kept-password'\n")

    _run(function, settings)
    first = _settings(settings)[INSIGHT_PASSWORD]
    _run("add_missing_secrets", settings)

    assert len(first) == 48 and int(first, 16) >= 0
    assert _settings(settings)[INSIGHT_PASSWORD] == first


# ------------------------------------------------------ NEXOVUE_CONNECTIONS (T3.12)


@pytest.mark.parametrize(("chosen", "written"), [(None, "yes"), ("no", "no"), ("yes", "yes")])
def test_an_install_records_whether_connections_run(
    tmp_path: Path, chosen: str | None, written: str
) -> None:
    settings = tmp_path / "nexovue.env"
    settings.touch()

    _run("write_choices", settings, {"NEXOVUE_CONNECTIONS": chosen} if chosen else None)

    assert _settings(settings)["NEXOVUE_CONNECTIONS"] == written


def test_anything_but_yes_or_no_is_refused(tmp_path: Path) -> None:
    settings = tmp_path / "nexovue.env"
    settings.touch()

    refused = run_kit_function(
        tmp_path, "write_choices", str(settings), environment={"NEXOVUE_CONNECTIONS": "maybe"}
    )

    assert refused.returncode != 0
    assert "NEXOVUE_CONNECTIONS is yes or no" in refused.stderr


def _stopped(tmp_path: Path, lines: str) -> list[str]:
    settings = tmp_path / "nexovue.env"
    settings.write_text(lines)
    return _run("services_to_stop", settings).split()


@pytest.mark.parametrize(
    ("lines", "stops_it"),
    [
        ("NEXOVUE_CONNECTIONS='yes'\n", True),
        # Written by a version before the setting: connections are on by default.
        ("", True),
        ("NEXOVUE_CONNECTIONS='no'\n", False),
        # Hosted runs no connections role whatever the setting says (founder decision 3).
        ("NEXOVUE_CONNECTIONS='yes'\nNEXOVUE_LAYOUT='hosted'\n", False),
    ],
)
def test_a_restore_stops_the_connections_role_only_where_it_runs(
    tmp_path: Path, lines: str, stops_it: bool
) -> None:
    """Design, *Observability and operations*: it would keep writing to Postgres and calling
    trackers during a restore; and a service whose profile is off is never named."""
    stopped = _stopped(tmp_path, lines)

    assert ("scheduler-connections" in stopped) is stops_it
    assert {"api", "scheduler-beat", "scheduler-light", "scheduler-deliveries"} <= set(stopped)


@pytest.mark.parametrize(
    ("lines", "profile"),
    [("", True), ("NEXOVUE_CONNECTIONS='no'\n", False), ("NEXOVUE_LAYOUT='hosted'\n", False)],
)
def test_compose_runs_the_connections_profile_only_where_it_runs(
    tmp_path: Path, lines: str, profile: bool
) -> None:
    settings = tmp_path / "nexovue.env"
    settings.write_text(lines)

    printed = _run("compose_profiles", settings).split()

    assert ("connections" in printed) is profile
