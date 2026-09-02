"""The cache in front of the feed, and the key it stores things under.

The key is the part that matters. A cache keyed only by the currency pair will
answer every later question with the first rate it ever fetched, wearing whatever
date the new caller asked about, and it looks completely normal while doing it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.upstream import Quote, RateCache, RateQuestion


class Clock:
    """A monotonic clock the test drives by hand."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def quote(rate: str = "56.1718", on: str = "2026-08-28") -> Quote:
    return Quote(rate=Decimal(rate), published_on=date.fromisoformat(on))


KEY = ("EUR", "TRY", "2026-08-28")
OTHER_KEY = ("EUR", "TRY", "latest")


# --- the key -----------------------------------------------------------------


def test_no_date_asks_the_feed_for_its_latest_publication():
    question = RateQuestion(base="EUR", target="TRY", on=None, settled=False)
    assert question.path == "latest"


def test_a_named_date_becomes_the_path():
    question = RateQuestion(
        base="EUR", target="TRY", on=date(2026, 8, 28), settled=True
    )
    assert question.path == "2026-08-28"


def test_the_same_pair_on_two_dates_is_two_different_questions():
    march_2020 = RateQuestion("EUR", "TRY", date(2020, 3, 16), settled=True)
    latest = RateQuestion("EUR", "TRY", None, settled=False)
    assert march_2020.cache_key != latest.cache_key


def test_two_pairs_on_the_same_date_are_two_different_questions():
    to_try = RateQuestion("EUR", "TRY", date(2026, 8, 28), settled=True)
    to_usd = RateQuestion("EUR", "USD", date(2026, 8, 28), settled=True)
    assert to_try.cache_key != to_usd.cache_key


def test_the_direction_of_the_pair_is_part_of_the_question():
    forward = RateQuestion("EUR", "TRY", None, settled=False)
    reverse = RateQuestion("TRY", "EUR", None, settled=False)
    assert forward.cache_key != reverse.cache_key


# --- storing and expiring ----------------------------------------------------


def test_an_empty_cache_answers_nothing():
    assert RateCache(max_entries=4).get(KEY) is None


def test_a_stored_quote_comes_back_unchanged():
    cache = RateCache(max_entries=4)
    stored = quote()
    cache.put(KEY, stored, ttl_seconds=600)
    assert cache.get(KEY) == stored


def test_a_quote_is_gone_once_its_time_to_live_has_passed():
    clock = Clock()
    cache = RateCache(max_entries=4, monotonic=clock)
    cache.put(KEY, quote(), ttl_seconds=600)

    clock.advance(599)
    assert cache.get(KEY) is not None

    clock.advance(2)
    assert cache.get(KEY) is None


def test_an_expired_quote_is_dropped_rather_than_left_to_accumulate():
    clock = Clock()
    cache = RateCache(max_entries=4, monotonic=clock)
    cache.put(KEY, quote(), ttl_seconds=10)
    clock.advance(11)
    cache.get(KEY)
    assert len(cache) == 0


# --- the size bound ----------------------------------------------------------


def test_the_least_recently_used_entry_is_evicted_first():
    cache = RateCache(max_entries=2)
    cache.put(("EUR", "TRY", "a"), quote(), ttl_seconds=600)
    cache.put(("EUR", "TRY", "b"), quote(), ttl_seconds=600)
    cache.put(("EUR", "TRY", "c"), quote(), ttl_seconds=600)

    assert len(cache) == 2
    assert cache.get(("EUR", "TRY", "a")) is None
    assert cache.get(("EUR", "TRY", "c")) is not None


def test_reading_an_entry_makes_it_recent_again():
    cache = RateCache(max_entries=2)
    cache.put(("EUR", "TRY", "a"), quote(), ttl_seconds=600)
    cache.put(("EUR", "TRY", "b"), quote(), ttl_seconds=600)

    cache.get(("EUR", "TRY", "a"))  # "a" is now the newer of the two
    cache.put(("EUR", "TRY", "c"), quote(), ttl_seconds=600)

    assert cache.get(("EUR", "TRY", "a")) is not None
    assert cache.get(("EUR", "TRY", "b")) is None


def test_writing_the_same_key_twice_replaces_rather_than_grows():
    cache = RateCache(max_entries=4)
    cache.put(KEY, quote(rate="1"), ttl_seconds=600)
    cache.put(KEY, quote(rate="2"), ttl_seconds=600)

    assert len(cache) == 1
    assert cache.get(KEY).rate == Decimal("2")


def test_two_keys_do_not_share_an_entry():
    cache = RateCache(max_entries=4)
    cache.put(KEY, quote(rate="1"), ttl_seconds=600)
    cache.put(OTHER_KEY, quote(rate="2"), ttl_seconds=600)

    assert cache.get(KEY).rate == Decimal("1")
    assert cache.get(OTHER_KEY).rate == Decimal("2")
