"""What the endpoint refuses before it asks anyone anything.

Every case here has to leave in the service's own error contract rather than in
the web framework's, and none of them may reach the feed.
"""

from __future__ import annotations

import pytest

from app.main import CONVERT_PATH

GOOD = {"amount": "250", "from": "EUR", "to": "TRY"}


def call(client, **overrides):
    params = dict(GOOD)
    for name, value in overrides.items():
        if value is None:
            params.pop(name, None)
        else:
            params[name] = value
    return client.get(CONVERT_PATH, params=params)


# --- amount ------------------------------------------------------------------


def test_a_missing_amount_is_reported_as_missing(client, feed):
    response = call(client, amount=None)

    assert response.status_code == 400
    assert response.json()["error"] == "missing_parameter"
    assert feed.requests == []


@pytest.mark.parametrize(
    "amount",
    ["abc", "", "1,5", "250 TRY"],
    ids=["letters", "empty", "comma", "with-unit"],
)
def test_an_amount_that_is_not_a_number_is_refused(client, amount):
    response = call(client, amount=amount)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


@pytest.mark.parametrize("amount", ["nan", "inf", "-inf", "Infinity"])
def test_an_amount_that_is_not_finite_is_refused(client, amount):
    # These parse as decimals. Left alone they would travel through the whole
    # service and come back out as a null the caller cannot act on.
    response = call(client, amount=amount)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


@pytest.mark.parametrize("amount", ["0", "0.00", "-1", "-250.75"])
def test_an_amount_of_zero_or_less_is_refused(client, amount):
    response = call(client, amount=amount)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


@pytest.mark.parametrize("amount", ["1000000000001", "1e400"])
def test_an_amount_above_the_ceiling_is_refused(client, amount):
    response = call(client, amount=amount)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


def test_the_largest_accepted_amount_is_accepted(client, feed):
    feed.publishes("latest", on="2026-09-02", rates={"TRY": "2.0"})

    assert call(client, amount="1000000000000").status_code == 200


# --- currencies --------------------------------------------------------------


@pytest.mark.parametrize("field", ["from", "to"])
def test_a_missing_currency_is_reported_as_missing(client, field):
    response = call(client, **{field: None})

    assert response.status_code == 400
    assert response.json()["error"] == "missing_parameter"


@pytest.mark.parametrize("field", ["from", "to"])
@pytest.mark.parametrize("code", ["EU", "EURO", "E1R", "", "€€€"])
def test_a_code_that_is_not_three_letters_is_refused(client, field, code):
    response = call(client, **{field: code})

    assert response.status_code == 400
    assert response.json()["error"] == "unknown_currency"


def test_converting_a_currency_into_itself_is_refused_with_the_answer_in_words(
    client, feed
):
    response = call(client, to="EUR")

    assert response.status_code == 400
    assert response.json()["error"] == "same_currency"
    assert "1:1" in response.json()["message"]
    assert feed.requests == []


def test_the_same_currency_check_ignores_case(client):
    response = call(client, **{"from": "eur", "to": "EUR"})

    assert response.json()["error"] == "same_currency"


# --- date --------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["yesterday", "2026-13-45", "28-08-2026", "2026/08/28", ""],
    ids=["word", "impossible", "day-first", "slashes", "empty"],
)
def test_a_date_that_is_not_a_calendar_date_is_refused(client, value):
    response = call(client, date=value)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_date"


def test_a_date_carrying_a_time_of_day_is_refused_and_told_why(client):
    response = call(client, date="2026-08-28T10:00:00")

    assert response.json()["error"] == "invalid_date"
    assert "no time of day" in response.json()["message"]


# --- the shape of every refusal ---------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": None},
        {"amount": "abc"},
        {"amount": "-1"},
        {"from": "EU"},
        {"to": "EUR"},
        {"date": "yesterday"},
        {"date": "2500-01-01"},
        {"date": "1900-01-01"},
    ],
)
def test_every_refusal_carries_a_code_and_a_sentence_and_nothing_else(
    client, overrides
):
    body = call(client, **overrides).json()

    assert set(body) == {"error", "message"}
    assert body["message"].endswith((".", "?"))
