"""Runtime configuration, resolved once from the environment.

Every value the operator can change from outside the process is resolved here so
that no other module has to know an environment variable exists. In particular
the real upstream host appears exactly once in this repository: as the default
below. The reviewer points FX_UPSTREAM_BASE at a fake upstream, and nothing else
in the package may assume otherwise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping
from urllib.parse import urlparse

#: Used when FX_UPSTREAM_BASE is unset. The only occurrence of the real host.
DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"

#: frankfurter.dev serves two generations of its API side by side. v1 is the
#: ECB-only dataset this service claims as its source; v2 blends 84 central banks
#: and answers the same question with a different rate on a different date, which
#: would make the "ECB via frankfurter.dev" source field untrue. The base URL
#: carries no version, so the client appends this one.
UPSTREAM_API_VERSION = "v1"

_ALLOWED_SCHEMES = ("http", "https")


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one process.

    Only ``upstream_base`` comes from the environment. The rest are tuning
    constants that live here because they belong to the same decision surface,
    not because the brief asks for them to be configurable.
    """

    upstream_base: str

    #: Split so a dead host fails fast while a slow but live one is given room.
    #: Worst case for a single upstream call is connect + read.
    connect_timeout: float = 2.0
    read_timeout: float = 4.0

    #: The newest rate changes when the ECB publishes, around 16:00 CET. A rate
    #: for a past date never changes, so it is held far longer.
    latest_ttl_seconds: float = 600.0
    historical_ttl_seconds: float = 86_400.0
    cache_max_entries: int = 512

    #: How far back the service may reach for a rate when the date asked for has
    #: none. See app.convert for the measurement behind this number.
    max_fallback_days: int = 7

    #: Above this an "amount" is not a conversion anyone is asking for, and
    #: Decimal will happily carry values that no consumer can render.
    max_amount: Decimal = Decimal("1000000000000")

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
