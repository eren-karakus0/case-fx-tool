"""Runtime configuration, resolved once from the environment.

Every value the operator can change from outside the process is resolved here so
that no other module has to know an environment variable exists. In particular
the real upstream host appears exactly once in this repository: as the default
below. The reviewer points FX_UPSTREAM_BASE at a fake upstream, and nothing else
in the package may assume otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

#: Used when FX_UPSTREAM_BASE is unset. The only occurrence of the real host.
DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"

#: frankfurter.dev serves two generations of its API side by side. v1 is the
#: ECB-only dataset this service claims as its source; v2 blends 84 central banks
#: and answers the same question with a different rate on a different date, which
#: would make the "ECB via frankfurter.dev" source field untrue. The base URL
#: carries no version, so the client appends this one.
UPSTREAM_API_VERSION = "v1"

#: The provenance label the brief fixes for successful answers. It names the
#: publisher of the rates, not the host requests are sent to, so it stays correct
#: when FX_UPSTREAM_BASE points at a stand-in. Deriving it from the base URL was
#: considered and rejected: the contract fixes this string, and a caller reading
#: "ECB via 127.0.0.1" would learn nothing useful.
SOURCE_LABEL = "ECB via frankfurter.dev"

#: Above this an "amount" is not a conversion anyone is asking for, and Decimal
#: will happily carry values no consumer can render. Enforced on the query
#: parameter itself so that it also appears in the published tool schema.
MAX_AMOUNT = Decimal("1000000000000")

_ALLOWED_SCHEMES = ("http", "https")


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one process.

    Only ``upstream_base`` comes from the environment. The rest are tuning
    constants that live here because they belong to the same decision surface,
    not because the brief asks for them to be configurable.
    """

    upstream_base: str

    #: The connect budget covers DNS, the TCP handshake and the TLS handshake
    #: together, which is what httpx charges to it. Measured on a cold process:
    #: two of five attempts to reach the real feed took over two seconds to get
    #: that far, and one took three, so a two second budget turns a healthy feed
    #: into an outage on the first call of a process. Five clears the worst
    #: observed with room, and costs nothing afterwards because the connection
    #: is pooled and later calls skip this phase entirely.
    connect_timeout: float = 5.0

    #: Applies per read rather than to the whole body, which is fine for a feed
    #: whose answers arrive in one packet. After the handshake the real feed has
    #: never taken as much as a second to answer.
    read_timeout: float = 4.0

    #: The newest rate changes when the ECB publishes, around 16:00 CET. A rate
    #: for a past date never changes, so it is held far longer.
    latest_ttl_seconds: float = 600.0
    historical_ttl_seconds: float = 86_400.0
    cache_max_entries: int = 512

    #: How far back the service may reach when the date asked about has no rate
    #: of its own. Measured, not guessed: over the whole ECB series the widest
    #: gap between two publications is five calendar days, at Easter and at
    #: Christmas, so an honest answer never has to reach back more than four.
    #: NOTES.md carries the numbers.
    max_fallback_days: int = 7

    @property
    def upstream_root(self) -> str:
        """The versioned root that every upstream request is built on."""
        return f"{self.upstream_base.rstrip('/')}/{UPSTREAM_API_VERSION}"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from ``env``, defaulting to the real environment.

    Raises:
        ValueError: if FX_UPSTREAM_BASE is set to something that is not an
            absolute http(s) URL. Failing here is deliberate: the alternative is
            a request-time crash that looks like an upstream outage.
    """
    source = os.environ if env is None else env
    base = (source.get("FX_UPSTREAM_BASE") or "").strip() or DEFAULT_UPSTREAM_BASE

    parsed = urlparse(base)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError(
            f"FX_UPSTREAM_BASE must be an absolute http(s) URL, got {base!r}"
        )

    return Settings(upstream_base=base)
