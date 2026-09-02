"""The success contract, exactly as the brief documents it."""

from __future__ import annotations

CONVERT = "/tools/convert"
DOCUMENTED_CALL = {"amount": 250, "from": "EUR", "to": "TRY", "date": "2026-08-28"}


def test_the_documented_call_answers_with_the_documented_body(client, feed):
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "47.1234"})

    response = client.get(CONVERT, params=DOCUMENTED_CALL)

    assert response.status_code == 200
    assert response.json() == {
        "amount": 250,
        "from": "EUR",
        "to": "TRY",
        "rate": 47.1234,
        "result": 11780.85,
        "rate_date": "2026-08-28",
        "asked_date": "2026-08-28",
        "source": "ECB via frankfurter.dev",
    }


def test_a_rate_from_the_day_asked_about_carries_no_note(client, feed):
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "47.1234"})

    assert "note" not in client.get(CONVERT, params=DOCUMENTED_CALL).json()


def test_the_published_rate_is_reported_at_full_precision(client, feed):
    # Rounding the rate to two places would turn this into 0.02 and the result
    # into 20000.00, a 3.5% error in whichever direction the customer is going.
    feed.publishes("2026-08-28", on="2026-08-28", rates={"USD": "0.020730"})

    body = client.get(
        CONVERT,
        params={"amount": 1000000, "from": "TRY", "to": "USD", "date": "2026-08-28"},
    ).json()

    assert body["rate"] == 0.02073
    assert body["result"] == 20730.0


def test_the_result_is_rounded_to_the_cent_half_away_from_zero(client, feed):
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "2.675"})

    body = client.get(
        CONVERT,
        params={"amount": 1, "from": "EUR", "to": "TRY", "date": "2026-08-28"},
    ).json()

    # Binary floating point and round() would answer 2.67 here.
    assert body["result"] == 2.68


def test_currency_codes_are_accepted_in_any_case_and_reported_upper_case(client, feed):
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "47.1234"})

    body = client.get(
        CONVERT,
        params={"amount": 250, "from": "eur", "to": "try", "date": "2026-08-28"},
    ).json()

    assert body["from"] == "EUR"
    assert body["to"] == "TRY"


def test_an_amount_with_ten_decimal_places_is_accepted_and_quoted_to_the_cent(
    client, feed
):
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "47.1234"})

    body = client.get(
        CONVERT,
        params={
            "amount": "250.1234567890",
            "from": "EUR",
            "to": "TRY",
            "date": "2026-08-28",
        },
    ).json()

    assert body["amount"] == 250.123456789
    assert body["result"] == 11786.67


def test_an_amount_too_small_to_reach_a_cent_answers_zero_with_the_real_rate(
    client, feed
):
    # 0.00 is the correct value to the cent, and the rate is still reported, so
    # the caller can see what it was converted at.
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "47.1234"})

    body = client.get(
        CONVERT,
        params={
            "amount": "0.0000000001",
            "from": "EUR",
            "to": "TRY",
            "date": "2026-08-28",
        },
    ).json()

    assert body["result"] == 0
    assert body["rate"] == 47.1234


def test_the_source_names_the_publisher_not_the_host_it_was_fetched_from(client, feed):
    # The feed here is http://upstream.invalid. The label still names the ECB,
    # because that is what the rate is, and it is what the contract fixes.
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "47.1234"})

    body = client.get(CONVERT, params=DOCUMENTED_CALL).json()

    assert body["source"] == "ECB via frankfurter.dev"


def test_the_pair_is_asked_of_the_feed_in_the_direction_it_was_given(client, feed):
    feed.publishes("2026-08-28", on="2026-08-28", rates={"EUR": "0.0212"})

    client.get(
        CONVERT,
        params={"amount": 1, "from": "TRY", "to": "EUR", "date": "2026-08-28"},
    )

    request = feed.rate_requests[-1]
    assert request.url.params["base"] == "TRY"
    assert request.url.params["symbols"] == "EUR"
