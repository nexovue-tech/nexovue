# 0103 — A URL leaves the page trimmed to its path unless the page's owner asked otherwise

- **Status:** Accepted
- **Date:** 2026-09-07
- **Closes:** the `url.full` half of the key-versus-value gap
  [0101](0101-spans-carry-query-text-and-the-database-poller-does-not.md) named and deliberately
  left open.

## Context

`packages/browser-sdk` ships `captureUrlQuery`, off by default, and its README states the reason:

> Attribute masking in the pipeline matches attribute *keys*, not values, so
> `url.query = "session_token=abc123"` would be stored verbatim for the whole retention period.
> Note that `url.full` carries the same exposure and **cannot be avoided for a page view**.

**The last clause is what this ADR withdraws, and the evidence is in our own store.**
`pagecontext.ts` puts `url.full = location.href` on every record — page views, web vitals and
errors alike — and `location.href` is the query string and the fragment. So the option protects the
attribute nobody reads and leaks the same characters through the attribute everything reads.

**Measured 2026-09-07 against the dev ClickHouse**, not reasoned about. `logs` holds 61,080 records
carrying `url.full`, from three producers:

| producer | records | carrying a query string |
|---|---|---|
| `frontend-proxy` — Envoy access logs, door 1 | 61,080 | 11,841 |
| `frontend-web` — **our browser SDK**, door 3 | 10 | 2 |
| `nexovue-prober` — the synthetic browser probe, door 2 | 1 | 0 |

Every one of the ten SDK records has an empty `url.query`, which is the option doing its job. Two
of them nevertheless stored a **2.4 KB query string** carrying an order and a postal address —
`streetAddress=1600+Amphitheatre+Parkway`, `city=Mountain+View`, `zipCode=94043` — and a third
stored the fragment `#hot-products`. A project configured to send no query string sent one anyway,
and it contained the closest thing the demo has to a customer's personal data.

**The fragment is the worse half and nothing here was watching it.** OAuth's implicit and hybrid
flows return `#access_token=…`, so a page-view record taken a moment after sign-in carries a
bearer token into a store with a thirty-day retention.

**What the specification says**, checked rather than remembered
([semconv URL registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/url/)):
`url.full` is `scheme://host[:port][path][?query][#fragment]` and the fragment SHOULD be included
when known; the values of `X-Amz-Signature`, `X-Amz-Credential`, `X-Amz-Security-Token`, `sig` and
`X-Goog-Signature` SHOULD be redacted by default with the key preserved (`q=Otel&sig=REDACTED`),
matched case-sensitively; and `url.full` **MUST NOT** contain credentials passed as
`https://user:password@host/`.

Those five keys are the AWS and Google presigned-URL parameters. **None of them matches anything we
actually leaked** — not `order`, not `sessionId`, not `#hot-products`. Following the specification
alone would have changed nothing about the rows above.

## Decision

**One switch governs everything after the path, and it is off.** `captureUrlQuery` is renamed
`captureQueryAndFragment`, because a flag that silently governed a fragment while naming only the
query is the defect restated as an API.

- **Off (the default).** `url.full` is `origin + pathname`. No `url.query` attribute. Nothing after
  the path leaves the page, on any record kind.
- **On.** `url.full` is the whole href and `url.query` is sent, both with the specification's five
  sensitive parameter values replaced by `REDACTED` and their keys kept.

**The synthetic browser probe keeps the query and the fragment**, and the asymmetry is deliberate.
`browser.py` reports `page.url` at the moment a step failed, and its own comment says why — *"a
redirect to a login page is exactly the thing worth seeing"*. Trimming that to a path deletes the
diagnosis the record exists for. What it gains instead is the two rules that are unconditional: the
userinfo subcomponent is stripped, which the specification makes a MUST and nothing enforced, and
the five sensitive parameters are redacted.

**Door 1 is not ours and is not changed.** The 11,841 leaking rows above come from Envoy's access
logs and from the customer's own SDKs. The control there is the one ADR 0101 already found —
`masking_config`'s `mask_keys`, which reaches these attributes — and the obligation is to say so
where a customer reads it, not to rewrite their telemetry at ingest.

## Alternatives considered

**Keep `location.href` and rely on pipeline masking.** Rejected for the reason ADR 0101 recorded
about `db.query.text` and then left standing here: `credential_keys` matches attribute *keys*, and
`url.full` names no credential. `masksValues()` is true only for `card_numbers` and
`authorization_credentials`, so a Luhn-passing number or a `Bearer …` run inside the URL is caught
and a postal address is not. Masking was never going to close this.

**Mask `url.full` by default in the pipeline.** Fails [0059](0059-masking-defaults-are-high-confidence-only.md)'s
own test — on by default only for what can never be legitimately needed — and masking is
irreversible, where trimming at the producer leaves the customer a switch that restores the data
from the next page view onward.

**Implement the specification's redaction list and nothing else.** This is the smallest change that
can be called conformant, and it is the one that measurably fixes nothing: not one of the three
leaks above carries any of the five keys. A default that only catches presigned S3 URLs is a
default written for a different threat.

**Truncate `url.full` to N characters.** A token is usually in the first forty.

**Two options, one for the query and one for the fragment.** More precise and worse: nobody sets
two flags to answer one question, and the second would be the one left off.

## Consequences

**A customer whose pages are distinguished by a query string sees them merged.** `/search?q=shoes`
and `/search?q=boots` are one row on the RUM screen until they turn the option on. That is the cost,
it is the customer's to weigh, and `http.route` already exists for the case where the path is not
the identity.

**The name changed and the old one is gone.** `captureUrlQuery` is deleted rather than aliased —
there are no customers and no deployments, so an alias would buy compatibility with nobody and
carry the misleading name forever.

**Two implementations of one list.** The sensitive-parameter keys are now written in TypeScript and
in Python, across a boundary neither language can import. That is the shape
`test_cross_service_contracts.py` exists for, and the list is asserted equal there for the same
reason the integration auth mode is.

**The data already stored is still stored.** This changes what arrives from now on. The 11,834
existing rows carrying a query string are not rewritten, and the trimming does not reach back —
which is worth saying plainly, because "we fixed the leak" reads as "the postal address is gone"
and it is not.
