# Notes

## Decisions

**The date on the answer is the feed's date, never the question's.** The feed
already stamps every response with the publication its rate came from. Asked about
Saturday 2026-08-29 it answers `200` with `"date": "2026-08-28"`, so a service that
never reads that field presents Friday's number as Saturday's and nothing anywhere
looks wrong. Reading it is the whole job; everything below follows from it.

**When the day asked about has no rate, the last earlier publication is returned
and the answer says so.** `rate_date` carries the real date, `asked_date` carries
the question, and a `note` field states the difference in a sentence the model can
repeat to a customer.

*Rejected:* refusing every non-publication day. It is defensible and simpler, but
it makes the tool useless for the most ordinary real question, "what was this worth
last Saturday", and the brief explicitly sanctions the visible fallback.

*Rejected:* returning the fallback under the date that was asked for. That is the
defect this task is built around.

**A fallback may reach back at most 7 days.** Not a guess: I pulled the whole
EUR/USD series from the feed, 7083 published days from 1999-01-04 to 2026-09-01,
and measured the gaps between consecutive publications. The distribution is 1 day
x 5608, 2 x 23, 3 x 1392 (weekends), 4 x 23, 5 x 36. The widest is five calendar
days, at Easter and at Christmas, so the furthest an honest answer ever has to
reach back is four. Seven leaves margin without making the ceiling meaningless. A
gap wider than that is a feed that has stopped, not a holiday, and the service
refuses with `rate_too_stale` rather than quote it.

**A future date is refused before the feed is asked.** Not only to save a request.
The feed answers dates up to about a fortnight ahead with `200` and its most recent
rate: `/v1/2026-09-15` returned the rate published on 2026-09-01, and only
2026-09-16 returned `404`. Passing that through would quote a customer a rate for a
day that has not happened, with the feed's cooperation.

**"Today" is today in Frankfurt.** `datetime.now()` on a server in Istanbul is
already tomorrow for an hour or two each night, which is long enough to refuse a
date the ECB is about to publish, or to accept one it never will.

**The 404 that means two things.** An unknown currency and a date the series does
not cover both come back as `404 {"message": "not found"}`. They need different
answers: one is the caller's typo, the other is not. The currency list settles it,
and it is fetched only on the error path, so a successful conversion is still a
single request. EUR/BRL on 1999-01-04 is the real case; both codes are current, the
pair's history simply starts later.

**`from == to` is refused, not answered with 1.0.** The number would be right and
the provenance would be a lie: there is no ECB publication behind it and no date it
belongs to. The refusal carries the answer in words, so nothing is actually lost.

**v1, not v2.** frankfurter.dev serves both. Asked the same question at the same
moment, `v1` answered `55.9498` dated 2026-09-01 and `v2` answered `55.996` dated
2026-09-02, because v2 blends 84 central banks rather than the ECB alone. The
response this service is required to send says `"source": "ECB via
frankfurter.dev"`, and only one of them makes that true.

**Money is `Decimal` end to end, rounded half away from zero.** The published rate
is parsed straight to `Decimal` before anything sees it as a float, and the rate is
never rounded, only the result. Python's `round()` rounds half to even, which turns
11780.845 into 11780.84.

**Not built, on purpose:** auth, a database, a UI, a Dockerfile, CI, deployment,
and any second endpoint, including the `/health` that `tool.py` has. The brief says
they are not scored, and each one is another surface to keep correct.

**What running it changed, twice.** A closed port does not always refuse a
connection; on some platforms it times out on the connect. The first version
reported that as `upstream_timeout`, "did not answer in time", but nothing had been
established to be slow. A connect timeout now reports the source as unreachable.

The second correction cost the service a real defect. The connect budget started at
two seconds, which sounded prudent and was wrong: httpx charges DNS, the TCP
handshake and the TLS handshake all to that phase, and on a cold process five
attempts at the real feed took 1.32s, 0.76s, 2.66s, 0.42s and 3.03s to get that
far. Two of five over budget, so the first call of a freshly started process
answered `503 upstream_unavailable` against a feed that was perfectly healthy. I
only saw it because I ran the service on Ubuntu rather than on the machine it was
written on. The budget is five seconds now, which costs nothing after the first
call because the connection is pooled, and the numbers above are in the comment
next to it so the next person does not have to rediscover them.

## With another day

- **Single-flight.** Ten identical questions arriving together are ten upstream
  requests today. The cache only helps once the first has returned.
- **Per-currency minor units.** Everything is quoted to two decimals; JPY and KRW
  have none, so `¥1234.00` is quietly wrong in a way `11780.85` is not.
- **A conditional request** using the `ETag` the feed already sends, so a cache
  refresh costs a `304` rather than a body.
- **An evaluation case rather than a unit test** for the fallback wording, since
  what actually matters is whether a model relays "this is Friday's rate" to the
  customer, and no assertion in this repository checks that.
- **Structured logging** of the pair, the date asked, the date answered, and the
  gap between them. That gap going up is the first sign the feed is drifting, and
  right now nobody would see it.

## AI tools

Claude Code, throughout, in the way I normally work: it wrote most of the first
draft of each module and most of the test bodies, and I reviewed and rewrote as it
went. Two things I did not delegate.

The first is the upstream's actual behaviour. Before any code was written I ran
several dozen `curl` calls against the live feed to establish what it really does on
weekends, on future dates, before the series starts, for a currency it does not
price, and for a pair whose history starts late. Every design decision above rests
on an observed response rather than on an assumption about one, and the two
findings that shaped the service most, the silent weekend fallback and the `200` for
near-future dates, are both things no amount of reading would have produced.

The second is verification. `./test.sh` passing is not evidence that the service
works, so I ran it and used it: against the live feed, against a stand-in that
answers HTML, against one that never answers, and against a closed port. The
connect-timeout correction above came out of that and out of nothing else.

## One thing the AI got wrong

In `convert_amount` the first version computed inside a widened decimal context,
with a comment explaining that the default 28 significant digits were not enough
for the largest amount this service accepts multiplied by the largest rate the ECB
publishes. It reads plausibly, and it was wrong.

I checked it rather than shipping it, because a comment asserting a numeric fact is
a claim that can be run:

```python
a = Decimal("999999999999.1234567890")   # the ceiling, at ten decimal places
r = Decimal("20566.11")                  # EUR/IDR, the largest rate published
# prec=28 -> 20566109999981972.91590282079
# prec=50 -> 20566109999981972.915902820790
# equal, and identical to the cent
```

The two agree. The largest product this service can produce is about 2.1e16, which
is nineteen digits once rounded to the cent, comfortably inside the default. So the
widened context and its justification both came out, and the docstring now says why
the default is sufficient instead of why it is not.

The failure worth naming is not the arithmetic. It is that the comment was more
confident than the code had earned, and a wrong comment is worse than no comment
because it makes every other comment in the file suspect. Ranking that same failure
in someone else's file is most of `REVIEW.md`.
