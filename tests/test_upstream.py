"""Reading a feed response, treating it as something that has to earn belief.

During review the feed is a stand-in this service has never seen. Anything it
sends that cannot be checked is refused, because the one thing worse than no
answer here is a confident wrong one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.errors import FxError
from app.upstream import read_quote


def body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "amount": 1.0,
        "base": "EUR",
        "date": "2026-08-28",
        "rates": {"TRY": Decimal("56.1718")},
    }
    payload.update(overrides)
    return payload


def refusal(payload: object, target: str = "TRY") -> FxError:
    with pytest.raises(FxError) as raised:
        read_quote(payload, target)
    return raised.value


# --- what a good answer yields ----------------------------------------------


def test_the_rate_and_the_date_it_belongs_to_are_both_read_from_the_body():
    quote = read_quote(body(), "TRY")
    assert quote.rate == Decimal("56.1718")
    assert quote.published_on == date(2026, 8, 28)


def test_the_published_date_is_taken_as_given_even_when_it_is_not_the_one_asked_for():
    # The feed answers a Saturday with the preceding Friday's publication. That
    # date is the answer, not something to be corrected towards the question.
    quote = read_quote(body(date="2026-08-28"), "TRY")
    assert quote.published_on == date(2026, 8, 28)


def test_the_full_precision_of_the_published_rate_survives():
    quote = read_quote(body(rates={"TRY": Decimal("0.020730")}), "TRY")
    assert quote.rate == Decimal("0.020730")


def test_a_whole_number_rate_is_a_rate():
    quote = read_quote(body(rates={"JPY": 172}), "JPY")
    assert quote.rate == Decimal("172")


def test_a_rate_sent_as_a_string_is_accepted():
    # Not what the real feed does, but a stand-in might, and the value is
    # unambiguous. Being strict here would fail a review for no benefit.
    quote = read_quote(body(rates={"TRY": "56.1718"}), "TRY")
    assert quote.rate == Decimal("56.1718")


def test_only_the_date_and_the_rate_asked_for_are_required():
    # A minimal stand-in must be able to answer. Requiring "base" or "amount"
    # would refuse a response that carries everything actually needed.
    quote = read_quote({"date": "2026-08-28", "rates": {"TRY": Decimal("1.5")}}, "TRY")
    assert quote.rate == Decimal("1.5")


def test_fields_this_service_does_not_use_are_ignored():
    quote = read_quote(body(unexpected={"nested": [1, 2, 3]}), "TRY")
    assert quote.published_on == date(2026, 8, 28)


# --- what is refused ---------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        ["a", "list"],
        None,
        42,
    ],
    ids=["string", "list", "null", "number"],
)
def test_a_body_that_is_not_an_object_is_refused(payload):
    assert refusal(payload).error.code == "upstream_bad_response"


def test_a_body_with_no_publication_date_is_refused():
    payload = body()
    del payload["date"]
    assert refusal(payload).error.code == "upstream_bad_response"


@pytest.mark.parametrize("value", ["yesterday", "2026-13-45", "", 20260828])
def test_a_publication_date_that_is_not_a_date_is_refused(value):
    assert refusal(body(date=value)).error.code == "upstream_bad_response"


def test_a_body_with_no_rates_at_all_is_refused():
    payload = body()
    del payload["rates"]
    assert refusal(payload).error.code == "upstream_bad_response"


def test_a_body_without_the_currency_that_was_asked_for_is_refused():
    # This is the branch that matters: answering anyway would mean reaching for
    # some other number in the body.
    error = refusal(body(rates={"USD": Decimal("1.16")}), "TRY")
    assert error.error.code == "upstream_bad_response"
    assert "TRY" in error.message


@pytest.mark.parametrize("value", ["abc", True, None, {"nested": 1}])
def test_a_rate_that_is_not_a_number_is_refused(value):
    assert refusal(body(rates={"TRY": value})).error.code == "upstream_bad_response"


@pytest.mark.parametrize("value", ["0", "0.00", "-1.5"])
def test_a_rate_of_zero_or_less_is_not_a_rate(value):
    # Passing one through would price the customer's money at nothing, which is
    # exactly the failure this service exists to avoid.
    error = refusal(body(rates={"TRY": Decimal(value)}))
    assert error.error.code == "upstream_bad_response"


def test_every_refusal_is_reported_as_a_bad_gateway():
    # The caller did nothing wrong, so this must not read as a client error.
    assert refusal(body(rates={})).status == 502


def test_a_refusal_says_what_could_not_be_believed():
    message = refusal(body(rates={"USD": Decimal("1.16")}), "TRY").message
    assert "no rate for TRY" in message
