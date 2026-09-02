# fx-tool

One endpoint an AI agent can call to convert an amount between two currencies at
a published ECB euro reference rate.

It answers with the rate **and with the date that rate actually belongs to**. When
no rate exists for the day asked about, it says so in the answer rather than
quietly moving the date. When no rate can honestly be given, it refuses instead of
returning a number.

## Run

```sh
pip install -r requirements.txt          # or into a venv: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

`run.sh` and `test.sh` use `.venv` when one exists and `python3` otherwise.
Python 3.10 or newer. Built on Windows with 3.13.5, then cloned fresh into
Ubuntu 22.04 (3.10.12) and Ubuntu 24.04 (3.12.3), installed from
`requirements.txt`, and both the suite and live calls run there.

```sh
curl 'http://localhost:8080/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28'
```

```json
{
  "amount": 250,
  "from": "EUR",
  "to": "TRY",
  "rate": 56.1718,
  "result": 14042.95,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "source": "ECB via frankfurter.dev"
}
```

The OpenAPI description a model would read is at `/openapi.json`, and `/docs`
renders it.

## Test

```sh
./test.sh
```

No test opens a socket. Every upstream response is served by an in-process
`httpx.MockTransport`, so this passes with `FX_UPSTREAM_BASE` pointing at a closed
port, at an unresolvable host, or with the machine offline. All three were run.

The single warning in the output is starlette telling its own test client to move
to httpx2. It comes from the test framework, not from this service, and nothing
here depends on the resolution.

## Configuration

| Variable | Default | |
|---|---|---|
| `FX_UPSTREAM_BASE` | `https://api.frankfurter.dev` | Base URL of the rate feed. The service appends `/v1`. This default is the only place the real host appears in the application code; a test walks `app/` and fails if any other module names it. |
| `PORT` | `8080` | Read by `run.sh`. |

An invalid `FX_UPSTREAM_BASE` fails the process at startup rather than at the
first request, so it never reads as an upstream outage.

## The `note` field

`rate_date` and `asked_date` are always both present, and the difference between
them is the answer to "which day is this number from". When they differ, one extra
field appears, because the caller is a model that has to say that difference out
loud to a customer and a sentence relays better than a date subtraction:

```json
"note": "The ECB published no rate for 2026-08-30. This is the rate published on 2026-08-28, 2 days earlier."
```

## What it does in each case

| The caller asks | What happens | Answer |
|---|---|---|
| A weekend, a holiday, or today before the ECB publishes | The last earlier publication, with `rate_date` and `note` naming its day | `200` |
| An `amount` with ten decimal places | Accepted at full precision; the result rounds to the cent, half away from zero. Below half a cent it is `0.00`, with the real rate shown | `200` |
| A nearest earlier rate more than 7 days old | Refused; that gap is a stopped feed, not a holiday | `404 rate_too_stale` |
| A date in the future, or before `1999-01-04` | Refused without asking the feed | `400 date_in_future` / `date_before_series` |
| A pair whose history starts later, EUR/BRL in 1999 say | Refused; the codes are real, the rate is not | `404 rate_unavailable` |
| A code that is not three letters, or is not one the ECB prices (`XAU`) | Refused, and told which codes exist | `400 unknown_currency` |
| `from` and `to` the same | Refused, and told the conversion is 1:1 | `400 same_currency` |
| `amount` missing | Refused | `400 missing_parameter` |
| `amount` zero, negative, `nan`, `inf`, unparseable, or above 10<sup>12</sup> | Refused | `400 invalid_amount` |
| The feed is slow, or unreachable | Refused after the 4s read or 5s connect budget; a refused connection fails at once | `504 upstream_timeout` / `503 upstream_unavailable` |
| The feed answers 500, or with non-JSON, or without the rate asked for, or a rate of zero | Refused | `502 upstream_error` / `upstream_bad_response` |
| A path or method not served | Refused in this contract, not the framework's | `404` / `405 unsupported_request` |

Any repeat of the same question is answered from a per-process cache rather than
re-asked. The key carries the date, so a question about one day never answers a
question about another; asking for today and asking without a date are one entry,
because they are one question. A day that is over is held for a day, anything that
could still be republished for ten minutes, because the ECB publishes around
16:00 CET.

Every number is written into the response as the decimal it is, never through a
binary float, so an amount comes back with the digits it was sent with and a large
result keeps its cents.

## Errors

Every failure, without exception, leaves as:

```json
{ "error": "<machine code>", "message": "<a sentence a person could read>" }
```

| Code | Status | Meaning |
|---|---|---|
| `missing_parameter` | 400 | A required query parameter was not sent. |
| `invalid_amount` | 400 | `amount` was sent but is not a usable amount. |
| `unknown_currency` | 400 | A currency code is malformed, or is not one the ECB prices. |
| `same_currency` | 400 | `from` and `to` are the same currency. |
| `invalid_date` | 400 | `date` is not an ISO calendar date. |
| `date_in_future` | 400 | `date` has not happened yet on the publisher's calendar. |
| `date_before_series` | 400 | `date` precedes 1999-01-04, where the series begins. |
| `rate_unavailable` | 404 | Valid request, but the feed has no rate for that pair on that date. |
| `rate_too_stale` | 404 | The nearest earlier rate is more than 7 days older than the date asked about. |
| `unsupported_request` | 404 / 405 | This service does not serve that path or method. |
| `upstream_error` | 502 | The feed answered with an unexpected status. |
| `upstream_bad_response` | 502 | The feed answered in a shape that cannot be trusted. |
| `upstream_unavailable` | 503 | The feed could not be reached. |
| `upstream_timeout` | 504 | The feed did not answer in time. |
| `internal_error` | 500 | This service failed in a way it did not anticipate. |

`missing_parameter` is separate from `invalid_amount` and `unknown_currency`
because "you left this out" and "what you sent is not usable" call for different
fixes, and the caller is a model that has to pick one.

## Layout

```
app/config.py     what comes from the environment, and the tuning constants
app/errors.py     the error contract and the codes
app/convert.py    the date policy and the arithmetic, as pure functions
app/upstream.py   the feed client and the cache in front of it
app/main.py       the endpoint, and the four exception handlers
tests/            the suite, with a fake feed in tests/support.py
tool.py           the file under review in REVIEW.md, unmodified
```

`NOTES.md` has the decisions behind all of this. `REVIEW.md` is the second half of
the case.
