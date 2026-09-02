"""What the endpoint answers when the day asked about has no rate of its own.

This is the part of the task the brief calls the point of it, so the behaviour is
pinned from both directions: the answer that is given, and the answer that is
refused.
"""

from __future__ import annotations


def test_a_weekend_is_answered_with_the_last_publication_before_it(ask, feed):
    # The real feed answers a Saturday with 200 and the preceding Friday's date.
    # A service that never read that field would present this as Saturday's rate.
    feed.publishes("2026-08-29", on="2026-08-28", rates={"TRY": "56.1718"})

    body = ask(amount=100, on="2026-08-29").json()

    assert body["rate_date"] == "2026-08-28"
    assert body["asked_date"] == "2026-08-29"


def test_a_fallback_says_in_words_which_day_the_number_is_from(ask, feed):
    # The caller is a model that has to tell a customer which day this is. The
    # two dates already differ; the sentence makes it repeatable.
    feed.publishes("2026-08-30", on="2026-08-28", rates={"TRY": "56.1718"})

    note = ask(amount=100, on="2026-08-30").json()["note"]

    assert "2026-08-30" in note
    assert "2026-08-28" in note
    assert "2 days earlier" in note


def test_the_longest_gap_the_ecb_has_ever_left_is_still_answered(ask, feed):
    # 2026-04-02 to 2026-04-07 is the Easter gap: five calendar days, and the
    # widest in the series since 1999. The ceiling is seven, so it answers.
    feed.publishes("2026-04-06", on="2026-04-02", rates={"TRY": "50.0"})

    response = ask(amount=1, on="2026-04-06")

    assert response.status_code == 200
    assert response.json()["rate_date"] == "2026-04-02"


def test_a_rate_older_than_the_ceiling_is_refused_rather_than_quoted(ask, feed):
    # Twelve days behind is not a holiday, it is a feed that has stopped.
    feed.publishes("2026-09-01", on="2026-08-20", rates={"TRY": "56.0"})

    response = ask(amount=1, on="2026-09-01")

    assert response.status_code == 404
    assert response.json()["error"] == "rate_too_stale"


def test_a_rate_published_after_the_day_asked_about_is_refused(ask, feed):
    # No correct feed does this. If a stand-in does, the answer must not be
    # stamped onto the earlier date.
    feed.publishes("2026-08-20", on="2026-09-01", rates={"TRY": "56.0"})

    response = ask(amount=1, on="2026-08-20")

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_bad_response"


def test_a_future_date_is_refused_without_asking_the_feed(ask, feed):
    # The real feed answers dates up to a fortnight ahead with 200 and its most
    # recent rate. Passing one through would quote a day that has not happened.
    response = ask(on="2026-09-03")

    assert response.status_code == 400
    assert response.json()["error"] == "date_in_future"
    assert feed.requests == []


def test_a_date_before_the_series_begins_is_refused_without_asking_the_feed(ask, feed):
    response = ask(on="1999-01-03")

    assert response.status_code == 400
    assert response.json()["error"] == "date_before_series"
    assert feed.requests == []


def test_the_first_day_of_the_series_is_a_normal_question(ask, feed):
    feed.publishes("1999-01-04", on="1999-01-04", rates={"TRY": "0.372274"})

    response = ask(amount=1, on="1999-01-04")

    assert response.status_code == 200
    assert response.json()["rate"] == 0.372274


def test_today_is_not_the_future(ask, feed):
    # Today is asked of the feed as "latest", because a day that is not over
    # cannot have a rate of its own yet that "latest" would not already give.
    feed.publishes("latest", on="2026-09-02", rates={"TRY": "56.0"})

    assert ask(amount=1, on="2026-09-02").status_code == 200


def test_leaving_the_date_out_asks_the_feed_for_its_latest_publication(ask, feed):
    feed.publishes("latest", on="2026-09-01", rates={"TRY": "55.9498"})

    body = ask(amount=1).json()

    assert feed.calls_to("latest") == 1
    assert body["rate_date"] == "2026-09-01"


def test_an_answer_that_predates_today_is_flagged_even_when_no_date_was_asked_for(
    ask, feed
):
    # Before 16:00 CET there is no rate for today yet. Reporting today as the
    # asked date and yesterday as the rate date is what makes that visible,
    # rather than quietly presenting yesterday's number as current.
    feed.publishes("latest", on="2026-09-01", rates={"TRY": "55.9498"})

    body = ask(amount=1).json()

    assert body["asked_date"] == "2026-09-02"
    assert body["rate_date"] == "2026-09-01"
    assert "1 day earlier" in body["note"]
