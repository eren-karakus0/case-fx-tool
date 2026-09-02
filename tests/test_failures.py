"""What the endpoint answers when the feed is slow, down, or not to be believed.

None of these may produce a number. The brief's rule is that a wrong number is
worse than no number, and every case here is a chance to break it.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.config import load_settings
from app.main import CONVERT_PATH, Runtime, app, get_runtime
from tests.support import FAKE_BASE


def test_a_feed_that_answers_with_a_server_error_is_a_bad_gateway(ask, feed):
    feed.answers("latest", body='{"message": "boom"}', status=500)

    response = ask(amount=1)

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_error"
    assert "500" in response.json()["message"]


def test_a_feed_that_answers_with_html_is_refused(ask, feed):
    # A proxy or a CDN error page is the usual way this happens.
    feed.answers(
        "latest",
        body="<html><body>502 Bad Gateway</body></html>",
        content_type="text/html",
    )

    response = ask(amount=1)

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_bad_response"


def test_a_feed_that_answers_with_json_of_the_wrong_shape_is_refused(ask, feed):
    feed.answers("latest", body='{"unexpected": true}')

    response = ask(amount=1)

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_bad_response"


def test_a_feed_that_omits_the_currency_that_was_asked_for_is_refused(ask, feed):
    feed.publishes("latest", on="2026-09-01", rates={"USD": "1.16"})

    response = ask(amount=1, to="TRY")

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_bad_response"


def test_a_feed_that_cannot_be_reached_is_reported_as_unavailable(ask, feed):
    feed.fails("latest", httpx.ConnectError("connection refused"))

    response = ask(amount=1)

    assert response.status_code == 503
    assert response.json()["error"] == "upstream_unavailable"


def test_a_feed_that_cannot_even_be_connected_to_is_unavailable_not_slow(ask, feed):
    # Depending on the platform a closed port either refuses at once or times out
    # on the connect. Both mean the same thing to the caller, so both say so.
    feed.fails("latest", httpx.ConnectTimeout("no route"))

    response = ask(amount=1)

    assert response.status_code == 503
    assert response.json()["error"] == "upstream_unavailable"


def test_a_feed_that_does_not_answer_in_time_is_reported_as_a_timeout(ask, feed):
    # Distinct from unreachable: one says the host is gone, the other that it is
    # there and slow, and the caller's next move is not the same.
    feed.fails("latest", httpx.ReadTimeout("too slow"))

    response = ask(amount=1)

    assert response.status_code == 504
    assert response.json()["error"] == "upstream_timeout"


# --- the 404 that means two different things ---------------------------------


def test_a_currency_the_publisher_does_not_price_is_the_callers_mistake(ask):
    # The feed answers this with the same 404 as a date it has no data for, so
    # the currency list is what tells them apart.
    response = ask(amount=1, to="XAU")

    assert response.status_code == 400
    assert response.json()["error"] == "unknown_currency"


def test_an_unknown_currency_is_told_which_codes_do_exist(ask):
    message = ask(amount=1, to="XAU").json()["message"]

    assert "XAU" in message
    assert "TRY" in message and "USD" in message


def test_both_unknown_codes_are_named_at_once(ask):
    message = ask(amount=1, source="XAU", to="XAG").json()["message"]

    assert "XAU and XAG" in message


def test_a_known_pair_with_no_history_that_far_back_is_not_the_callers_mistake(ask):
    # EUR/BRL is a real pair, but the series does not reach 1999. Calling that
    # an unknown currency would send the caller looking for a typo.
    response = ask(amount=1, to="BRL", on="1999-01-04")

    assert response.status_code == 404
    assert response.json()["error"] == "rate_unavailable"


def test_the_currency_list_is_only_fetched_when_something_has_gone_wrong(ask, feed):
    feed.publishes("latest", on="2026-09-01", rates={"TRY": "55.9498"})

    ask(amount=1)

    assert feed.calls_to("currencies") == 0


def test_a_missing_currency_list_falls_back_to_the_vaguer_answer(ask, feed):
    # Being unable to tell the two meanings apart is not itself an error; it
    # only means the less specific of them is reported.
    feed.serves_no_currency_list()

    response = ask(amount=1, to="XAU")

    assert response.status_code == 404
    assert response.json()["error"] == "rate_unavailable"


# --- the fault nobody planned for --------------------------------------------


class ExplodingUpstream:
    """Stands in for a bug this service does not know it has."""

    async def quote(self, question):
        raise RuntimeError("an internal detail nobody outside should ever read")


def test_an_unforeseen_fault_still_leaves_in_the_contract(client):
    app.dependency_overrides[get_runtime] = lambda: Runtime(
        upstream=ExplodingUpstream(),
        settings=load_settings({"FX_UPSTREAM_BASE": FAKE_BASE}),
        today=date(2026, 9, 2),
    )

    response = client.get(
        CONVERT_PATH, params={"amount": 250, "from": "EUR", "to": "TRY"}
    )

    assert response.status_code == 500
    assert set(response.json()) == {"error", "message"}
    assert response.json()["error"] == "internal_error"


def test_an_unforeseen_fault_does_not_leak_its_own_message(client):
    # The caller is downstream of a customer conversation. An internal string is
    # not something to hand a model to relay.
    app.dependency_overrides[get_runtime] = lambda: Runtime(
        upstream=ExplodingUpstream(),
        settings=load_settings({"FX_UPSTREAM_BASE": FAKE_BASE}),
        today=date(2026, 9, 2),
    )

    message = client.get(
        CONVERT_PATH, params={"amount": 250, "from": "EUR", "to": "TRY"}
    ).json()["message"]

    assert "nobody outside should ever read" not in message
    assert "RuntimeError" not in message


# --- paths and methods this service does not serve ---------------------------


def test_a_mistyped_path_answers_in_the_same_contract(client):
    response = client.get("/tools/convrt")

    assert response.status_code == 404
    assert set(response.json()) == {"error", "message"}
    assert response.json()["error"] == "unsupported_request"


def test_a_wrong_method_answers_in_the_same_contract(client):
    response = client.post(CONVERT_PATH)

    assert response.status_code == 405
    assert response.json()["error"] == "unsupported_request"


@pytest.mark.parametrize(
    "program",
    [
        lambda feed: feed.answers("latest", body="{}", status=500),
        lambda feed: feed.answers("latest", body="not json"),
        lambda feed: feed.fails("latest", httpx.ConnectError("refused")),
        lambda feed: feed.fails("latest", httpx.ReadTimeout("slow")),
    ],
    ids=["server-error", "not-json", "unreachable", "timeout"],
)
def test_no_failure_ever_produces_a_rate_or_a_result(ask, feed, program):
    program(feed)

    body = ask(amount=250).json()

    assert set(body) == {"error", "message"}
    assert "rate" not in body and "result" not in body
