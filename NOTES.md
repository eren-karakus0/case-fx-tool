# Notes

## Decisions

**The date on the answer is the feed's, never the question's.** The feed stamps
every response with the publication its rate came from: asked about Saturday
2026-08-29 it answers 200 with `"date": "2026-08-28"`. A service that never reads
that field presents Friday's number as Saturday's and nothing looks wrong.

**When the day asked about has no rate, the last earlier publication is returned
and the answer says so.** `rate_date` carries the real date, `asked_date` the
question, and a `note` field states the difference in a sentence the model can
repeat to a customer. *Rejected:* refusing every non-publication day, which is
simpler but useless for the most ordinary question, "what was this worth last
Saturday". *Rejected:* returning the fallback under the date that was asked for,
which is the defect this task is built around.

**The fallback reaches back at most 7 days.** Measured: the whole EUR/USD series,
7083 published days since 1999-01-04, has gaps of 1 day x 5608, 2 x 23, 3 x 1392,
4 x 23, 5 x 36. The widest is five, at Easter and Christmas, so an honest answer
never reaches back more than four. A wider gap is a feed that has stopped, not a
holiday, and gets `rate_too_stale`.

**A future date is refused before the feed is asked**, and not to save a request.
The feed answers dates a fortnight ahead with 200 and its latest rate:
`/v1/2026-09-15` returned the rate published on 2026-09-01, and only 2026-09-16
returned 404. Passing that through would quote a day that has not happened.

**v1, not v2.** Asked the same question at the same moment, v1 answered 55.9498
dated 2026-09-01 and v2 55.996 dated 2026-09-02, because v2 blends 84 central banks
rather than the ECB alone. Only v1 makes `"source": "ECB via frankfurter.dev"` true.

Smaller, with the reasoning in the code beside them: "today" is today in Frankfurt,
because a server in Istanbul is already tomorrow for an hour each night; the feed's
404 means both "no such currency" and "no rate that far back", so the currency list
is fetched on the error path to tell them apart; `from == to` is refused rather than
answered 1.0, because that number has no publication behind it; money is `Decimal`
throughout and only the result is rounded, half away from zero, since `round()`
rounds half to even and turns 11780.845 into 11780.84.

**Not built, on purpose:** auth, a database, a UI, a Dockerfile, CI, deployment,
and any second endpoint, including the `/health` that `tool.py` has.

**What running it changed, twice.** A closed port does not always refuse a
connection; on some platforms it times out on the connect. That was reported as
"did not answer in time" though nothing had been shown to be slow, and now reports
the source as unreachable. The second was a real defect: the connect budget started
at two seconds, but httpx charges DNS, TCP and TLS all to that phase, and five cold
attempts at the real feed took 1.32s, 0.76s, 2.66s, 0.42s and 3.03s to get that far.
Two of five over budget, so the first call of a fresh process answered 503 against a
perfectly healthy feed. I only saw it by running the service on Ubuntu rather than
on the machine it was written on. Five seconds now, which costs nothing afterwards
because the connection is pooled.

## With another day

- **Single-flight.** Ten identical questions arriving together are ten upstream
  requests; the cache only helps once the first has returned.
- **Per-currency minor units.** JPY and KRW have none, so `¥1234.00` is quietly
  wrong in a way `11780.85` is not.
- **Conditional requests** using the `ETag` the feed already sends.
- **An evaluation case, not a unit test,** for the fallback wording: what matters is
  whether a model relays "this is Friday's rate" to the customer, and nothing here
  checks that.
- **Structured logging** of the gap between the date asked and the date answered.
  That gap growing is the first sign the feed is drifting, and nobody would see it.

## AI tools

Claude Code throughout, the way I normally work: it drafted most of each module and
most of the test bodies, and I reviewed and rewrote as it went. Two things I did not
delegate.

The upstream's actual behaviour came first. Before any code, several dozen `curl`
calls established what the feed really does on weekends, on future dates, before the
series starts, and for a pair whose history starts late. Every decision above rests
on an observed response, and the two findings that shaped the service most, the
silent weekend fallback and the 200 for near-future dates, are things no amount of
reading would have produced.

Then verification. `./test.sh` passing is not evidence the service works, so I ran
it: against the live feed, a stand-in answering HTML, one that never answers, and a
closed port, then on a fresh clone into Ubuntu 22.04 and 24.04. Both corrections
above came from that; the second is invisible to a test suite.

## One thing the AI got wrong

`convert_amount` first computed inside a widened decimal context, with a comment
explaining that the default 28 significant digits were not enough for the largest
amount this service accepts times the largest rate the ECB publishes. It reads
plausibly. It was wrong.

A comment asserting a numeric fact is a claim that can be run, so I ran it:

```python
a = Decimal("999999999999.1234567890")   # the ceiling, at ten decimal places
r = Decimal("20566.11")                  # EUR/IDR, the largest rate published
# prec=28 -> 20566109999981972.91590282079
# prec=50 -> 20566109999981972.915902820790   equal, and identical to the cent
```

The largest product possible here is about 2.1e16, nineteen digits once rounded to
the cent, comfortably inside the default. The widened context and its justification
both came out. The failure worth naming is not the arithmetic: the comment was more
confident than the code had earned, and a wrong comment is worse than none because
it makes every other comment something you have to check. Ranking that same failure
in someone else's file is most of `REVIEW.md`.
