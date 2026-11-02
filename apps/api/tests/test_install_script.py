"""`deploy/nexovue` against a stand-in `docker` on PATH (docs/changes/ready-to-deploy, T14).

The stand-in records every call and answers the few questions the script asks Docker — its version,
memory, architecture, and the schema each version's images carry — so the script's decisions are
checked without containers: what it writes, which files and project it hands Compose, what it
refuses. The real thing is proved by the rehearsal on a fresh machine.
"""

from __future__ import annotations

import base64
import os
import stat
import subprocess
from pathlib import Path
from typing import Final

import pytest

import nexovue_api

DEPLOY: Final = Path(nexovue_api.__file__).parents[4] / "deploy"
SCRIPT: Final = DEPLOY / "nexovue"
SECRETS: Final = (
    "POSTGRES_PASSWORD",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_READER_PASSWORD",
    "CLICKHOUSE_ERASER_PASSWORD",
    "CLICKHOUSE_DETECTION_WRITER_PASSWORD",
    "KAFKA_CLUSTER_ID",
    "WORKERS_MASKING_HASH_SECRET",
    "CHECK_SECRET_KEY",
    "CREDENTIAL_ENCRYPTION_KEY",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
)

STAND_IN: Final = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$*" in *"${STUB_FAIL_ON:-no such call}"*) exit 1 ;; esac
case "$1 $2" in
  "info --format")
    case "$3" in
      *MemTotal*) echo "${STUB_MEMORY:-17179869184}" ;;
      *Architecture*) echo x86_64 ;;
      *DockerRootDir*) echo "$STUB_ROOT" ;;
    esac ;;
  "info "*) ;;
  "version --format") echo "${STUB_DOCKER:-27.3.1}" ;;
  "compose version") echo "${STUB_COMPOSE:-2.30.3}" ;;
  "run --rm")
    image="$5"; version="${image##*:}"; variable="STUB_SCHEMA_${version//./_}"
    case "$image" in
      *nexovue-pipeline:*) echo "clickhouse 33" ;;
      *) echo "postgres ${!variable:-0039}" ;;
    esac ;;
esac
exit 0
"""


@pytest.fixture
def machine(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(STAND_IN)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        # Docker's data directory, absent as it is on a Mac (inside Docker's VM): no disk check.
        "STUB_ROOT": str(tmp_path / "docker-root"),
        "NEXOVUE_HOME": str(tmp_path / "nexovue"),
        "NEXOVUE_SKIP_PORT_CHECK": "1",
        "NEXOVUE_VERSION": "1.0.0",
        "NEXOVUE_APP_HOST": "nexovue.example.com",
        "NEXOVUE_INGEST_HOST": "ingest.nexovue.example.com",
        "NEXOVUE_TLS_EMAIL": "ops@example.com",
        "FIRST_OWNER_EMAIL": "owner@example.com",
    }


def _run(env: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *arguments],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )


def _settings(env: dict[str, str]) -> dict[str, str]:
    lines = (Path(env["NEXOVUE_HOME"]) / "nexovue.env").read_text().splitlines()
    pairs = (line.split("=", 1) for line in lines if "=" in line)
    return {key: value.strip("'") for key, value in pairs}


def _compose_calls(env: dict[str, str]) -> list[str]:
    log = Path(env["DOCKER_LOG"]).read_text().splitlines()
    return [line for line in log if line.startswith("compose --project-name")]


def test_an_install_writes_every_secret_once_and_starts_the_self_hosted_layout(
    machine: dict[str, str],
) -> None:
    result = _run(machine, "install")

    assert result.returncode == 0, result.stderr
    settings = _settings(machine)
    values = [settings[name] for name in SECRETS]
    assert all(values) and len(set(values)) == len(values), "every secret generated, none shared"
    assert settings["NEXOVUE_LAYOUT"] == "self-hosted"
    assert settings["NEXOVUE_VERSION"] == "1.0.0"
    mode = (Path(machine["NEXOVUE_HOME"]) / "nexovue.env").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600, "the settings hold every secret"

    password = settings["FIRST_OWNER_PASSWORD"]
    assert len(password) >= 12
    assert result.stdout.count(password) == 1, "a generated password is shown exactly once"

    current = Path(machine["NEXOVUE_HOME"]) / "current"
    assert current.resolve() == (Path(machine["NEXOVUE_HOME"]) / "releases" / "1.0.0").resolve()
    up = [call for call in _compose_calls(machine) if " up " in call]
    assert len(up) == 1
    assert "--project-name nexovue " in up[0]
    assert up[0].count(" -f ") == 1 and "compose/nexovue.yaml" in up[0]


def test_installing_again_keeps_the_secrets(machine: dict[str, str]) -> None:
    assert _run(machine, "install").returncode == 0
    first = _settings(machine)

    assert _run(machine, "install").returncode == 0

    assert {name: _settings(machine)[name] for name in SECRETS} == {
        name: first[name] for name in SECRETS
    }, "a second install must not regenerate what the stores were created with"


def test_a_missing_answer_with_no_terminal_names_the_variable(machine: dict[str, str]) -> None:
    del machine["NEXOVUE_INGEST_HOST"]

    result = _run(machine, "install")

    assert result.returncode != 0
    assert "NEXOVUE_INGEST_HOST is required" in result.stderr
    assert not any(" up " in call for call in _compose_calls(machine))
    assert not (Path(machine["NEXOVUE_HOME"]) / "nexovue.env").exists(), "no half-written settings"


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("STUB_MEMORY", str(8 * 1024**3), "at least 12 GiB"),
        ("STUB_COMPOSE", "2.20.2", "Docker Compose 2.20.2 is too old"),
        ("STUB_DOCKER", "20.10.0", "Docker 20.10.0 is too old"),
    ],
)
def test_a_machine_that_cannot_run_it_is_refused_before_anything_is_written(
    machine: dict[str, str], variable: str, value: str, message: str
) -> None:
    machine[variable] = value

    result = _run(machine, "install")

    assert result.returncode != 0
    assert message in result.stderr
    assert not (Path(machine["NEXOVUE_HOME"]) / "nexovue.env").exists()


def test_a_value_compose_cannot_read_literally_is_refused(machine: dict[str, str]) -> None:
    machine["FIRST_OWNER_PASSWORD"] = "it's-twelve-or-more"

    result = _run(machine, "install")

    assert result.returncode != 0
    assert "may not contain a single quote" in result.stderr
    assert not (Path(machine["NEXOVUE_HOME"]) / "nexovue.env").exists(), "no half-written settings"


def _installed_with_a_stand_in_backup(machine: dict[str, str]) -> Path:
    assert _run(machine, "install").returncode == 0
    home = Path(machine["NEXOVUE_HOME"])
    backup = home / "releases" / "1.0.0" / "backup" / "backup.sh"
    backup.write_text(
        "#!/usr/bin/env bash\n"
        'snapshot="$NEXOVUE_BACKUP_DIR/2026-10-28T020000Z"\n'
        'mkdir -p "$snapshot"\n'
        "for file in manifest.txt postgres.dump clickhouse.zip; do\n"
        '  echo x > "$snapshot/$file"\n'
        "done\n"
    )
    return home


@pytest.mark.parametrize(("schema_next", "recorded"), [("0039", "same"), ("0040", "changed")])
def test_an_upgrade_backs_up_first_and_records_whether_the_schema_changed(
    machine: dict[str, str], schema_next: str, recorded: str
) -> None:
    home = _installed_with_a_stand_in_backup(machine)
    machine |= {"NEXOVUE_VERSION": "1.1.0", "STUB_SCHEMA_1_1_0": schema_next}

    result = _run(machine, "upgrade")

    assert result.returncode == 0, result.stderr
    settings = _settings(machine)
    assert settings["NEXOVUE_VERSION"] == "1.1.0"
    assert settings["NEXOVUE_PREVIOUS_VERSION"] == "1.0.0"
    assert settings["NEXOVUE_ROLLBACK_SCHEMA"] == recorded
    assert settings["NEXOVUE_PRE_UPGRADE_SNAPSHOT"] == "2026-10-28T020000Z"
    assert (home / "current").resolve() == (home / "releases" / "1.1.0").resolve()


# What an upgrade itself records, which may change; every other line must not.
UPGRADE_RECORDS: Final = (
    "NEXOVUE_VERSION",
    "NEXOVUE_PREVIOUS_VERSION",
    "NEXOVUE_PRE_UPGRADE_SNAPSHOT",
    "NEXOVUE_ROLLBACK_SCHEMA",
)
NEW_ACCOUNT: Final = "CLICKHOUSE_DETECTION_WRITER_PASSWORD"


def test_an_upgrade_adds_a_missing_account_password_and_changes_nothing_else(
    machine: dict[str, str],
) -> None:
    """C9, C25: an install from before an account existed gains its password, and no
    secret the stores were created with is regenerated."""
    home = _installed_with_a_stand_in_backup(machine)
    settings_file = home / "nexovue.env"
    older = [
        line
        for line in settings_file.read_bytes().splitlines(keepends=True)
        if not line.startswith(f"{NEW_ACCOUNT}=".encode())
    ]
    settings_file.write_bytes(b"".join(older))
    machine |= {"NEXOVUE_VERSION": "1.1.0"}

    result = _run(machine, "upgrade")

    assert result.returncode == 0, result.stderr
    after = settings_file.read_bytes().splitlines(keepends=True)
    kept = [
        line
        for line in older
        if not line.startswith(tuple(f"{k}=".encode() for k in UPGRADE_RECORDS))
    ]
    assert [line for line in kept if line not in after] == [], "an existing value changed"
    settings = _settings(machine)
    added = settings.get(NEW_ACCOUNT, "")
    assert len(added) == 48, f"{NEW_ACCOUNT} was not added"
    assert added not in {settings[name] for name in SECRETS if name != NEW_ACCOUNT}
    assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600, "the settings hold every secret"


def test_rollback_past_a_schema_change_is_refused_without_the_restore(
    machine: dict[str, str],
) -> None:
    _installed_with_a_stand_in_backup(machine)
    machine |= {"NEXOVUE_VERSION": "1.1.0", "STUB_SCHEMA_1_1_0": "0040"}
    assert _run(machine, "upgrade").returncode == 0
    starts = len([call for call in _compose_calls(machine) if " up " in call])

    result = _run(machine, "rollback")

    assert result.returncode != 0
    assert "nexovue rollback --restore" in result.stderr
    assert _settings(machine)["NEXOVUE_VERSION"] == "1.1.0", "nothing changed"
    assert len([call for call in _compose_calls(machine) if " up " in call]) == starts


def test_rollback_with_the_same_schema_returns_to_the_previous_version(
    machine: dict[str, str],
) -> None:
    home = _installed_with_a_stand_in_backup(machine)
    machine |= {"NEXOVUE_VERSION": "1.1.0", "STUB_SCHEMA_1_1_0": "0039"}
    assert _run(machine, "upgrade").returncode == 0

    result = _run(machine, "rollback")

    assert result.returncode == 0, result.stderr
    assert _settings(machine)["NEXOVUE_VERSION"] == "1.0.0"
    assert (home / "current").resolve() == (home / "releases" / "1.0.0").resolve()
    assert " up " in _compose_calls(machine)[-1]


def test_the_quickstart_runs_its_own_project_over_plain_http(machine: dict[str, str]) -> None:
    result = _run(machine, "quickstart")

    assert result.returncode == 0, result.stderr
    assert "NOT for production" in result.stdout
    up = _compose_calls(machine)[-1]
    assert "--project-name nexovue-quickstart " in up
    assert "compose/nexovue.quickstart.yaml" in up
    settings_file = Path(machine["HOME"]) / ".nexovue" / "quickstart" / "nexovue.env"
    assert "NEXOVUE_LAYOUT='quickstart'" in settings_file.read_text()
    assert not Path(machine["NEXOVUE_HOME"]).exists(), "a quickstart touches no install"


def test_each_secret_is_in_the_form_its_service_reads(machine: dict[str, str]) -> None:
    """Found by the quickstart rehearsal: a URL-safe masking secret kept the worker restarting."""
    assert _run(machine, "install").returncode == 0
    settings = _settings(machine)

    masking = base64.b64decode(settings["WORKERS_MASKING_HASH_SECRET"], validate=True)
    assert len(masking) >= 32, "the worker refuses a masking secret under 32 bytes"
    for key in ("CHECK_SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY"):
        assert len(base64.urlsafe_b64decode(settings[key])) == 32, key
    cluster = settings["KAFKA_CLUSTER_ID"]
    assert len(base64.urlsafe_b64decode(cluster + "==")) == 16 and "=" not in cluster


def test_three_brokers_chosen_at_install_add_their_compose_file(machine: dict[str, str]) -> None:
    machine["NEXOVUE_KAFKA_BROKERS"] = "3"

    assert _run(machine, "install").returncode == 0

    assert _settings(machine)["NEXOVUE_KAFKA_BROKERS"] == "3"
    assert "compose/nexovue.kafka3.yaml" in _compose_calls(machine)[-1]


def test_a_broker_count_kafka_cannot_vote_with_is_refused(machine: dict[str, str]) -> None:
    machine["NEXOVUE_KAFKA_BROKERS"] = "2"

    result = _run(machine, "install")

    assert result.returncode != 0
    assert "NEXOVUE_KAFKA_BROKERS is 1 or 3" in result.stderr


def test_a_backup_that_could_not_be_shipped_fails(machine: dict[str, str]) -> None:
    """Found by the restore rehearsal: the upload failed and the backup reported itself complete."""
    home = _installed_with_a_stand_in_backup(machine)
    with (home / "nexovue.env").open("a") as settings:
        settings.write("BACKUP_S3_BUCKET='offsite'\n")
    machine["STUB_FAIL_ON"] = "backup ship"

    result = _run(machine, "backup")

    assert result.returncode != 0
    assert "backup complete" not in result.stdout
