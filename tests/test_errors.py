"""The framework's own validation failures must leave in this service's shape.

The error entries below are the ones the running stack actually produces; they
were read off fastapi 0.135.1 / pydantic 2.13.5 rather than written from memory.
The end-to-end equivalents live in tests/test_validation.py.
"""

from __future__ import annotations

import pytest

from app.errors import (
    INTERNAL_ERROR,
    INVALID_AMOUNT,
    INVALID_DATE,
    MISSING_PARAMETER,
    UNKNOWN_CURRENCY,
    translate_validation_error,
)


def entry(field: str, kind: str) -> dict[str, object]:
    return {"type": kind, "loc": ("query", field), "msg": "irrelevant"}


@pytest.mark.parametrize("field", ["amount", "from", "to"])
def test_a_parameter_that_was_not_sent_is_reported_as_missing(field):
    error = translate_validation_error([entry(field, "missing")])
    assert error.error is MISSING_PARAMETER
    assert error.status == 400
    # The message has to name the parameter, or the model cannot act on it.
    assert f"'{field}'" in error.message


@pytest.mark.parametrize(
    "kind", ["decimal_parsing", "finite_number", "greater_than", "less_than_equal"]
)
def test_every_way_an_amount_can_fail_maps_to_invalid_amount(kind):
    error = translate_validation_error([entry("amount", kind)])
    assert error.error is INVALID_AMOUNT
    assert error.body["error"] == "invalid_amount"


def test_each_amount_failure_gets_its_own_sentence():
    # A single generic sentence would tell the model nothing it did not know.
    messages = {
        translate_validation_error([entry("amount", kind)]).message
        for kind in ("decimal_parsing", "finite_number", "greater_than", "less_than_equal")
    }
    assert len(messages) == 4


@pytest.mark.parametrize("field", ["from", "to"])
def test_a_malformed_currency_code_maps_to_unknown_currency(field):
    error = translate_validation_error([entry(field, "string_pattern_mismatch")])
    assert error.error is UNKNOWN_CURRENCY
    assert "ISO 4217" in error.message


@pytest.mark.parametrize(
    "kind", ["date_from_datetime_parsing", "date_from_datetime_inexact"]
)
def test_a_malformed_date_maps_to_invalid_date(kind):
    error = translate_validation_error([entry("date", kind)])
    assert error.error is INVALID_DATE


def test_a_date_carrying_a_time_of_day_is_told_why_it_was_refused():
    error = translate_validation_error([entry("date", "date_from_datetime_inexact")])
    assert "no time of day" in error.message


def test_an_unknown_field_is_an_internal_fault_not_a_bad_request():
    # Reaching this means the route signature and the error table disagree,
    # which is not something the caller can fix by changing the request.
    error = translate_validation_error([entry("surprise", "missing")])
    assert error.error is INTERNAL_ERROR
    assert error.status == 500


def test_an_empty_error_list_still_produces_the_contract():
    error = translate_validation_error([])
    assert set(error.body) == {"error", "message"}


def test_the_body_is_only_ever_a_code_and_a_message():
    error = translate_validation_error([entry("amount", "missing")])
    assert error.body == {"error": "missing_parameter", "message": error.message}
