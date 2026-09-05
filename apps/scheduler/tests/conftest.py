from __future__ import annotations

from collections.abc import Iterator

import pytest
from loopback_site import LoopbackSite, serve_loopback_site


@pytest.fixture
def loopback_site() -> Iterator[LoopbackSite]:
    """A loopback service the suites prove was never contacted."""
    with serve_loopback_site() as site:
        yield site
