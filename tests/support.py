"""Test doubles shared by the suite.

Nothing here opens a socket. Every feed response is served by an
httpx.MockTransport inside the process, which is what lets ./test.sh pass with
FX_UPSTREAM_BASE pointing at a closed port, or with the machine offline.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

#: RFC 2606 reserves .invalid, so a request that escaped the mock transport would
#: fail to resolve rather than quietly reach something real.
FAKE_BASE = "http://upstream.invalid"

_VERSION_PREFIX = "/v1"

DEFAULT_CURRENCIES = {
    "EUR": "Euro",
    "TRY": "Turkish Lira",
    "USD": "United States Dollar",
    "BRL": "Brazilian Real",
}


class Clock:
    """A monotonic clock the test drives by hand."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeFeed:
    """A stand-in for the rate feed, programmed per test.

    Anything not programmed answers 404 with the body the real feed uses, which
    is the case the service has to disentangle: the same status means both
    "no such currency" and "no rate that far back".
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[str, Callable[[], httpx.Response]] = {}
        self.publishes_currencies(DEFAULT_CURRENCIES)

    # --- programming ---------------------------------------------------------

    def publishes(self, path: str, *, on: str, rates: dict[str, str]) -> None:
        """Program a normal answer: a publication date, and rates on that date.

        The body is assembled as text so a rate keeps the exact decimal digits
        the test wrote. Going through a Python float first would be the very
        thing the service is built to avoid.
        """
        pairs = ", ".join(f'"{code}": {value}' for code, value in rates.items())
        body = f'{{"amount": 1.0, "base": "EUR", "date": "{on}", "rates": {{{pairs}}}}}'
        self.answers(path, body=body)

    def publishes_currencies(self, codes: dict[str, str]) -> None:
        pairs = ", ".join(f'"{code}": "{name}"' for code, name in codes.items())
        self.answers("currencies", body=f"{{{pairs}}}")

    def answers(
        self,
        path: str,
        *,
        body: str = "",
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        def respond() -> httpx.Response:
            return httpx.Response(
                status, text=body, headers={"content-type": content_type}
            )

        self._routes[_url_path(path)] = respond

    def fails(self, path: str, exception: Exception) -> None:
        """Program a transport failure, as a slow or unreachable host produces."""

        def raise_it() -> httpx.Response:
            raise exception

        self._routes[_url_path(path)] = raise_it

    def serves_no_currency_list(self) -> None:
        self.answers("currencies", body='{"message": "not found"}', status=404)

    # --- inspection ----------------------------------------------------------

    def calls_to(self, path: str) -> int:
        wanted = _url_path(path)
        return sum(1 for request in self.requests if request.url.path == wanted)

    @property
    def rate_requests(self) -> list[httpx.Request]:
        """Every request except the currency list, which is a lookup aside."""
        aside = _url_path("currencies")
        return [request for request in self.requests if request.url.path != aside]

    # --- the transport -------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self._routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"message": "not found"})
        return route()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _url_path(path: str) -> str:
    return f"{_VERSION_PREFIX}/{path}"
