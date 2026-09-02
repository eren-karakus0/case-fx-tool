"""The client for the rate feed, and the cache that sits in front of it.

One rule holds this module together: the date a rate belongs to is read out of
the feed's own answer. Nothing here derives it from the question that was asked.

The feed is treated as untrusted input. It is a public service behind a CDN, and
during review it is a stand-in this service has never seen, so every field is
checked before it is believed.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Callable

import httpx

from app.config import Settings
from app.errors import (
    RATE_UNAVAILABLE,
    UNKNOWN_CURRENCY,
    UPSTREAM_BAD_RESPONSE,
    UPSTREAM_ERROR,
    UPSTREAM_TIMEOUT,
    UPSTREAM_UNAVAILABLE,
    FxError,
)

#: The path the feed uses for "whatever you published most recently".
LATEST = "latest"


@dataclass(frozen=True)
class Quote:
    """A rate, and the date its publisher stamped on it."""

    rate: Decimal
    published_on: date


@dataclass(frozen=True)
class RateQuestion:
    """One question for the feed.

    Attributes:
        base: the currency being converted out of.
        target: the currency being converted into.
        on: the date asked about, or None when the caller named no date and
            wants the most recent publication.
        settled: whether the day asked about is already behind us. A settled day
            can never receive a new rate, so its answer is held far longer.
    """

    base: str
    target: str
    on: date | None
    settled: bool

    @property
    def path(self) -> str:
        return LATEST if self.on is None else self.on.isoformat()

    @property
    def cache_key(self) -> tuple[str, str, str]:
        # The date is part of the key. Without it the first rate fetched for a
        # pair would go on answering every later question about that pair.
        return (self.base, self.target, self.path)


class _NotFound(Exception):
    """The feed has no row for this question. Private: callers see an FxError."""


@dataclass(frozen=True)
class _Entry:
    quote: Quote
    expires_at: float


class RateCache:
    """A per-process cache with a size bound and a time to live.

    A repeat of the same question must not reach the feed, which is what the
    brief asks for. Two workers keep two caches, and nothing is invalidated
    early; that is the right size for a tool a model calls, and a shared store
    is not something this task needs.
    """

    def __init__(
        self, max_entries: int, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._entries: OrderedDict[tuple[str, str, str], _Entry] = OrderedDict()
        self._max_entries = max_entries
        self._monotonic = monotonic

    def get(self, key: tuple[str, str, str]) -> Quote | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._monotonic():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry.quote

    def put(self, key: tuple[str, str, str], quote: Quote, ttl_seconds: float) -> None:
        self._entries[key] = _Entry(quote, self._monotonic() + ttl_seconds)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class Upstream:
    """Reads rates from a feed shaped like the one at the configured base URL."""

    def __init__(
        self, settings: Settings, client: httpx.AsyncClient, cache: RateCache
    ) -> None:
        self._settings = settings
        self._client = client
        self._cache = cache
        # The published currency list changes about once a decade, so once it
        # has been read it is kept for the life of the process.
        self._currencies: frozenset[str] | None = None

    async def quote(self, question: RateQuestion) -> Quote:
        """Answer one question, from cache when possible.

        Raises:
            FxError: UNKNOWN_CURRENCY or RATE_UNAVAILABLE when the feed has no
                row for the question, and one of the UPSTREAM_* codes when the
                feed cannot be reached or cannot be believed.
        """
        cached = self._cache.get(question.cache_key)
        if cached is not None:
            return cached

        try:
            payload = await self._get(question.path, question.base, question.target)
        except _NotFound:
            raise await self._explain_not_found(question) from None

        quote = read_quote(payload, question.target)
        ttl = (
            self._settings.historical_ttl_seconds
            if question.settled
            else self._settings.latest_ttl_seconds
        )
        self._cache.put(question.cache_key, quote, ttl)
        return quote

    async def _get(self, path: str, base: str, target: str) -> object:
        """Fetch and decode one feed response.

        Raises:
            _NotFound: the feed has no row for this question.
            FxError: one of the UPSTREAM_* codes.
        """
        url = f"{self._settings.upstream_root}/{path}"
        try:
            response = await self._client.get(
                url, params={"base": base, "symbols": target}
            )
        except httpx.ConnectTimeout as exc:
            # Timing out on the connect itself is an availability problem, not a
            # slow answer: nothing was ever established to be slow about.
            raise FxError(
                UPSTREAM_UNAVAILABLE,
                "The rate source could not be reached. No rate is returned "
                "rather than an old or guessed one; trying again may work.",
            ) from exc
        except httpx.TimeoutException as exc:
            raise FxError(
                UPSTREAM_TIMEOUT,
                "The rate source did not answer in time. No rate is returned "
                "rather than an old or guessed one; trying again may work.",
            ) from exc
        except httpx.RequestError as exc:
            raise FxError(
                UPSTREAM_UNAVAILABLE,
                "The rate source could not be reached. No rate is returned "
                "rather than an old or guessed one; trying again may work.",
            ) from exc

        if response.status_code == 404:
            raise _NotFound()

        if response.status_code != 200:
            raise FxError(
                UPSTREAM_ERROR,
                f"The rate source answered with HTTP {response.status_code}, so "
                f"no rate could be read. Trying again may work.",
            )

        try:
            # parse_float keeps the published rate exact. The default would turn
            # it into a binary float before any of this code ever saw it.
            return response.json(parse_float=Decimal)
        except ValueError as exc:
            raise FxError(
                UPSTREAM_BAD_RESPONSE,
                "The rate source answered with something that is not JSON, so "
                "no rate could be read.",
            ) from exc

    async def _explain_not_found(self, question: RateQuestion) -> FxError:
        """Work out which of two things a 404 from the feed actually means.

        The feed answers an unknown currency and a date its series does not
        cover with the same status, so the currency list is consulted to tell
        them apart. It is fetched here rather than on the way in, which keeps a
        successful conversion down to a single request.
        """
        known = await self._known_currencies()
        if known is not None:
            unknown = [
                code for code in (question.base, question.target) if code not in known
            ]
            if unknown:
                names = " and ".join(unknown)
                verb = (
                    "is not a currency" if len(unknown) == 1 else "are not currencies"
                )
                return FxError(
                    UNKNOWN_CURRENCY,
                    f"{names} {verb} the ECB publishes a euro reference rate for. "
                    f"Known codes: {', '.join(sorted(known))}.",
                )

        asked = (
            question.on.isoformat() if question.on else "the most recent publication"
        )
        return FxError(
            RATE_UNAVAILABLE,
            f"The rate source has no {question.base}/{question.target} rate for "
            f"{asked}. Not every currency's history reaches back to the start of "
            f"the series.",
        )

    async def _known_currencies(self) -> frozenset[str] | None:
        """The codes the feed publishes, or None if the list cannot be read.

        None is not an error. It only means the two meanings of a 404 cannot be
        told apart this time, and the caller falls back to the vaguer of them.
        """
        if self._currencies is not None:
            return self._currencies

        try:
            response = await self._client.get(
                f"{self._settings.upstream_root}/currencies"
            )
            if response.status_code != 200:
                return None
            payload = response.json()
        except (httpx.RequestError, ValueError):
            return None

        if not isinstance(payload, dict) or not payload:
            return None

        self._currencies = frozenset(str(code).upper() for code in payload)
        return self._currencies


def read_quote(payload: object, target: str) -> Quote:
    """Turn a feed response into a Quote, or refuse it.

    Only two things are required: the publication date, and the rate for the
    currency that was asked for. Everything else the real feed sends is ignored,
    so a minimal stand-in during review answers just as well as the real thing.

    Raises:
        FxError: UPSTREAM_BAD_RESPONSE for any shape that cannot be believed.
    """
    if not isinstance(payload, dict):
        raise _bad_response("the body was not a JSON object")

    published = payload.get("date")
    if not isinstance(published, str):
        raise _bad_response("the body carried no publication date")

    try:
        published_on = date.fromisoformat(published)
    except ValueError:
        raise _bad_response(
            f"the publication date {published!r} is not a date"
        ) from None

    rates = payload.get("rates")
    if not isinstance(rates, dict) or target not in rates:
        raise _bad_response(f"the body carried no rate for {target}")

    raw = rates[target]
    try:
        rate = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except InvalidOperation:
        raise _bad_response(f"the rate for {target} was not a number") from None

    if not rate.is_finite() or rate <= 0:
        # A zero or negative rate is not a rate. Passing one through would price
        # the customer's money at nothing.
        raise _bad_response(f"the rate for {target} was {rate}, which is not a rate")

    return Quote(rate=rate, published_on=published_on)


def _bad_response(reason: str) -> FxError:
    return FxError(
        UPSTREAM_BAD_RESPONSE,
        f"The rate source answered in a shape this service cannot trust: "
        f"{reason}. No rate is returned rather than a guessed one.",
    )
