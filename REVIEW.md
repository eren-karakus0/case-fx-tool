# Review of tool.py

Each finding is the running service's answer beside what the upstream said to the
same question at the same moment. `tool.py` is untouched; the one case needing a
stand-in repointed `tool.UPSTREAM` from a Python session. Ranked by what reaches a
customer: how often it fires, how invisible it is, how far wrong.

## 1. The cache answers a question it was never asked

`key = f"{base}-{target}"` leaves out the date and nothing expires, so the first
rate fetched for a pair answers every later question about it, wearing whatever
date the new caller asked for.

```
?from_=EUR&to=USD&on=2020-03-16 -> rate 1.12, rate_date 2020-03-16
?from_=EUR&to=USD               -> rate 1.12, rate_date 2026-09-02
upstream /v1/latest EUR->USD    -> date 2026-09-01, 1.159
```

**To a customer:** one person asks about March 2020 and everyone after them is
quoted that rate as today's, until the process restarts. A 10,000 EUR invoice
comes back as 11,200 USD instead of 11,590; on EUR/TRY the same poisoning is a
factor of eight. Plausible, well formed, untrue.

## 2. The date on the answer is manufactured from the question

Both return paths report `str(on or date.today())`. The upstream's own `date`
field is never read, though the docstring promises `"""Return (rate, the date the
rate belongs to)"""`.

```
on=2026-08-30 -> 0.86, dated 2026-08-30 | upstream: 0.8572 on 2026-08-28
on=2026-09-10 -> 0.94, dated 2026-09-10 | upstream: date 2026-09-01
on=2030-01-01 -> 7.47, dated 2030-01-01 | upstream: 404 not found
```

**To a customer:** Friday's rate presented as Sunday's, and that is the field an
invoice is checked against later. Then a rate for a day that has not happened,
because the upstream answers near-future dates with 200 and nothing fails loudly.
Then a rate for 2030 the upstream refused to give: invented out of nothing.

The comment above the fallback explains it as "the ECB publishes nothing on
weekends". The ECB does not, but the *upstream* does, answering Sunday with 200
and Friday's date, so that branch never runs for the reason it gives. It fires on
a 404 body with no `rates` key, an unknown currency or a date outside the series,
and re-asking `/latest` is wrong for both. That comment is why findings 1 to 3
look reasonable: the author's model of the upstream was wrong and everything
downstream inherited it.

## 3. The documented call is not the call the code implements

The handler declares `from_` and `on`, so the brief's `from` and `date` are never
read and take their defaults, `EUR` and "latest". `asked_date` is absent entirely.

```
?amount=250&from=USD&to=TRY&date=2026-08-28 -> from EUR, 55.95, result 13987.5
upstream USD->TRY on 2026-08-28: 48.245     -> the answer is 12061.25
```

**To a customer:** 250 dollars quoted as 250 **euros** at another day's rate,
sixteen percent over, from a call copied out of the documentation.

## Three smaller ones

**A failure answers zero with HTTP 200.** `except Exception` returns
`rate: 0.0, result: 0.0` in a body shaped like a real answer, so the customer is
told their 250 EUR is worth nothing and the caller cannot tell. Seen with an
unknown currency (`conversion failed: 'rates'`) and a stand-in serving HTML
(`Expecting value: line 1 column 1`). Below the three above only because a zero is
absurd enough that something downstream may balk at it.

**`round(rate, 2)` destroys small rates.** TRY/USD at 0.02071 becomes 0.02, so a
million lira is quoted as 20,000.00 USD instead of 20,710.00. Rounding the
*result* to cents is right; rounding the rate is not.

**`amount` is unvalidated.** `nan` answers `{"amount":null,"result":null}` with a
200, `-500` answers `-5555.0`, and a missing amount answers `422 {"detail":[...]}`,
a second error shape the caller has to know.

## The one I would fix before shipping tonight

**The cache key.** One line, and it stops the largest, most frequent and least
visible wrong number here. It is also the only defect that worsens with uptime and
the only one with no tell: findings 2 and 3 leave a wrong date or currency in the
response for a careful reader, while a poisoned cache returns a well-formed answer
that is simply untrue. Given a second line, I would delete the `except Exception`
that returns zeros.

## Things that look suspicious but are fine

- **No timeout on the client.** httpx defaults to five seconds on every phase.
  `print(httpx.AsyncClient().timeout)` -> `Timeout(timeout=5.0)`.
- **`except Exception` swallowing a cancelled request.** It does not.
  `asyncio.CancelledError` has been a `BaseException` since 3.8.
- **The unbounded cache.** The write happens only after a lookup succeeds, so
  nothing a caller invents lands in it. Its problem is its key, not its size.
- **Rounding the result to two decimals.** Correct. Money is quoted in cents.
