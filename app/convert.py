"""The rules a conversion obeys, independent of HTTP and of any upstream.

Two of them are the point of the whole task:

* the date a rate belongs to is the date its publisher stamped on it, never the
  date the caller asked about;
* when the honest answer is "no rate", the service says so instead of reaching
  for the nearest number.

Everything here is a pure function over values, so the policy can be read and
tested without a server or a socket.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from app.errors import (
    DATE_BEFORE_SERIES,
    DATE_IN_FUTURE,
    RATE_TOO_STALE,
    SAME_CURRENCY,
    UPSTREAM_BAD_RESPONSE,
    FxError,
)

#: The ECB publishes from Frankfurt, so "today" is today there. Resolved at import
#: so a missing time zone database fails the process at startup rather than the
#: first request, where it would look like an outage.
ECB_TIMEZONE = ZoneInfo("Europe/Berlin")

#: First day of the euro reference rate series. Individual currencies join later:
#: EUR/BRL, for instance, has no rate on this date even though EUR/USD does.
ECB_SERIES_START = date(1999, 1, 4)

#: Results are quoted to the cent. Doing this per currency (JPY and KRW have no
#: minor unit) was left out; see NOTES.md.
CENTS = Decimal("0.01")


def ecb_today(now: datetime | None = None) -> date:
    """The current date on the publisher's calendar.

    Args:
        now: an aware datetime to read instead of the clock, for tests.
    """
    moment = datetime.now(ECB_TIMEZONE) if now is None else now.astimezone(ECB_TIMEZONE)
    return moment.date()


def normalise_currency(raw: str) -> str:
    """Upper-case a currency code that has already passed the shape check."""
    return raw.strip().upper()


def ensure_distinct(base: str, target: str) -> None:
    """Refuse a conversion between a currency and itself.

    There is no published rate for EUR/EUR, so answering 1.0 would mean stamping
    a number with a source and a publication date it does not have. The message
    carries the answer the caller was after, so nothing is lost by refusing.

    Raises:
        FxError: SAME_CURRENCY, when the two codes are equal.
    """
    if base == target:
        raise FxError(
            SAME_CURRENCY,
            f"'from' and 'to' are both {base}. Converting a currency into itself "
            f"is 1:1 and no exchange rate is involved, so the ECB publishes none.",
        )


def resolve_asked_date(requested: date | None, today: date) -> date:
    """Decide which date the caller is asking about, and refuse impossible ones.

    Args:
        requested: the caller's ``date`` parameter, or None for "the latest".
        today: the current date on the publisher's calendar.

    Returns:
        The date the answer will be reported against.

    Raises:
        FxError: DATE_IN_FUTURE or DATE_BEFORE_SERIES.
    """
    if requested is None:
        return today

    if requested > today:
        raise FxError(
            DATE_IN_FUTURE,
            f"No rate can exist for {requested.isoformat()}: it is in the future. "
            f"Today on the publisher's calendar is {today.isoformat()}. Leave "
            f"'date' out to use the most recently published rate.",
        )

    if requested < ECB_SERIES_START:
        raise FxError(
            DATE_BEFORE_SERIES,
            f"The ECB euro reference rate series begins on "
            f"{ECB_SERIES_START.isoformat()}, and {requested.isoformat()} is "
            f"before it.",
        )

    return requested


def ensure_publishable(asked: date, published: date, max_fallback_days: int) -> None:
    """Check that a published rate may honestly answer the date asked about.

    Args:
        asked: the date the caller asked about.
        published: the date the upstream says its rate belongs to.
        max_fallback_days: how far back an answer may reach.

    Raises:
        FxError: UPSTREAM_BAD_RESPONSE if the rate is newer than the question,
            which no correct upstream can produce; RATE_TOO_STALE if it is older
            than the service is willing to present.
    """
    if published > asked:
        raise FxError(
            UPSTREAM_BAD_RESPONSE,
            f"The upstream answered {asked.isoformat()} with a rate published on "
            f"{published.isoformat()}, which is later than the date asked about. "
            f"No rate is returned rather than one from the wrong day.",
        )

    gap = (asked - published).days
    if gap > max_fallback_days:
        raise FxError(
            RATE_TOO_STALE,
            f"The newest rate on or before {asked.isoformat()} is from "
            f"{published.isoformat()}, {gap} days earlier. This service does not "
            f"present a rate more than {max_fallback_days} days older than the "
            f"date asked about.",
        )


def convert_amount(amount: Decimal, rate: Decimal) -> Decimal:
    """Multiply and round to the cent, half away from zero.

    No binary float touches this path. Python's round() rounds half to even, so
    the same arithmetic there turns 11780.845 into 11780.84 rather than the
    11780.85 a customer reading an invoice expects.

    The default decimal context is wide enough. The largest amount this service
    accepts times the largest rate the ECB publishes is about 2.1e16, which is
    nineteen digits once quantised to the cent, well inside the twenty-eight the
    context carries; any rounding it does happens far below the cent.
    """
    return (amount * rate).quantize(CENTS, rounding=ROUND_HALF_UP)


def fallback_note(asked: date, published: date) -> str | None:
    """A sentence the caller can repeat when the rate is not from the day asked for.

    Returns None when the rate does belong to that day, so an ordinary answer
    does not carry a field that says nothing.
    """
    if published == asked:
        return None

    days = (asked - published).days
    unit = "day" if days == 1 else "days"
    return (
        f"The ECB published no rate for {asked.isoformat()}. This is the rate "
        f"published on {published.isoformat()}, {days} {unit} earlier."
    )
