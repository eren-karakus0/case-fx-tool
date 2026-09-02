"""The HTTP surface: one endpoint, and one place where every failure is rendered.

The caller is a language model, so the schema this module publishes is part of
the interface. The parameter names, the constraints and the descriptions are what
tell a model when to reach for this tool and what to send it.

Validation is split on purpose. Shape and range are declared on the parameters,
so they appear in the published schema and are enforced before the handler runs.
Meaning lives in app.convert, because "that date is in the future" is not
something a schema can say.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import MAX_AMOUNT, SOURCE_LABEL, Settings, load_settings
from app.convert import (
    convert_amount,
    ecb_today,
    ensure_distinct,
    ensure_publishable,
    fallback_note,
    normalise_currency,
    resolve_asked_date,
)
from app.errors import (
    INTERNAL_ERROR,
    UNSUPPORTED_REQUEST,
    FxError,
    translate_validation_error,
)
from app.upstream import Quote, RateCache, RateQuestion, Upstream

CONVERT_PATH = "/tools/convert"

#: Three letters, either case. The semantic check, "is this a currency the ECB
#: prices", needs the feed and happens later.
CURRENCY_PATTERN = r"^[A-Za-z]{3}$"


@dataclass(frozen=True)
class Runtime:
    """What the handler needs that does not come from the URL.

    Injected as one dependency so a test replaces the feed, the policy and the
    clock in a single override, and so the handler signature stays a description
    of the HTTP contract.
    """

    upstream: Upstream
    settings: Settings
    today: date


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the one HTTP client and the one cache this process uses.

    The client is shared: a new connection per request would add a handshake to
    every call a model waits on. Timeouts are set explicitly rather than left to
    the library's default, because "the upstream is slow" is a case this service
    is expected to have an answer for.
    """
    settings = load_settings()
    async with httpx.AsyncClient(timeout=client_timeout(settings)) as client:
        app.state.settings = settings
        app.state.upstream = Upstream(
            settings, client, RateCache(settings.cache_max_entries)
        )
        yield


def client_timeout(settings: Settings) -> httpx.Timeout:
    """The budget one upstream call is given, phase by phase.

    Named rather than inlined so that the wiring can be tested: a budget that
    silently stopped reaching the client would show up only as an outage under
    load, which is the worst place to find it.
    """
    return httpx.Timeout(
        connect=settings.connect_timeout,
        read=settings.read_timeout,
        write=settings.read_timeout,
        pool=settings.connect_timeout,
    )


app = FastAPI(
    title="fx-tool",
    version="1.0.0",
    summary="Convert an amount between two currencies at a published ECB reference rate.",
    lifespan=lifespan,
)


def get_runtime(request: Request) -> Runtime:
    return Runtime(
        upstream=request.app.state.upstream,
        settings=request.app.state.settings,
        today=ecb_today(),
    )


@app.get(
    CONVERT_PATH,
    summary="Convert an amount between two currencies at a published ECB rate",
    description=(
        "Answers with the euro reference rate the ECB published, and with the "
        "date that rate belongs to.\n\n"
        "The ECB publishes on working days only. When the day asked about has no "
        "rate, the answer carries the most recent earlier publication instead: "
        "'rate_date' then differs from 'asked_date' and a 'note' field says so in "
        "words. A rate is never presented as belonging to a day it does not "
        "belong to, and never invented. When no rate can honestly be given the "
        "call fails with a machine code and a sentence rather than a number."
    ),
    response_model=None,
)
async def convert(
    amount: Annotated[
        Decimal,
        Query(
            gt=0,
            le=MAX_AMOUNT,
            description="How much to convert. Must be greater than zero.",
            examples=[250],
        ),
    ],
    from_currency: Annotated[
        str,
        Query(
            alias="from",
            pattern=CURRENCY_PATTERN,
            description="Currency to convert out of, as a three-letter ISO 4217 code.",
            examples=["EUR"],
        ),
    ],
    to: Annotated[
        str,
        Query(
            pattern=CURRENCY_PATTERN,
            description="Currency to convert into, as a three-letter ISO 4217 code.",
            examples=["TRY"],
        ),
    ],
    runtime: Annotated[Runtime, Depends(get_runtime)],
    on: Annotated[
        date | None,
        Query(
            alias="date",
            description=(
                "The day the rate should be from, as YYYY-MM-DD. Leave it out for "
                "the most recent publication. It may not be in the future."
            ),
            examples=["2026-08-28"],
        ),
    ] = None,
) -> JSONResponse:
    base = normalise_currency(from_currency)
    target = normalise_currency(to)
    ensure_distinct(base, target)

    asked_on = resolve_asked_date(on, runtime.today)
    question = RateQuestion(
        base=base, target=target, on=on, settled=asked_on < runtime.today
    )

    quote = await runtime.upstream.quote(question)
    ensure_publishable(asked_on, quote.published_on, runtime.settings.max_fallback_days)

    return JSONResponse(
        _success_body(amount=amount, question=question, quote=quote, asked_on=asked_on)
    )


def _success_body(
    *, amount: Decimal, question: RateQuestion, quote: Quote, asked_on: date
) -> dict[str, object]:
    """Render one answer.

    Keyword-only: four values of two types in a row are easy to transpose, and
    transposing the two dates here is exactly the defect this service exists to
    avoid.
    """
    body: dict[str, object] = {
        "amount": _as_json_number(amount),
        "from": question.base,
        "to": question.target,
        "rate": _as_json_number(quote.rate),
        "result": _as_json_number(convert_amount(amount, quote.rate)),
        "rate_date": quote.published_on.isoformat(),
        "asked_date": asked_on.isoformat(),
        "source": SOURCE_LABEL,
    }

    note = fallback_note(asked_on, quote.published_on)
    if note is not None:
        body["note"] = note

    return body


@app.exception_handler(FxError)
async def handle_fx_error(request: Request, exc: FxError) -> JSONResponse:
    return JSONResponse(exc.body, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Answer the framework's own validation failures in this service's shape.

    Without this the caller would have to parse two different error contracts to
    find out what went wrong.
    """
    error = translate_validation_error(exc.errors())
    return JSONResponse(error.body, status_code=error.status)


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Keep the contract total: a mistyped path or method leaves in it too."""
    return JSONResponse(
        {
            "error": UNSUPPORTED_REQUEST.code,
            "message": (
                f"This service answers GET {CONVERT_PATH}. "
                f"{request.method} {request.url.path} is not something it serves."
            ),
        },
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last resort, so that a fault never leaves as an unparseable 500.

    Nothing is echoed back from the exception: the caller is downstream of a
    customer conversation, and an internal message is not something to relay.
    """
    return JSONResponse(
        {
            "error": INTERNAL_ERROR.code,
            "message": (
                "This service failed to answer. No rate is returned rather than "
                "an uncertain one; trying again may work."
            ),
        },
        status_code=INTERNAL_ERROR.status,
    )


def _as_json_number(value: Decimal) -> int | float:
    """Render a Decimal as a JSON number.

    An integral value is written without a fractional part, which is how the
    documented response shows an amount of 250. What reaches here is a rate with
    at most six decimal places and a result already quantised to the cent, both
    of which round-trip through a binary float to the same text.
    """
    return int(value) if value == value.to_integral_value() else float(value)
