"""`install.sh`, the one-line install a release attaches (docs/changes/ready-to-deploy).

A stand-in `curl` answers from a folder laid out as a release is, and the kit's own `nexovue` is a
stand-in that records how it was called — so these check what the script downloads, what it refuses,
and which command it hands over to, without the network or Docker.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Final

import pytest

import nexovue_api

SCRIPT: Final = Path(nexovue_api.__file__).parents[4] / "deploy" / "install.sh"
VERSION: Final = "1.2.3"
KIT: Final = f"nexovue-{VERSION}.tar.gz"

# Serves `<release folder>/v<version>/<file>` for any URL ending in that path.
STAND_IN_CURL: Final = r"""#!/usr/bin/env bash
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    http*) url="$1" ;;
  esac
  shift
done
printf '%s\n' "$url" >> "$CURL_LOG"
source="$RELEASE_DIR/${url#*/download/}"
[ -f "$source" ] || exit 22
cp "$source" "$out"
"""

STAND_IN_NEXOVUE: Final = '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$NEXOVUE_LOG"\n'


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def release(tmp_path: Path) -> dict[str, str]:
    """A release of 1.2.3 holding its kit and SHA256SUMS, and a released install.sh."""
    kit_dir = tmp_path / "build" / f"nexovue-{VERSION}"
    kit_dir.mkdir(parents=True)
    _executable(kit_dir / "nexovue", STAND_IN_NEXOVUE)
    assets = tmp_path / "release" / f"v{VERSION}"
    assets.mkdir(parents=True)
    with tarfile.open(assets / KIT, "w:gz") as archive:
        archive.add(kit_dir, arcname=kit_dir.name)
    digest = hashlib.sha256((assets / KIT).read_bytes()).hexdigest()
    (assets / "SHA256SUMS").write_text(
        f"{'0' * 64}  nexovue-{VERSION}-offline-amd64.tar\n{digest}  {KIT}\n"
    )
    released = tmp_path / "install.sh"
    released.write_text(SCRIPT.read_text().replace("@NEXOVUE_VERSION@", VERSION))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "curl", STAND_IN_CURL)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RELEASE_DIR": str(tmp_path / "release"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "NEXOVUE_LOG": str(tmp_path / "nexovue.log"),
        "NEXOVUE_HOME": str(tmp_path / "home"),
        "SCRIPT": str(released),
    }


def _run(
    env: dict[str, str], *arguments: str, script: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", script or env["SCRIPT"], *arguments],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )


def _handed_to(env: dict[str, str]) -> list[str]:
    log = Path(env["NEXOVUE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def test_a_new_server_gets_this_release_s_kit_checked_then_installed(
    release: dict[str, str],
) -> None:
    result = _run(release, "--offline")

    assert result.returncode == 0, result.stderr
    assert _handed_to(release) == ["install --offline"]
    fetched = Path(release["CURL_LOG"]).read_text().splitlines()
    assert all(
        url.startswith("https://github.com/nexovue-tech/nexovue/releases/download/v1.2.3/")
        for url in fetched
    )


def test_an_installed_server_is_upgraded(release: dict[str, str]) -> None:
    home = Path(release["NEXOVUE_HOME"])
    home.mkdir()
    (home / "nexovue.env").write_text("NEXOVUE_VERSION='1.0.0'\n")

    assert _run(release).returncode == 0
    assert _handed_to(release) == ["upgrade"]


def test_a_kit_that_does_not_match_the_release_s_checksums_is_never_run(
    release: dict[str, str],
) -> None:
    kit = Path(release["RELEASE_DIR"]) / f"v{VERSION}" / KIT
    kit.write_bytes(kit.read_bytes() + b"tampered")

    result = _run(release)

    assert result.returncode != 0
    assert "does not match the release's SHA256SUMS" in result.stderr
    assert _handed_to(release) == []


def test_the_repository_s_own_copy_refuses_to_run(release: dict[str, str]) -> None:
    result = _run(release, script=str(SCRIPT))

    assert result.returncode != 0
    assert "download install.sh from a release" in result.stderr
    assert not Path(release["CURL_LOG"]).exists(), "nothing downloaded"
