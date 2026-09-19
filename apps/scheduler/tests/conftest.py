from __future__ import annotations

from collections.abc import Iterator

import pytest
from alert_fixtures import engine, tenant  # noqa: F401  shared by the alert suites
from recording_site import RecordingSite, serve_recording_site


@pytest.fixture
def recording_site() -> Iterator[RecordingSite]:
    """A real HTTP target on this machine's private address that records every request."""
    with serve_recording_site() as site:
        yield site
