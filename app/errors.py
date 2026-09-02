"""One error contract for every failure that leaves this service.

    {"error": "<machine code>", "message": "<a sentence a person could read>"}

The caller is a language model relaying an answer to a paying customer, so each
message is written to do two jobs: be repeatable to the customer as it stands,
and tell the model what to change before trying again.

The codes are grouped by who has to act on them: the caller, nobody (the rate
honestly does not exist), or the operator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorCode:
    """A machine code and the HTTP status it is reported with."""

    code: str
    status: int


# --- The caller sent something this service cannot honour. -------------------
MISSING_PARAMETER = ErrorCode("missing_parameter", 400)
INVALID_AMOUNT = ErrorCode("invalid_amount", 400)
UNKNOWN_CURRENCY = ErrorCode("unknown_currency", 400)
SAME_CURRENCY = ErrorCode("same_currency", 400)
INVALID_DATE = ErrorCode("invalid_date", 400)
DATE_IN_FUTURE = ErrorCode("date_in_future", 400)
DATE_BEFORE_SERIES = ErrorCode("date_before_series", 400)

#: A path or method this service does not serve. Kept here so that a mistyped
#: URL answers in the same contract as everything else, rather than in whatever
#: the web framework would produce on its own.
UNSUPPORTED_REQUEST = ErrorCode("unsupported_request", 404)

# --- The request is well formed, but no rate can honestly be returned. -------
RATE_UNAVAILABLE = ErrorCode("rate_unavailable", 404)
RATE_TOO_STALE = ErrorCode("rate_too_stale", 404)

# --- The upstream let us down. Retrying later may work. ----------------------
UPSTREAM_TIMEOUT = ErrorCode("upstream_timeout", 504)
UPSTREAM_UNAVAILABLE = ErrorCode("upstream_unavailable", 503)
UPSTREAM_ERROR = ErrorCode("upstream_error", 502)
UPSTREAM_BAD_RESPONSE = ErrorCode("upstream_bad_response", 502)

# --- Something this service failed to anticipate. ----------------------------
INTERNAL_ERROR = ErrorCode("internal_error", 500)


class FxError(Exception):
    """A failure that can be raised anywhere and rendered in exactly one place."""

    def __init__(self, error: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message

    @property
    def status(self) -> int:
        return self.error.status

    @property
    def body(self) -> dict[str, str]:
        return {"error": self.error.code, "message": self.message}


# The query parameter that failed decides which code the caller gets back. The
# route accepts exactly these four; anything else means this table has drifted
# away from the signature, which is an internal fault rather than a bad request.
_FIELD_CODES: dict[str, ErrorCode] = {
    "amount": INVALID_AMOUNT,
    "from": UNKNOWN_CURRENCY,
    "to": UNKNOWN_CURRENCY,
    "date": INVALID_DATE,
}

# Keyed by field, then by the pydantic error type. "*" is the message used when
# a field fails in a way not listed here. The types were read off the running
# stack rather than from memory; see tests/test_errors.py.
_MESSAGES: dict[str, dict[str, str]] = {
    "amount": {
        "missing": (
            "The 'amount' query parameter is required. It says how much to "
            "convert, for example amount=250."
        ),
        "decimal_parsing": (
            "'amount' must be a decimal number, for example 250 or 250.75."
        ),
        "finite_number": (
            "'amount' must be a finite decimal number. NaN and Infinity are not "
            "amounts."
        ),
        "greater_than": (
            "'amount' must be greater than zero; there is nothing to convert "
            "otherwise."
        ),
        "less_than_equal": (
            "'amount' is larger than this service will convert. Send at most "
            "1000000000000."
        ),
        "*": "'amount' must be a positive decimal number, for example 250.",
    },
    "from": {
        "missing": (
            "The 'from' query parameter is required. It is the currency to "
            "convert out of, for example from=EUR."
        ),
        "*": (
            "'from' must be a three-letter ISO 4217 currency code, for example "
            "EUR."
        ),
    },
    "to": {
        "missing": (
            "The 'to' query parameter is required. It is the currency to "
            "convert into, for example to=TRY."
        ),
        "*": (
            "'to' must be a three-letter ISO 4217 currency code, for example TRY."
        ),
    },
    "date": {
        "date_from_datetime_inexact": (
            "'date' must be a calendar date with no time of day, for example "
            "2026-08-28. The ECB publishes one rate per day, not per moment."
        ),
        "*": (
            "'date' must be an ISO calendar date, for example 2026-08-28. Leave "
            "it out to use the most recently published rate."
        ),
    },
}


def translate_validation_error(errors: Sequence[Mapping[str, Any]]) -> FxError:
    """Turn a framework validation failure into this service's error contract.

    The web framework would otherwise answer with its own 422 body, which is a
    different shape from every other failure this service can produce. The
    caller should not have to parse two contracts.

    Args:
        errors: the entries the framework reports, each with a ``loc`` and a
            ``type``. Only the first is reported; a model fixes one thing at a
            time, and a list of complaints is harder to act on than a sentence.

    Returns:
        The error to render. Falls back to INTERNAL_ERROR when the failing field
        is not one this route declares, because that means the route and this
        table disagree.
    """
    first = errors[0] if errors else {}
    location = tuple(first.get("loc") or ())
    field = str(location[-1]) if location else ""
    kind = str(first.get("type") or "")

    if field not in _FIELD_CODES:
        return FxError(
            INTERNAL_ERROR,
            "The request could not be validated. Please report this.",
        )

    messages = _MESSAGES[field]
    message = messages.get(kind) or messages["*"]
    code = MISSING_PARAMETER if kind == "missing" else _FIELD_CODES[field]
    return FxError(code, message)
