# 0104 — A limiter that cannot count leaves the door open and bounds it in process

- **Status:** Accepted
- **Date:** 2026-09-07
- **Relates to:** [0087](0087-the-origin-is-what-ties-a-public-key-to-its-owner.md), which recorded that the ingest
  subject is the key and never the address

## Context

`gates.go`'s `throttle` allows the request when Valkey cannot be reached, and says why:

> Fail open. A Valkey blip must not turn into an ingest outage: losing a customer's telemetry costs
> more than briefly losing the protection, and the broker and payload caps still bound the damage.

`phase-1-plan.md` §7c records this as *"worth revisiting once a paying tenant can be harmed by it"*
and `phase-1-v2.md` §4a puts it in the same decision as G6, the unbounded-bill question.

**That pairing is wrong, and correcting it is most of this decision.** Two different things bound
two different risks:

- The **allowance** bounds volume, and therefore the bill. `allowance/allowance.go` is explicit
  that it is *"the free tier's boundary, and only the free tier's… no paying customer is ever
  refused for volume"* (ADR 0093).
- The **limiter** bounds rate, and therefore the platform. `RECEIVER_RATE_LIMIT_PER_SECOND`
  defaults to 1000 per key per second.

A paid tenant sending at exactly their limit, forever, produces an unbounded bill with the limiter
working perfectly. So the limiter is not what stands between us and G6 — the plan's own pricing is.
What the limiter stands between us and is a burst large enough to hurt every *other* tenant, which
is G5's question, not G6's.

**What is actually unbounded today.** While Valkey is unreachable, no per-second bound applies to
anyone, and the allowance does not apply to a paid plan at all. The remaining bounds are the body
cap and the broker — both of which limit one request and the throughput behind it, neither of which
limits requests per second. A Valkey outage is also precisely when the platform has least headroom
to absorb one.

## Decision

**The door stays open, and it bounds itself while the counter is unavailable.** A bounded
in-process token bucket per key prefix, engaged only on the error path, sized as a multiple of
`RECEIVER_RATE_LIMIT_PER_SECOND` rather than equal to it — the fallback exists to stop a burst, not
to reproduce the limit, and reproducing it would refuse traffic that Valkey would have allowed.

Three properties, stated because each is a thing someone will otherwise assume:

- **It is per receiver instance.** The effective bound is the multiple times the number of
  instances, and it grows as the fleet grows. An in-process limiter cannot be otherwise, and
  pretending it is a cluster limit is how a fallback becomes a lie.
- **The map is bounded**, for the reason `cache.go` already gives: the key prefix on an invalid
  request is attacker-chosen, so an unbounded map is an unbounded memory cost handed to whoever can
  reach the port.
- **The degraded log line is unchanged.** `Traffic.Degraded(reasonLimiterDown)` already writes one
  line per reason per summary window, and the fallback is not a second reason.

**This is one build row, not built here.** It belongs beside G5, and the multiple is a number
somebody watches rather than a number somebody picks — the same rule the §4 budgets are held to.

## Alternatives considered

**Fail closed.** Every customer's telemetry stops because our Valkey is down, which is the failure
the current comment refuses and it is right to. It also inverts the blast radius: a limiter outage
would take out the customers who were within their limit, which is all of them.

**Leave it unbounded, as today.** Defensible while the free tier is the only tier, because the
allowance still caught volume. It stops being defensible the moment a paid plan exists, and a paid
plan is C-series work already scheduled.

**Use the allowance as the fallback.** It does not apply to paid plans, which is the only case that
motivated the change.

**Cache the counter locally and reconcile.** More faithful and much more code, and it re-introduces
the shared state whose absence is the point of a fallback.

## Consequences

**A long Valkey outage now refuses some traffic**, where today it refuses none. That is a real
behaviour change and the honest way to describe it is that the fallback trades a small amount of
telemetry for a bound on the worst case.

**There are two limits to reason about**, and a refusal during an outage will not match the
configured number. The log line has to name which limit refused, or the first support conversation
about it is unanswerable.

**§4a of `phase-1-v2.md` was wrong** to file this with G6, and the file is corrected rather than
quietly re-filed. The limiter is a G5 concern.
