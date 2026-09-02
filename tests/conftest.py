"""Fixtures wiring the service to a fake feed, a fixed date and a driven clock.

The whole runtime the handler depends on is replaced through one override, so a
test names only what it actually cares about.
"""

from __future__ import annotations

from datetime import date
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import CONVERT_PATH, Runtime, app, get_runtime
from app.upstream import RateCache, Upstream
from tests.support import FAKE_BASE, Clock, FakeFeed

#: A Wednesday. The Saturday and Sunday before it are 2026-08-29 and 2026-08-30,
#: and the ECB's last publication before them was Friday 2026-08-28.
TODAY = date(2026, 9, 2)
LAST_PUBLICATION = date(2026, 8, 28)


@pytest.fixture
def feed() -> FakeFeed:
    return FakeFeed()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def client(feed: FakeFeed, clock: Clock, today: date) -> Iterator[TestClient]:
    """The service, talking to the fake feed and believing `today` is today.

    The application's own lifespan is deliberately not run: it would build a real
    HTTP client against whatever FX_UPSTREAM_BASE happens to hold, and no test
    should depend on the environment it is run in. That the lifespan works is
    asserted separately, in test_boot.py.
    """
    settings = load_settings({"FX_UPSTREAM_BASE": FAKE_BASE})
    upstream = Upstream(
        settings,
        httpx.AsyncClient(transport=feed.transport()),
        RateCache(settings.cache_max_entries, monotonic=clock),
    )
    app.dependency_overrides[get_runtime] = lambda: Runtime(
        upstream=upstream, settings=settings, today=today
    )

    # Server exceptions are returned rather than raised so the catch-all handler
    # can be tested through the same door as everything else.
    yield TestClient(app, raise_server_exceptions=False)

    app.dependency_overrides.clear()


@pytest.fixture
def ask(client: TestClient):
    """Call the endpoint the way a caller would.

    The source currency is named `source` here only because `from` is a Python
    keyword; it goes onto the wire under the documented name.
    """

    def _ask(*, amount: object = 250, source: str = "EUR", to: str = "TRY", on: str | None = None):
        params: dict[str, object] = {"amount": amount, "from": source, "to": to}
        if on is not None:
            params["date"] = on
        return client.get(CONVERT_PATH, params=params)

    return _ask
