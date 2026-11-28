"""F4 AC65 (plan T2.3): the API never logs a chat tool's arguments.

A tool's search phrase travels in the query string, and uvicorn's access line prints the
query string. nexovue's own request log records method, path and status only. Here the
`api` role's own start command runs as its own process, as the image runs it, and a planted
phrase sent as `q` must appear nowhere in what that process writes.

Its own process, because uvicorn configures logging before it imports the app, and the app's
logging setup runs at import: only that order is production's. In-process, the order is
reversed and the result depends on what earlier tests left in the test process's logging.

It needs no store: the request is refused before any read, and the lines are written either
way.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

import environment  # noqa: F401 — sets what importing the app reads
import httpx

from nexovue_api.roles import command_for
from nexovue_api.settings import API_PREFIX

PLANTED: Final = "planted-phrase-7f3a91"
# Production's level, and the level uvicorn writes its access line at.
LOG_LEVEL: Final = "info"
STARTUP_SECONDS: Final = 60.0
SHUTDOWN_SECONDS: Final = 30.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_until_serving(api: subprocess.Popen[bytes], base_url: str, output: Path) -> None:
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if api.poll() is not None:
            raise AssertionError(f"the api role exited at start:\n{output.read_text()}")
        try:
            httpx.get(f"{base_url}/healthz", timeout=1.0)
            return
        except httpx.TransportError:
            time.sleep(0.2)
    raise AssertionError(f"the api role did not serve within {STARTUP_SECONDS}s")


def test_a_planted_search_phrase_appears_in_no_log_output(tmp_path: Path) -> None:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    # The role's command as an operator tunes it: later options win.
    command = command_for("api", ["--host", "127.0.0.1", "--port", str(port)])
    output = tmp_path / "api.log"
    with output.open("wb") as written:
        api = subprocess.Popen(
            [sys.executable, "-m", *command],
            env={**os.environ, "LOG_LEVEL": LOG_LEVEL},
            stdout=written,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_until_serving(api, base_url, output)
            answered = httpx.get(
                f"{base_url}{API_PREFIX}/logs/search", params={"q": PLANTED, "project_id": "x"}
            )
            assert answered.status_code >= 400
        finally:
            api.send_signal(signal.SIGINT)
            api.wait(timeout=SHUTDOWN_SECONDS)

    everything = output.read_text()
    assert PLANTED not in everything
    # The process logged the refused request, so silence is not why the phrase is absent.
    assert "/logs/search" in everything, everything
