"""The conversion rules, tested as pure functions with no server and no socket."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.convert import (
    ECB_SERIES_START,
    convert_amount,
    ecb_today,
    ensure_distinct,
    ensure_publishable,
    fallback_note,
    normalise_currency,
    resolve_asked_date,
)
from app.errors import FxError

TODAY = date(2026, 9, 2)


# --- what "today" means ------------------------------------------------------

def test_today_follows_the_publishers_calendar_not_utc():
    # 23:30 UTC is already the next day in Frankfurt. A service reading UTC would
    # call that date "the future" and refuse a rate the ECB is about to publish.
    late_in_utc = datetime(2026, 9, 1, 23, 30, tzinfo=timezone.utc)
    assert ecb_today(late_in_utc) == date(2026, 9, 2)


def test_today_is_still_yesterday_in_frankfurt_early_in_a_far_eastern_morning():
    # 00:30 in Istanbul (UTC+3) is 23:30 the previous day in Frankfurt. Reading
    # the server clock here would accept a date the ECB cannot have published.
    istanbul = timezone(timedelta(hours=3))
    istanbul_midnight = datetime(2026, 9, 2, 0, 30, tzinfo=istanbul)
    assert ecb_today(istanbul_midnight) == date(2026, 9, 1)


# --- currency codes ----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("eur", "EUR"), (" try ", "TRY"), ("Usd", "USD")])
def test_currency_codes_are_upper_cased(raw, expected):
    assert normalise_currency(raw) == expected


def test_a_currency_cannot_be_converted_into_itself():
    with pytest.raises(FxError) as raised:
        ensure_distinct("EUR", "EUR")
    assert raised.value.error.code == "same_currency"
    # The refusal still carries the answer, so the caller loses nothing by it.
    assert "1:1" in raised.value.message


def test_two_different_currencies_are_accepted():
    assert ensure_distinct("EUR", "TRY") is None


# --- which date is being asked about -----------------------------------------

def test_no_date_means_today_on_the_publishers_calendar():
    assert resolve_asked_date(None, TODAY) == TODAY


def test_today_is_not_the_future():
    assert resolve_asked_date(TODAY, TODAY) == TODAY


def test_tomorrow_is_refused_without_asking_anyone():
    with pytest.raises(FxError) as raised:
        resolve_asked_date(date(2026, 9, 3), TODAY)
    assert raised.value.error.code == "date_in_future"


def test_the_first_day_of_the_series_is_accepted():
    assert resolve_asked_date(ECB_SERIES_START, TODAY) == ECB_SERIES_START


def test_the_day_before_the_series_starts_is_refused():
    with pytest.raises(FxError) as raised:
        resolve_asked_date(date(1999, 1, 3), TODAY)
    assert raised.value.error.code == "date_before_series"
    assert "1999-01-04" in raised.value.message


# --- may this published rate answer that question? ---------------------------

def test_a_rate_from_the_day_asked_about_is_always_publishable():
    assert ensure_publishable(TODAY, TODAY, max_fallback_days=7) is None


@pytest.mark.parametrize("gap", [1, 3, 5, 7])
def test_a_gap_within_the_ceiling_is_accepted(gap):
    # Five is the longest gap the ECB has left in the whole series (Easter and
    # Christmas); the ceiling is seven so those days answer normally.
    asked = date(2026, 4, 7)
    published = date.fromordinal(asked.toordinal() - gap)
    assert ensure_publishable(asked, published, max_fallback_days=7) is None


def test_a_rate_older_than_the_ceiling_is_refused_rather_than_quoted():
    with pytest.raises(FxError) as raised:
        ensure_publishable(date(2026, 9, 2), date(2026, 8, 20), max_fallback_days=7)
    assert raised.value.error.code == "rate_too_stale"
    assert raised.value.status == 404


def test_a_rate_published_after_the_question_is_treated_as_a_broken_upstream():
    # No correct upstream produces this. If a stand-in does, the service must not
    # stamp a later rate onto an earlier date.
    with pytest.raises(FxError) as raised:
        ensure_publishable(date(2026, 8, 28), date(2026, 9, 1), max_fallback_days=7)
    assert raised.value.error.code == "upstream_bad_response"


# --- the arithmetic ----------------------------------------------------------

def test_a_plain_conversion():
    assert convert_amount(Decimal("250"), Decimal("56.1718")) == Decimal("14042.95")


def test_the_result_rounds_half_away_from_zero_not_half_to_even():
    assert convert_amount(Decimal("1"), Decimal("2.675")) == Decimal("2.68")
    # The same arithmetic in binary floating point, which is what round() gives:
    assert round(float(Decimal("1")) * float(Decimal("2.675")), 2) == 2.67


def test_the_full_precision_of_the_rate_is_used_before_rounding():
    # Rounding the rate first, as 0.02, would make this 20000.00.
    assert convert_amount(Decimal("1000000"), Decimal("0.020730")) == Decimal("20730.00")


def test_an_amount_below_half_a_cent_converts_to_zero_and_says_so():
    # Honest: the rate is still reported, and 0.00 is the correct value to the
    # cent. This is documented rather than treated as an error.
    assert convert_amount(Decimal("0.0000000001"), Decimal("56.1718")) == Decimal("0.00")


def test_the_largest_accepted_amount_stays_exact_to_the_cent():
    # 1e12 at the largest rate the ECB publishes is the widest this can get.
    result = convert_amount(Decimal("1000000000000"), Decimal("20566.11"))
    assert result == Decimal("20566110000000000.00")


# --- the sentence that makes a fallback visible ------------------------------

def test_a_rate_from_the_day_asked_about_carries_no_note():
    assert fallback_note(TODAY, TODAY) is None


def test_a_one_day_fallback_reads_as_one_day():
    note = fallback_note(date(2026, 9, 2), date(2026, 9, 1))
    assert "1 day earlier" in note


def test_a_weekend_fallback_names_both_dates():
    note = fallback_note(date(2026, 8, 30), date(2026, 8, 28))
    assert "2026-08-30" in note and "2026-08-28" in note and "2 days earlier" in note
