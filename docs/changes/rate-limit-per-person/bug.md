# Bug: a team behind one office network shares one person's read limit

## Reported behavior

`just e2e` fails 2 synthetics specs on the pushed code (`dcfc9b2`) from a clean start, and 5 once F26's
`members.spec.ts` joins the suite. Every failure renders "Too many requests. Retry after the interval in
Retry-After." (2026-09-19/20, recorded in `docs/features/team-and-api-access/status.md`). F18's status
had already recorded the cause as a decision for later: *"The telemetry tier's quota and concurrency cap
are keyed by IP first. Every console user behind one NAT shares 60 reads a minute."*

## Reproduction

The API log of one Stage 1 e2e run (5 min): 620 requests, 149 refused — 280 of the 343 refusals across
runs on the telemetry tier (`policy=telemetry`), on ordinary screen reads: `/synthetics/checks`,
`/services`, `/synthetics/checks/trend`, `/services/trend`. The suite's four browsers reach the API
through the web container's proxy, so the API sees one address.

`tests/test_ratelimit_callers_live.py` reproduces it without a browser: two people signed in to one
organization, from one address, each opening a screen — the second is refused on the first's budget.

## Root cause

`RateLimitMiddleware._identities` (`middleware/ratelimit.py`) counts every request against the caller's
**address first**, and holds the concurrency cap on that address (`_serve`, the first identity). A
signed-in person's session is counted too, but only in addition. So the per-address bucket — 60 reads a
minute and 8 in flight on the telemetry tier — is shared by everyone behind one network. That was
invisible while every organization was one person; F26 made teams, and a team in one office is exactly
several people behind one address. One console tab reads about 13 times a minute, so roughly five
colleagues exhaust it.

## Customer impact

Pre-deployment, none. For the first team customer: a sixth colleague opening Services sees "could not be
loaded", intermittently, depending on what the others are looking at — in the product whose job is to be
readable during an incident, which is when a whole team opens it at once.

## Fix

The founder's decision (2026-09-20): **signed-in reads are counted per person, with a per-organization
ceiling**; per-address limits remain for signed-out and sign-in routes. A token that resolves to no live
session is counted per address, so an invented token buys nothing. See `status.md`.

## Regression test

`tests/test_ratelimit_callers_live.py`.
