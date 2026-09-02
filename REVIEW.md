# Review of tool.py

I ran it. Every finding below was produced by calling the running service and
comparing its answer with what the upstream said to the same question at the same
moment; the outputs are real, not reconstructed. Where a case needed a stand-in
upstream I repointed `tool.UPSTREAM` at one from a Python session, which is the
same thing the brief says the reviewer does, and left the file untouched.

Ranked by what actually reaches a customer: how often it fires, how invisible it
is when it does, and how far wrong it puts the number.

---

## 1. The cache answers a question it was never asked

```python
key = f"{base}-{target}"
if key in _cache:
    return _cache[key], str(on or date.today())
```

The date is not in the key and nothing expires. The first rate ever fetched for a
pair answers every later question about that pair, wearing whatever date the new
caller asked about.

**What it does to a customer.** One person asks what something was worth in March
2020. Everyone after them is quoted that 2020 rate as today's. The response looks
entirely normal, the number is plausible enough to act on, and it stays wrong until
the process restarts.

**Verified.**

```
GET /tools/convert?amount=1&from_=EUR&to=USD&on=2020-03-16
  -> {"rate":1.12,"rate_date":"2020-03-16"}
GET /tools/convert?amount=1&from_=EUR&to=USD
  -> {"rate":1.12,"rate_date":"2026-09-02"}      <- 2020's rate, today's date
api.frankfurter.dev/v1/latest?base=EUR&symbols=USD
  -> {"date":"2026-09-01","rates":{"USD":1.159}}  <- what today actually is
```

A 10,000 EUR invoice comes back as 11,200 USD instead of 11,590. On EUR/TRY the
same poisoning is a factor of eight rather than three percent: 7.1568 on
2020-03-16 against 55.9498 today.

## 2. The date on the answer is manufactured from the question

Both return paths report `str(on or date.today())`. The upstream's own `date`
field, the one thing that says which publication a rate came from, is never read.
The docstring above it says `"""Return (rate, the date the rate belongs to)"""`,
which is not what the function returns.

**What it does to a customer.** They are told a number belongs to a day it does not
belong to, and that is the field an invoice or a tax filing gets checked against
later. Three shapes of it, worst last:

```
GET ...&to=GBP&on=2026-08-30        -> {"rate":0.86,"rate_date":"2026-08-30"}
  upstream for 2026-08-30           -> {"date":"2026-08-28","rates":{"GBP":0.8572}}
      Friday's rate, presented as Sunday's.

GET ...&to=CHF&on=2026-09-10        -> {"rate":0.94,"rate_date":"2026-09-10"}
  upstream for 2026-09-10           -> {"date":"2026-09-01",...}
      A rate for a day that has not happened. The upstream answers near-future
      dates with 200 and its latest rate, so nothing here fails loudly.

GET ...&to=DKK&on=2030-01-01        -> {"rate":7.47,"rate_date":"2030-01-01"}
  upstream for 2030-01-01           -> 404 {"message":"not found"}
      The upstream refused. The fallback fetched today's rate and stamped 2030 on
      it. That is a rate invented out of nothing.
```

Every answer it gives without an `on` parameter is dated `2026-09-02` today, a day
the ECB has published nothing for; its most recent publication is `2026-09-01`.

## 3. The documented call is not the call the code implements

The brief's URL is `?amount=250&from=EUR&to=TRY&date=2026-08-28`. The handler
declares `from_` and `on`, so `from` and `date` are never read and silently take
their defaults, `EUR` and "latest". `asked_date` does not exist in the response at
all.

**What it does to a customer.** They ask for 250 US dollars at last Friday's rate
and are quoted 250 **euros** at a rate from a different day, with a 200 and no hint
that anything was dropped.

**Verified.** The documented call, unaltered:

```
GET /tools/convert?amount=250&from=USD&to=TRY&date=2026-08-28
  -> {"amount":250.0,"from":"EUR","to":"TRY","rate":55.95,"result":13987.5,
      "rate_date":"2026-09-02"}
upstream, USD->TRY on 2026-08-28 -> 48.245, so the answer is 12061.25
```

13,987.50 against 12,061.25 is sixteen percent over, from a call copied out of the
documentation. The published OpenAPI is consistent with the code and not with the
brief: it advertises `from_` and `on`, so a model reading the schema and a person
reading the README build two different requests.

## 4. The comment that justifies the fallback is wrong about the upstream

```python
if target not in payload.get("rates", {}):
    # The ECB publishes nothing on weekends and holidays, so fall back to
    # the most recent rates instead of failing the request.
```

The ECB publishes nothing on weekends, but the *upstream* does. Asked about Sunday
2026-08-30 it answers `200` with `{"date":"2026-08-28","rates":{"GBP":0.8572}}`, so
the target is present and this branch never runs for the reason it gives.

What actually triggers it is a `404` body, `{"message":"not found"}`, which has no
`rates` key. That happens for a currency the ECB does not price and for a date
outside the series, and re-asking `/latest` is exactly the wrong move in both. For
an unknown currency it fetches a second 404 and falls into the zero-rate handler
below; for a date outside the series it fetches today's rate and stamps the asked
date on it, which is the 2030 invention in finding 2.

This is not a fourth bug so much as the reason the first three look reasonable. The
author's model of the upstream was wrong, and every decision downstream inherited
it. A comment that asserts something false is worse than no comment, because it
makes every other comment in the file something you have to check yourself.

## Three smaller ones

**A failure is reported as a rate of zero, with HTTP 200.** The bare
`except Exception` returns `rate: 0.0, result: 0.0` in a body shaped exactly like a
real answer, so the caller has no way to tell them apart and the customer is told
their 250 EUR is worth nothing. Verified with an unknown currency, which logs
`conversion failed: 'rates'`, and with a stand-in upstream serving HTML, which logs
`conversion failed: Expecting value: line 1 column 1`. It ranks below the three
above only because a zero is absurd enough that something downstream may balk,
while a stale but plausible rate is not.

**`round(rate, 2)` destroys small rates.** The published rate is rounded before it
is used, so TRY/USD at `0.02071` becomes `0.02`. A million lira is quoted as
20,000.00 USD instead of 20,710.00, a 3.4% error, and the smaller the rate the worse
it gets. Rounding the *result* to cents is right; rounding the rate is not.

**`amount` is not validated.** `amount=nan` answers `{"amount":null,"result":null}`
with a 200 and a real-looking rate, which teaches the model nothing about what went
wrong. `amount=-500` answers `-5555.0`. `amount=0` answers `0.0`. A missing `amount`
answers `422 {"detail":[...]}`, a second error shape the caller has to know about.

## The one I would fix before shipping tonight

**Finding 1, the cache key.** Putting the date and a time to live into it is a
one-line change and it stops the largest, most frequent and least visible wrong
number in the file. It is also the only defect here that gets *worse* the longer the
process stays up, and the only one with no tell: findings 2 and 3 leave a wrong date
or a wrong currency sitting in the response where a careful reader can catch them,
while a poisoned cache returns a well-formed answer that is simply untrue.

If a second line were allowed, I would delete the `except Exception` that returns
zeros. Failing loudly is not a feature worth waiting a sprint for.

## Things that look suspicious but are fine

**`httpx.AsyncClient()` with no timeout.** It looks like a request could hang for
ever. It cannot: httpx defaults to five seconds on connect, read, write and pool.
Confirmed with `python -c "import httpx; print(httpx.AsyncClient().timeout)"` ->
`Timeout(timeout=5.0)`. A shorter, split budget would be better, but nothing hangs.

**`except Exception` swallowing a cancelled request.** It does not.
`asyncio.CancelledError` has inherited from `BaseException` rather than `Exception`
since Python 3.8, so a client disconnect still propagates. Confirmed with
`issubclass(asyncio.CancelledError, Exception)` -> `False`.

**The unbounded cache.** A module-level dict that never evicts is usually a leak.
Here the key is only `base-target`, and the write happens after the lookup has
succeeded, so nothing a caller invents ever lands in it. The whole key space is the
pairs the ECB publishes, under a thousand entries. The cache's problem is its key,
not its size.

**The client is never closed.** There is no `aclose()` and no lifespan hook, but one
long-lived client in one long-lived process is the correct pattern; reusing the
connection pool is the entire point of sharing it.

**Rounding the result to two decimals.** That part is right. Money is quoted in
cents, and only the rounding of the rate is a defect.

**`print()` instead of a logger.** A real complaint, but to a linter rather than to
a customer, and right now it is the only reason any of these failures are visible at
all.

---

Each of the first three, and each of the three smaller ones, has a test in this
repository that fails if the same mistake is made again: `test_caching.py` pins that
a different date is a different question, `test_dates.py` pins that a fallback
carries the publisher's date and that a future date is refused before the upstream
is asked, `test_failures.py` pins that no failure path ever emits a `rate` or a
`result`, and `test_contract.py` pins the documented call field by field.
