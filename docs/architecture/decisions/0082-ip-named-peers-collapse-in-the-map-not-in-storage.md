# 0082 — A peer named by a bare IP is stored as it was asserted and collapsed into one node for display

- **Status:** Accepted
- **Date:** 2026-08-31
- **Answers the open question in:** [0080](0080-an-unanswered-call-is-named-by-what-the-caller-asserted.md)

## Context

[0080](0080-an-unanswered-call-is-named-by-what-the-caller-asserted.md) names a callee that never
reported for itself from what the caller's own span asserted, most specific first, and left one
question open: **should a bare IP be allowed to name a node?** It recorded the defect as
`checkout → (172.19.0.11)` and `checkout → (172.19.0.19)` — one dependency wearing two names, and
two more after the next restart.

Re-measured on the same stack a day later, the demo now draws **three**:

```
checkout -> (172.19.0.11)  kind=grpc
checkout -> (172.19.0.15)  kind=grpc
checkout -> (172.19.0.19)  kind=grpc
```

The newest such row is `2026-08-31 06:10`, so this is current behaviour and not a historical
artefact. The count grows with container restarts, which is exactly the unbounded-cardinality
argument 0080 made against letting an address name a node.

0080 also connected this to billing, because the billing unit is distinct service names per project
per day ([0077](0077-the-billing-unit-is-a-table-and-the-price-is-a-dated-row.md)). **That concern
is withdrawn.** Raj verified that the billable count is derived only from `service.name` on
telemetry a service sent about itself, so an inferred peer is never billable. No guard was added,
because there is no exposure to guard.

## Decision

**Keep storing the address; collapse it for display.**

The address is what the caller genuinely said, and storage should hold the most specific truth
available — the same principle 0080 used to reject technology-first naming. Presentation can always
aggregate upward and can never recover detail it discarded.

So `service_edges_1m` is unchanged, the API's answer is unchanged, and the **service map** groups
address-named peers of the same `server_peer_kind` into one node.

### The collapse lives in the screen, not in the API

Two reasons, and the second is the one that decides it.

Collapsing in the API would discard `.11` versus `.19` from every consumer of
`GET /service-map` — which is the same mistake 0080 rejected one level up, just moved from the
database to the wire.

And merging two edges requires a merged latency that **cannot be recovered**. By the time the API
has produced a `ServiceEdge` its `quantiles` state has already been reduced to `p50/p95/p99`
scalars; two sets of percentiles cannot be re-merged into a third. The API would have to invent a
number. In the drawing the merge is a deliberate, stated reduction instead — see below.

### What counts as an address

Deliberately strict, because a false positive collapses a *real* named dependency into the host
group, and 0080 rejected filtering precisely because a heuristic might drop "a hostname that happens
to look like an IP".

- **IPv4** — exactly four dot-separated all-digit labels, each 0–255. A DNS name's rightmost label
  may not be all-numeric (RFC 1123 §2.1), so a dotted quad is unambiguously **not** a hostname.
  This rejects `1.2.3`, `1.2.3.4.5`, `999.1.1.1` and `10.0.0.1.internal`, and it leaves
  `db2.example.com` alone.
- **IPv6** — two or more colons. A hostname cannot contain a colon at all, and a `host:port` carries
  exactly one, so the count settles it. Brackets and a trailing port are stripped first.

### The WHATWG URL parser was tried and rejected, measured

The URL Standard's own [host parser](https://url.spec.whatwg.org/#concept-host-parser) makes this
exact distinction — its "ends in a number" checker IPv4-parses any host whose last label is all
digits. It looked like the right primitive, and it is wrong in **both** directions:

```
new URL("http://1").hostname          -> "0.0.0.1"     a one-label name becomes an address
new URL("http://1.2.3").hostname      -> "1.2.0.3"     it invents an octet
new URL("http://010.1.1.1").hostname  -> "8.1.1.1"     it re-reads 010 as octal
new URL("http://fe80::1")             -> throws        unbracketed IPv6 is not a URL host
```

It over-catches short numeric hostnames — the precise failure 0080 warned about — and it *misses*
the unbracketed IPv6 spelling that `server.address` actually carries, because a URL requires
brackets and a bare `server.address` has none. It would also **rewrite** an address to a different
address, which a display layer must never do. Hand-rolled and strict beats a spec parser built for
a different question.

### What the node is called

`(postgresql hosts)`, `(grpc hosts)`, or `(unnamed hosts)` when no caller named a technology.

The parentheses are 0080's convention for *a name we were told rather than one that reported for
itself*, so `isPlaceholderService` recognises the group with no knowledge of this rule: it stays
dashed, italic and unlinked for free.

**The count is deliberately not in the name.** The name is also the React Flow node id, so
`(3 grpc hosts)` would silently become a *different node* the moment a fourth instance appeared —
re-introducing the churn this decision exists to remove. The count is data on the node instead,
rendered as the second line (`grpc · 3 instances`), and the hover lists every address, which is how
the detail the storage kept is given back to the reader.

### The merged edge

Two edges that now share a caller, a callee and a kind must become one, because two React Flow edges
sharing an id silently become one anyway — a bug the edge id already carries a comment about.

- Counts are **summed**.
- The error rate is **recomputed against the merged total**, never averaged from the two rates.
- Latency is the **worst** p50/p95/p99 of the group, not an average. The worst is true of at least
  one instance; an average is true of none.

Measured against 59 real edge rows, running the shipped `buildServiceGraph`: 59 edges in, 57 out,
**743,798 calls in and 743,798 out**, no duplicate edge ids, and the three addresses above drawn as
one `(grpc hosts)` node carrying `3 instances`. Named peers — `(valkey-cart)`, `(astronomy-db)`,
`(flagd)`, `(uninstrumented)` — were untouched.

## Alternatives considered

**Collapse in the API, exposing a `callee_group` field — rejected.** The map would need no rule of
its own and every client would agree. Rejected because it throws the individual addresses away for
every consumer, which is 0080's own argument against technology-first naming applied one level
higher; and because the API cannot honestly merge two already-reduced percentile sets, so the field
would carry a fabricated latency.

**Reject IP literals at write time and fall through to the technology — rejected.** 0080 listed this
and it is the option this ADR most directly declines. It makes `(postgresql)` out of three distinct
hosts *permanently*, with no later change able to separate them, which is the disaggregation trap
again. Storage keeps the address; only the drawing aggregates.

**Cap distinct peer names per project per window and fold the overflow into `(uninstrumented)`
— rejected.** 0080's third option, and it was motivated by a billing exposure that turns out not to
exist. It would also put the cap in the write path, where a burst of restarts would silently start
discarding real peers, and it re-creates the single false hub 0080 was written to break apart.

**Only collapse when there are two or more addresses — rejected.** A single address would keep its
own `(172.19.0.11)` node, which is more specific. Rejected because the node would then *rename
itself* when a second instance appeared and rename back when it went away — the same instability
under a different trigger. Collapsing unconditionally costs nothing: the hover carries the address.

**Widen the IPv4 rule to catch octal quads like `0300.1.1.1` — rejected.** It is `192.1.1.1` to an
octal-aware resolver and we leave it as its own node. Accepting labels longer than three digits
would start matching strings a hostname could legitimately use, and the failure mode of missing one
is the honest status quo rather than a wrong merge. A test documents the choice.

## Consequences

**One place collapses and one does not.** The service *detail* screen's dependency panel still lists
`(172.19.0.11)` and `(172.19.0.19)` as two rows. That is a real inconsistency a customer could
notice, and it is accepted deliberately rather than papered over: `apps/web/CLAUDE.md` forbids one
feature importing another, so sharing the predicate means moving it to `shared/`, and duplicating it
into a second feature is the workaround rule 5 exists to prevent. **Follow-up work**, named here
rather than done badly. It is less damaging in a list than on a map — two rows in a list are an
honest enumeration of two edges, where two nodes on a graph assert two dependencies.

**A window covering 2026-08-30 14:51–14:59 still draws the old `(grpc)` hub.** 341 calls across
three services landed under a callee named `(grpc)` during the `rpc.system` experiment 0080 records
as tried-and-reverted. Nothing has written one since. Those rows are not addresses, so this
decision does not collapse them; they fall out of any recent window on their own and retention
clears them. It is not a regression — do not report it as one. Deleting stored rows to tidy a
display is not worth a mutation on an `AggregatingMergeTree`.

**A collapsed node's latency is the group's worst, and reads higher than any single instance's
typical.** Stated on the node rather than inferred, but a reader comparing it against a per-address
number they got elsewhere will see a discrepancy, and that is the cost of merging at all.

**The rule is one more place that must be kept in step with 0080's parentheses convention.** That
convention had already failed to reach one call site: `servicedetail/api.ts` still tested
`isPlaceholderService` against a closed list of two names, so *every* peer named from a caller's
assertion — `(valkey-cart)`, `(astronomy-db)` — rendered as a link to a service screen that can
never have anything on it. Fixed to the parentheses test in the same change. This is exactly what an
open-set convention is for, and exactly how it fails when a copy of it is written down as a list.

**IPv6 collapsing is unverified against real data.** The demo emits no IPv6 peer. The rule is
covered by unit tests only, including `::1`, `fe80::1`, `2001:db8::8a2e:370:7334`, `[::1]`,
`[2001:db8::1]:5432` and `::ffff:10.0.0.1`.

### Withdrawn: percentiles are not non-reproducible

Recorded here because the claim was made in this work and must not be left standing.

While writing the live test, two back-to-back reads of `endpoint_stats_1m` returned different
percentiles — `flagd` p95 `4000.813511899998` then `3412.619052149973` — and this was reported as
`quantiles` reservoir sampling making `quantilesMerge` non-deterministic, with a `quantilesTDigest`
migration proposed.

**It does not reproduce and the claim is withdrawn.** Re-measured: eight reads over a fixed literal
window (`minute >= toDateTime('2026-08-30 14:00:00') AND minute < ...`) were byte-identical, and ten
reads of the original unwindowed query hashed identically (`ac18755cd2518e76d820edd682f6abe7` ×10).
`system.part_log` shows **49 `NewPart` and 43 `MergeParts` events on that table between 05:05 and
06:26 server time**, spanning the original measurement — so rows were arriving between the two
reads and the *input set* changed, not the arithmetic. Merging fixed states is deterministic. No
migration is warranted and the proposal was cancelled.

The narrower true statement survives: `quantiles` is reservoir sampling at **write** time, so which
values a state kept is not reproducible from the raw spans. That is inherent to an approximate
aggregate, is not observable as two reads disagreeing, and is not a defect.

The live test written in this change still asserts counts exactly and latency only for ordering.
That is defensible on its own — it is testing the narrowing, not the quantile algorithm — and it
was left as written.
