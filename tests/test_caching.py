"""A repeat of the same question must not reach the feed, and a different
question must.

The second half is the one that bites. A cache keyed on the currency pair alone
still satisfies "do not re-ask", while answering every later caller with the
first rate it ever fetched.
"""

from __future__ import annotations


def test_the_same_question_twice_reaches_the_feed_once(ask, feed):
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "56.1718"})

    first = ask(amount=250, on="2026-08-28")
    second = ask(amount=250, on="2026-08-28")

    assert feed.calls_to("2026-08-28") == 1
    assert first.json() == second.json()


def test_asking_for_today_and_asking_for_the_latest_are_one_question(ask, feed):
    # Same answer, so it must cost one request rather than two. Keyed on the
    # literal date these would be two entries for one day's data.
    feed.publishes("latest", on="2026-09-01", rates={"TRY": "55.9498"})

    without_a_date = ask(amount=1).json()
    naming_today = ask(amount=1, on="2026-09-02").json()

    assert without_a_date == naming_today
    assert feed.calls_to("latest") == 1
    assert feed.calls_to("2026-09-02") == 0


def test_a_different_amount_is_the_same_question_about_the_rate(ask, feed):
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "56.1718"})

    ask(amount=250, on="2026-08-28")
    body = ask(amount=100, on="2026-08-28").json()

    assert feed.calls_to("2026-08-28") == 1
    assert body["result"] == 5617.18


def test_the_same_pair_on_a_different_day_is_a_different_question(ask, feed):
    # The failure this guards against: the March 2020 rate going on to answer
    # every later question about EUR/TRY, wearing whatever date was asked for.
    feed.publishes("2020-03-16", on="2020-03-16", rates={"TRY": "7.1"})
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "56.1718"})

    old = ask(amount=1, on="2020-03-16").json()
    new = ask(amount=1, on="2026-08-28").json()

    assert old["rate"] == 7.1
    assert new["rate"] == 56.1718
    assert old["rate_date"] == "2020-03-16"
    assert new["rate_date"] == "2026-08-28"


def test_a_historical_answer_does_not_leak_into_the_latest_one(ask, feed):
    feed.publishes("2020-03-16", on="2020-03-16", rates={"TRY": "7.1"})
    feed.publishes("latest", on="2026-09-01", rates={"TRY": "55.9498"})

    ask(amount=1, on="2020-03-16")
    body = ask(amount=1).json()

    assert body["rate"] == 55.9498
    assert body["rate_date"] == "2026-09-01"


def test_a_different_pair_is_a_different_question(ask, feed):
    feed.publishes("latest", on="2026-09-01", rates={"TRY": "55.9498", "USD": "1.16"})

    to_try = ask(amount=1, to="TRY").json()
    to_usd = ask(amount=1, to="USD").json()

    assert to_try["rate"] == 55.9498
    assert to_usd["rate"] == 1.16
    assert feed.calls_to("latest") == 2


def test_the_newest_rate_is_re_asked_once_its_short_life_has_passed(ask, feed, clock):
    # A new rate appears around 16:00 CET, so today's answer is held minutes
    # rather than hours.
    feed.publishes("latest", on="2026-09-01", rates={"TRY": "55.9498"})

    ask(amount=1)
    clock.advance(601)
    ask(amount=1)

    assert feed.calls_to("latest") == 2


def test_a_settled_day_is_held_far_longer_than_the_newest_rate(ask, feed, clock):
    # A rate for a day that is over cannot change, so re-asking is pure cost.
    feed.publishes("2026-08-28", on="2026-08-28", rates={"TRY": "56.1718"})

    ask(amount=1, on="2026-08-28")
    clock.advance(3_600)
    ask(amount=1, on="2026-08-28")

    assert feed.calls_to("2026-08-28") == 1


def test_a_failure_is_not_remembered_as_if_it_were_an_answer(ask, feed):
    # A cached failure would keep answering long after the feed recovered.
    feed.answers("latest", body='{"message": "boom"}', status=500)
    assert ask(amount=1).status_code == 502

    feed.publishes("latest", on="2026-09-01", rates={"TRY": "55.9498"})
    assert ask(amount=1).status_code == 200
