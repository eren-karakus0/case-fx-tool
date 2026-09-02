# Notes

## Decisions

**The date on the answer is the feed's, never the question's.** Asked about
Saturday 2026-08-29 the feed answers 200 with `"date": "2026-08-28"`, so a service
that never reads that field presents Friday's number as Saturday's and nothing
looks wrong.

**When the day asked about has no rate, the last earlier publication is returned
and the answer says so**, through `rate_date`, `asked_date` and a `note` that
states the difference in a sentence the model can repeat. *Rejected:* refusing
every non-publication day, simpler but useless for the most ordinary question,
"what was this worth last Saturday". *Rejected:* returning the fallback under the
date that was asked for, the defect this task is built around.

**The fallback reaches back at most 7 days.** Measured, not guessed: the EUR/USD
series, 7083 published days since 1999-01-04, has gaps of 1 day x 5608, 2 x 23,
3 x 1392, 4 x 23, 5 x 36. The widest is five, at Easter and Christmas, so an honest
answer never reaches back more than four. A wider gap is a feed that has stopped,
not a holiday, and gets `rate_too_stale`.

**A future date is refused before the feed is asked**, and not to save a request:
the feed answers dates a fortnight ahead with 200 and its latest rate.
`/v1/2026-09-15` returned the rate published on 2026-09-01, and only 2026-09-16
returned 404.

**v1, not v2.** Asked the same question at the same moment, v1 answered 55.9498
dated 2026-09-01 and v2 55.996 dated 2026-09-02, because v2 blends 84 central banks
rather than the ECB alone. Only v1 makes `"source": "ECB via frankfurter.dev"` true.

Smaller, reasoned in the code beside them: "today" is Frankfurt's today, since a
server in Istanbul is already tomorrow for an hour each night; the feed's 404 means
both "no such currency" and "no rate that far back", so the currency list is
fetched on the error path to separate them; `from == to` is refused rather than
answered 1.0, because that number has no publication behind it; money is `Decimal`
from the feed's JSON to the response bytes, never through a float, and only the
result is rounded, half away from zero.

**Not built, on purpose:** auth, a database, a UI, a Dockerfile, CI, deployment,
and any second endpoint, including the `/health` that `tool.py` has.

**Running it changed two things.** A closed port does not always refuse a
connection; on some platforms it times out on the connect, which was reported as
"did not answer in time" though nothing had been shown to be slow. The second was a
real defect: the connect budget started at two seconds, but httpx charges DNS, TCP
and TLS all to that phase, and five cold attempts at the feed took 1.32s, 0.76s,
2.66s, 0.42s and 3.03s. Two of five over budget, so the first call of a fresh
process answered 503 against a healthy feed. I saw it only by running the service
on Ubuntu rather than on the machine it was written on.

Running it there also settled the other budget by accident. Mid-verification the
feed had a bad few minutes, answering one dated path with HTTP 522 after 19.8s and
another after 35.8s. The service returned `upstream_timeout` in four seconds
instead, which is the behaviour the read budget exists for: a model waiting on a
customer conversation cannot hold for half a minute, and no rate is the honest
answer when the source has stopped answering.

## With another day

- **Single-flight.** Ten identical questions arriving together are ten upstream
  requests; the cache only helps once the first has returned.
- **Per-currency minor units.** JPY and KRW have none, so `¥1234.00` is quietly
  wrong in a way `11780.85` is not.
- **Conditional requests** using the `ETag` the feed already sends.
- **An evaluation case, not a unit test,** for the fallback wording: what matters
  is whether a model relays "this is Friday's rate" to the customer.
- **Structured logging** of the gap between the date asked and the date answered.
  That gap growing is the first sign the feed is drifting.

## AI tools

Claude Code throughout, the way I normally work: it drafted most of each module and
most of the tests, and I reviewed and rewrote as it went. Two things I did not
delegate. First, the upstream's behaviour: several dozen `curl` calls before any
code, which is where the two findings that shaped this most came from, and neither
is something reading would have produced. Second, verification: `./test.sh` passing
is not evidence the service works, so I ran it against the live feed, a stand-in
answering HTML, one that never answers, a closed port, and a fresh clone into
Ubuntu 22.04 and 24.04. Both corrections above came from that.

## One thing the AI got wrong

Twice, the same way, and the second time it reached the response body.

`convert_amount` first computed inside a widened decimal context, justified by a
comment saying the default 28 significant digits were not enough. A comment
asserting a numeric fact is a claim that can be run, so I ran it: at this service's
ceiling, `prec=28` and `prec=50` agree exactly. The context and its justification
both came out.

The same failure survived one layer down. `_as_json_number` converted Decimals to
`float` on the way out, with a docstring saying they "round-trip through a binary
float to the same text". They do not past about fifteen significant digits: an
amount of 123456789.1234567891 came back as 123456789.12345679, and a large result
was written `2.0566109999999796e+16` rather than `20566109999999794.34`. That is
the defect this whole service exists to avoid, one step further down the pipe, and
it was hiding behind a comment nobody had run. Decimals are now written straight
into the JSON, and the tests that catch it read the raw response text, because
parsing narrows both sides of the comparison and hides it again.

The lesson is not about decimals. A wrong comment is worse than none, because it
makes every other comment something you have to check yourself. Ranking that same
failure in someone else's file is most of `REVIEW.md`.
