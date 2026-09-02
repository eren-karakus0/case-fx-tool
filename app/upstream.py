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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

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

#: Two different transport failures say the same thing to the caller, so they say
#: it in the same words.
_UNREACHABLE = (
    "The rate source could not be reached. No rate is returned rather than an "
    "old or guessed one; trying again may work."
)
_TOO_SLOW = (
    "The rate source did not answer in time. No rate is returned rather than an "
    "old or guessed one; trying again may work."
)


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
        today: the current date on the publisher's calendar, which is what makes
            the difference between a day that is over and one that is not.
    """

    base: str
    target: str
    on: date | None
    today: date

    @property
    def settled(self) -> bool:
        """Whether the day asked about is already behind us.

        A settled day can never receive a new rate, which decides both how the
        feed is asked and how long its answer is held. Derived rather than
        passed in: as a field it could be set to True with no date, a state
        nothing here could answer and nothing outside was obliged to avoid.
        """
        return self.on is not None and self.on < self.today

    @property
    def path(self) -> str:
        """Which of the feed's paths answers this question.

        A day that is not settled is asked for as "latest", whether or not the
        caller named it. Asking the feed for today and asking for its most recent
        publication are the same question with the same answer, and treating them
        as one keeps a repeat from reaching the feed twice.
        """
        if self.on is None or self.on >= self.today:
            return LATEST
        return self.on.isoformat()

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
        # The published list changes rarely enough that re-reading it inside one
        # process would never pay for itself, so it is kept once read.
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
        """Fetch one feed response and decode it.

        Raises:
            _NotFound: the feed has no row for this question.
            FxError: one of the UPSTREAM_* codes.
        """
        url = f"{self._settings.upstream_root}/{path}"
        response = await self._fetch(url, {"base": base, "symbols": target})
        return _decode(response)

    async def _fetch(self, url: str, params: dict[str, str]) -> httpx.Response:
        """Perform one request, converting transport failures at the boundary.

        No other module should have to know which HTTP library is underneath, or
        that a timeout and an unreachable host are different exception types.

        Raises:
            FxError: UPSTREAM_UNAVAILABLE or UPSTREAM_TIMEOUT.
        """
        try:
            return await self._client.get(url, params=params)
        except httpx.ConnectTimeout as exc:
            # Timing out on the connect itself is an availability problem rather
            # than a slow answer: nothing was ever established to be slow about.
            raise FxError(UPSTREAM_UNAVAILABLE, _UNREACHABLE) from exc
        except httpx.TimeoutException as exc:
            raise FxError(UPSTREAM_TIMEOUT, _TOO_SLOW) from exc
        except httpx.RequestError as exc:
            raise FxError(UPSTREAM_UNAVAILABLE, _UNREACHABLE) from exc

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
                return FxError(UNKNOWN_CURRENCY, _unknown_currency_message(unknown, known))

        return FxError(RATE_UNAVAILABLE, _no_rate_message(question))

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


def _decode(response: httpx.Response) -> object:
    """Turn a feed response into JSON, or say why it cannot be.

    Raises:
        _NotFound: the feed's 404, which it uses for two different things.
        FxError: UPSTREAM_ERROR or UPSTREAM_BAD_RESPONSE.
    """
    if response.status_code == 404:
        raise _NotFound()

    if response.status_code != 200:
        raise FxError(
            UPSTREAM_ERROR,
            f"The rate source answered with HTTP {response.status_code}, so no "
            f"rate could be read. Trying again may work.",
        )

    try:
        # parse_float keeps the published rate exact. The default would turn it
        # into a binary float before any of this code ever saw it.
        return response.json(parse_float=Decimal)
    except ValueError as exc:
        raise FxError(
            UPSTREAM_BAD_RESPONSE,
            "The rate source answered with something that is not JSON, so no "
            "rate could be read.",
        ) from exc


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

    published_on = _published_date(payload)
    return Quote(rate=_rate_for(payload, target), published_on=published_on)


def _published_date(payload: dict[str, object]) -> date:
    """The date the feed says its rates belong to.

    Raises:
        FxError: UPSTREAM_BAD_RESPONSE when it is absent or is not a date.
    """
    published = payload.get("date")
    if not isinstance(published, str):
        raise _bad_response("the body carried no publication date")

    try:
        return date.fromisoformat(published)
    except ValueError:
        raise _bad_response(
            f"the publication date {published!r} is not a date"
        ) from None


def _rate_for(payload: dict[str, object], target: str) -> Decimal:
    """The published rate for one currency.

    Raises:
        FxError: UPSTREAM_BAD_RESPONSE when it is absent, unreadable, or not a
            positive finite number.
    """
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

    return rate


def _unknown_currency_message(unknown: list[str], known: frozenset[str]) -> str:
    names = " and ".join(unknown)
    verb = "is not a currency" if len(unknown) == 1 else "are not currencies"
    return (
        f"{names} {verb} the ECB publishes a euro reference rate for. "
        f"Known codes: {', '.join(sorted(known))}."
    )


def _no_rate_message(question: RateQuestion) -> str:
    asked = question.on.isoformat() if question.on else "the most recent publication"
    return (
        f"The rate source has no {question.base}/{question.target} rate for "
        f"{asked}. Not every currency's history reaches back to the start of "
        f"the series."
    )


def _bad_response(reason: str) -> FxError:
    return FxError(
        UPSTREAM_BAD_RESPONSE,
        f"The rate source answered in a shape this service cannot trust: "
        f"{reason}. No rate is returned rather than a guessed one.",
    )
