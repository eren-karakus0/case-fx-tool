# Review of tool.py

Every finding below is the running service's answer next to what the upstream said
to the same question at the same moment. `tool.py` itself is untouched; the one
case needing a stand-in upstream was done by repointing `tool.UPSTREAM` from a
Python session. Ranked by what reaches a customer: how often it fires, how
invisible it is, and how far wrong it puts the number.

## 1. The cache answers a question it was never asked

`key = f"{base}-{target}"` leaves the date out and nothing expires, so the first
rate ever fetched for a pair answers every later question about it, wearing
whatever date the new caller asked about.

**To a customer:** one person asks what something was worth in March 2020, and
everyone after them is quoted that 2020 rate as today's. Plausible, well formed,
and wrong until the process restarts. A 10,000 EUR invoice comes back as 11,200 USD
instead of 11,590; on EUR/TRY the same poisoning is a factor of eight.

```
?amount=1&from_=EUR&to=USD&on=2020-03-16 -> rate 1.12, rate_date 2020-03-16
?amount=1&from_=EUR&to=USD               -> rate 1.12, rate_date 2026-09-02
upstream /v1/latest EUR->USD             -> date 2026-09-01, 1.159
```

## 2. The date on the answer is manufactured from the question

Both return paths report `str(on or date.today())`. The upstream's own `date`
field, the only thing that says which publication a rate came from, is never read,
though the docstring promises `"""Return (rate, the date the rate belongs to)"""`.

**To a customer:** they are told a number belongs to a day it does not, and that is
the field an invoice or a tax filing is checked against later. Worst last:

```
on=2026-08-30 -> rate 0.86, rate_date 2026-08-30 | upstream: 0.8572 on 2026-08-28
                 Friday's rate, presented as Sunday's, and rounded on the way.
on=2026-09-10 -> rate 0.94, rate_date 2026-09-10 | upstream: date 2026-09-01
                 A day that has not happened. The upstream answers near-future
                 dates with 200 and its latest rate, so nothing fails loudly.
on=2030-01-01 -> rate 7.47, rate_date 2030-01-01 | upstream: 404 not found
                 The upstream refused; the fallback stamped 2030 on today's rate.
                 A rate invented out of nothing.
```

Every answer given without `on` is dated 2026-09-02 today, a day the ECB has
published nothing for.

## 3. The documented call is not the call the code implements

The brief's URL is `?amount=250&from=EUR&to=TRY&date=2026-08-28`. The handler
declares `from_` and `on`, so `from` and `date` are never read and take their
defaults, `EUR` and "latest". `asked_date` is absent from the response entirely.

**To a customer:** they ask for 250 US dollars at last Friday's rate and are quoted
250 **euros** at a different day's rate, with a 200 and no hint anything was
dropped.

```
?amount=250&from=USD&to=TRY&date=2026-08-28 -> from EUR, rate 55.95, result 13987.5
upstream USD->TRY on 2026-08-28: 48.245     -> the answer is 12061.25
```

Sixteen percent over, from a call copied out of the documentation. The published
OpenAPI advertises `from_` and `on`, so a model reading the schema and a person
reading the README build different requests.

## 4. The comment justifying the fallback is wrong about the upstream

> `# The ECB publishes nothing on weekends and holidays, so fall back to`
> `# the most recent rates instead of failing the request.`

The ECB publishes nothing on weekends, but the upstream does: asked about Sunday
2026-08-30 it answers 200 with `{"date":"2026-08-28",...}`, so the target is present
and this branch never runs for the reason it gives. What triggers it is a 404 body
with no `rates` key, meaning an unknown currency or a date outside the series, and
re-asking `/latest` is wrong for both: the first falls into the zero-rate handler
below, the second is the 2030 invention above.

Less a fourth bug than the reason the first three look reasonable. A comment that
asserts something false makes every other comment in the file something you have to
check yourself.

## Three smaller ones

**A failure answers zero with HTTP 200.** `except Exception` returns
`rate: 0.0, result: 0.0` in a body shaped like a real answer, so the customer is
told their 250 EUR is worth nothing and the caller cannot tell. Seen with an
unknown currency (`conversion failed: 'rates'`) and with a stand-in serving HTML
(`Expecting value: line 1 column 1`). Below the three above only because a zero is
absurd enough that something downstream may balk.

**`round(rate, 2)` destroys small rates.** TRY/USD at 0.02071 becomes 0.02, so a
million lira is quoted as 20,000.00 USD instead of 20,710.00. Rounding the *result*
to cents is right; rounding the rate is not.

**`amount` is unvalidated.** `nan` answers `{"amount":null,"result":null}` with a
200, `-500` answers `-5555.0`, `0` answers `0.0`, and a missing amount answers
`422 {"detail":[...]}`, a second error shape the caller has to know.

## The one I would fix before shipping tonight

**The cache key.** One line, and it stops the largest, most frequent and least
visible wrong number in the file. It is also the only defect that worsens with
uptime and the only one with no tell: findings 2 and 3 leave a wrong date or
currency in the response for a careful reader to catch, while a poisoned cache
returns a well-formed answer that is simply untrue. Given a second line, I would
delete the `except Exception` that returns zeros.

## Things that look suspicious but are fine

- **No timeout on the client.** Nothing hangs: httpx defaults to five seconds on
  every phase. `print(httpx.AsyncClient().timeout)` -> `Timeout(timeout=5.0)`.
- **`except Exception` swallowing a cancelled request.** It does not.
  `asyncio.CancelledError` has been a `BaseException` since 3.8.
- **The unbounded cache.** The write happens only after a lookup succeeds, so
  nothing a caller invents lands in it; the key space is under a thousand pairs.
  The cache's problem is its key, not its size.
- **Rounding the result to two decimals.** Correct. Money is quoted in cents.

Each defect above has a test here that fails if it returns: `test_caching.py` for
the key, `test_dates.py` for the publisher's date and the future, `test_failures.py`
for no failure ever emitting a rate, `test_contract.py` for the documented call.
