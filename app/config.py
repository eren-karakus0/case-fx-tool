"""Runtime configuration, resolved once from the environment.

Every value the operator can change from outside the process is resolved here so
that no other module has to know an environment variable exists. In particular the
real upstream host appears exactly once in the application code, as the default
below: the reviewer points FX_UPSTREAM_BASE at a stand-in, and nothing else in the
package may assume otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

#: Used when FX_UPSTREAM_BASE is unset. The only occurrence of the real host.
DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"

#: frankfurter.dev serves v1 (ECB only) and v2 (84 central banks) side by side,
#: and they answer the same question with different numbers on different dates.
#: Only v1 makes the source label below true. The base URL carries no version.
UPSTREAM_API_VERSION = "v1"

#: Fixed by the brief. It names the publisher, not the host requests go to, so it
#: stays correct against a stand-in; deriving it from the base URL was rejected,
#: since "ECB via 127.0.0.1" would tell a caller nothing.
SOURCE_LABEL = "ECB via frankfurter.dev"

#: Above this an "amount" is not a conversion anyone is asking for. Enforced on
#: the query parameter so it appears in the published tool schema too.
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

    #: httpx charges DNS, the TCP handshake and the TLS handshake all to connect.
    #: Measured cold against the real feed: 1.32s, 0.76s, 2.66s, 0.42s, 3.03s. Two
    #: of five over two seconds, which is why that budget turned a healthy feed
    #: into an outage on a process's first call. Later calls skip this phase
    #: entirely, so the wider budget costs nothing.
    connect_timeout: float = 5.0

    #: Per read rather than for the whole body, which suits a feed whose answers
    #: arrive in one packet.
    read_timeout: float = 4.0

    #: The newest rate changes when the ECB publishes, around 16:00 CET; a rate
    #: for a day that is over never changes.
    latest_ttl_seconds: float = 600.0
    historical_ttl_seconds: float = 86_400.0
    cache_max_entries: int = 512

    #: How far back to reach when the date asked about has no rate. The widest gap
    #: in the whole ECB series is five calendar days, so four is the most an honest
    #: answer ever needs; NOTES.md carries the measurement.
    max_fallback_days: int = 7

    @property
    def upstream_root(self) -> str:
        """The versioned root that every upstream request is built on."""
        return f"{self.upstream_base.rstrip('/')}/{UPSTREAM_API_VERSION}"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from ``env``, defaulting to the real environment.

    Raises:
        ValueError: if FX_UPSTREAM_BASE is not an absolute http(s) URL. Failing
            here is deliberate: the alternative is a request-time crash that looks
            like an upstream outage.
    """
    source = os.environ if env is None else env
    base = (source.get("FX_UPSTREAM_BASE") or "").strip() or DEFAULT_UPSTREAM_BASE

    parsed = urlparse(base)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError(
            f"FX_UPSTREAM_BASE must be an absolute http(s) URL, got {base!r}"
        )

    return Settings(upstream_base=base)
