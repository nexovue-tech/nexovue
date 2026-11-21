"""F4 AC65 (plan T2.3): the API never logs a chat tool's arguments.

A tool's search phrase travels in the query string, and uvicorn's access line prints the
query string. So the `api` role runs with `--no-access-log`, and nexovue's own request log
records method, path and status only. Here a real uvicorn server, configured from the role's
own start command, serves the real app; a planted phrase sent as `q` must appear in none of
its output: stdout, stderr, the standard logs or nexovue's structured log.

It needs no store: the request is refused before any read, and the lines are written either
way.
"""

from __future__ import annotations

import asyncio
import logging
import socket

import environment  # noqa: F401 — sets what importing the app reads
import httpx
import pytest
import uvicorn
from structlog.testing import capture_logs

from nexovue_api.main import app
from nexovue_api.roles import ROLES
from nexovue_api.settings import API_PREFIX

PLANTED = "planted-phrase-7f3a91"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _client(record: logging.LogRecord) -> bool:
    return record.name.split(".")[0] in {"httpx", "httpcore"}


async def test_a_planted_search_phrase_appears_in_no_log_output(
    capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        access_log="--no-access-log" not in ROLES["api"],
        log_level="info",
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    caplog.set_level(logging.DEBUG)
    try:
        with capture_logs() as structured:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                answered = await client.get(
                    f"{API_PREFIX}/logs/search", params={"q": PLANTED, "project_id": "x"}
                )
        assert answered.status_code >= 400
    finally:
        server.should_exit = True
        await serving

    printed = capfd.readouterr()
    everything = "\n".join(
        [
            printed.out,
            printed.err,
            # The test's own client logs the URL it sent; only the server's lines count.
            *(record.getMessage() for record in caplog.records if not _client(record)),
            *(str(line) for line in structured),
        ]
    )
    assert PLANTED not in everything
    # The server did write lines, so silence is not why the phrase is absent.
    assert everything.strip()
