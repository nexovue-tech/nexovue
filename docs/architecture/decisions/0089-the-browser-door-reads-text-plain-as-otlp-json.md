# 0089 — The browser door reads `text/plain` as OTLP/JSON, because it is the only body `sendBeacon` can deliver

- **Status:** Accepted
- **Date:** 2026-09-01
- **Builds on:** [ADR 0086](0086-a-keys-kind-is-the-table-it-lives-in.md), which gave the public key its own
  table and its own door, and [ADR 0087](0087-the-origin-is-what-ties-a-public-key-to-its-owner.md),
  which decided the transport this record amends

## Context

**`navigator.sendBeacon` could not deliver telemetry to the browser door by any
route.** That is the one moment a browser SDK's data matters most — a visitor
closing the tab is exactly when the session ends and the flush is the last
chance to send it — and both paths were watched failing in a real Chrome
(148.0.7778.280) against the running dev receiver.

**Path one — a `Blob` typed `application/json`.** Not CORS-safelisted, so the
request runs in CORS mode and is preflighted. The preflight passed. The POST
then failed, and Chrome named the cause itself:

> Response to preflight request doesn't pass access control check: The value of
> the 'Access-Control-Allow-Credentials' header in the response is '' which must
> be 'true' when the request's credentials mode is 'include'.

The network log for that attempt reads `OPTIONS … → 204 No Content` followed by
`POST … [FAILED: net::ERR_FAILED]`. The Beacon specification is the reason there
is no way around it: `sendBeacon` initialises its request with credentials mode
`"include"`, always, and offers no option to change it
([w3c/beacon](https://w3c.github.io/beacon/), read 2026-09-01). ADR 0087 §4
deliberately never sends `Access-Control-Allow-Credentials`. Two fixed positions,
and the export dies between them.

**Path two — a `Blob` typed `text/plain`.** CORS-safelisted, so no preflight and
no CORS check, and the request was **delivered**. The receiver then answered
**415**, because `codecFor` accepted `application/x-protobuf` and
`application/json` and nothing else.

So the door was reachable and refused what reached it.

**ADR 0086 §4's justification for the query-string credential was true but
insufficient.** It said "`navigator.sendBeacon` cannot set request headers at
all", and moved the credential into the URL to answer that. Correct, and not
enough: the credential arrived and the *body* was refused. The query parameter
solved half a problem and the record read as though it had solved all of it.

## Decision

### 1. `text/plain` is read as OTLP/JSON, on the browser door and nowhere else

The map is the whole change:

```go
var browserContentTypes = contentTypes{
	contentTypeProtobuf: protobufCodec,
	contentTypeJSON:     jsonCodec,
	contentTypeText:     jsonCodec,
}
```

`text/plain` is not an OTLP encoding and this is not a claim that it is. It is
the CORS safelist's requirement — `application/x-www-form-urlencoded`,
`multipart/form-data` and `text/plain` are the three content types a
cross-origin POST may carry without a preflight
([MDN CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)) —
and the body behind it is OTLP/JSON like any other. `charset=UTF-8`, which a
browser appends, parses off as a parameter and does not change the media type.

The reply is written in `application/json`, because that is what the body was.

### 2. The server door cannot accept it, and that is structural rather than remembered

Two package values, not one value and a flag:

```go
var serverContentTypes = contentTypes{
	contentTypeProtobuf: protobufCodec,
	contentTypeJSON:     jsonCodec,
}
```

`contentTypes` is a map from media type to codec, and `codecFor` is a probe into
it. The server door's set has no `text/plain` entry, so there is no branch to
get wrong and no condition to remember: a `text/plain` export on `/v1/traces`
misses the map and is refused 415, by the same code that refuses
`application/xml`.

Which set a door gets is fixed where the two entrances are named together, and
is the only line in the file where they differ:

```go
mountSignals(mux, "", doors.Server, serverContentTypes)
mountSignals(mux, BrowserPathPrefix, doors.Browser, browserContentTypes)
```

`Options.accepts` is **unexported**, so `cmd/receiver/main.go` — which builds
both doors from one shared value — cannot set it, widen it, or accidentally
share one door's set with the other. This is the third time the same argument
has been applied: 0086 gave each kind its own table so a credential comparison
could stay unconditional, 0086 §"BrowserPathPrefix" gave each door its own route
so a gatekeeper could not be chosen per request, and this gives each door its
own content types so an affordance cannot be reached from the wrong entrance.

### 3. Accepting the content type is not accepting the content

`text/plain` maps to the **same** `jsonCodec` the `application/json` path uses.
The body goes through `normalizeJSONIDs` and `protojson.Unmarshal` unchanged,
so a beacon whose body is not JSON, or is JSON of the wrong shape, is refused
`400 INVALID_ARGUMENT` with the signal's own malformed message. Nothing about
the header relaxes what is parsed. There is one codec for OTLP/JSON and three
media types that reach it, rather than a permissive path beside a strict one.

### 4. Correcting ADR 0087 on the mechanism, without reopening its decision

0087 §4 says allowing credentials "would carry a visitor's cookies and client
certificates to us on every export". **The outcome was right and the mechanism
was described wrongly, and the difference now matters.**
`Access-Control-Allow-Credentials` governs whether the *page* may read a
credentialed response; it has never governed what the browser *sends*. On the
preflighted path the effect was the same — without the header the browser
refused to send the request at all, so nothing arrived — but on the safelisted
path there is no preflight to fail, so **a `sendBeacon` export now reaches us
with whatever cookies the visitor holds for the receiver's own host**, and
declining the header does not prevent it. See the consequences below; the
decision to keep declining the header stands, for the reason in §5 of the
alternatives.

## Alternatives considered

**Send `Access-Control-Allow-Credentials: true`.** The direct fix for path one,
and ADR 0087 refused it. Not reopened here. What it would buy is narrower than
the record implied — it would let a page *read* a credentialed response from a
write-only door that returns a constant — and what it would cost is that every
browser caller of every signal path would be told credentialed cross-origin
reads of this door are permitted, in exchange for a beacon path that
`text/plain` opens anyway. The corrected mechanism in §4 changes the reasoning,
not the answer.

**Accept that the unload flush is lost.** The honest alternative and the one
worth naming, because it costs nothing to build. Rejected: the flush is not one
export among many. A visitor closing the tab is the end of the session, so the
beacon carries whatever the SDK has buffered since the last flush plus every
end-of-session signal there is. Losing exactly the last export of every session
is not a uniform sampling loss; it is a hole in one shape of data.

**`fetch(url, { keepalive: true })` instead of a beacon.** 0086 rejected this and
the current source strengthens the rejection rather than weakening it.
`@opentelemetry/otlp-exporter-base@0.222.0`'s `fetch-transport.js` — read
2026-09-01 — carries these comments in shipped code:

> Browsers enforce a 64KiB cumulative limit across all pending keepalive requests.
> We use 60KB to leave headroom for headers.

> Chrome enforces 9 concurrent keepalive fetch requests per renderer process.

and it **disables `keepalive`** when either bound would be crossed, which
converts the export into an ordinary fetch that a closing page cancels. So
`keepalive` fails silently at exactly the size and concurrency where an unload
flush is largest.

**One of the other two safelisted content types.**
`application/x-www-form-urlencoded` or `multipart/form-data` would work
identically at the CORS layer. Rejected: both announce a form encoding the body
does not have, so a reader of a captured request would be told something untrue
about it. `text/plain` claims only that the bytes are text, which they are.

**A content type of our own — `application/x-nexovue-beacon`.** Rejected on the
spec: the safelist is closed. Any media type outside those three preflights, and
we are back at path one.

**A `bool` on `Options` — `AcceptsTextPlain` — set for the browser door.** The
smaller diff. Rejected: it is a condition someone has to remember on a
credential-adjacent path, which is the shape ADR 0086 spent two tables to avoid.
A door that structurally cannot reach an affordance beats a door that is
configured not to.

**Accept `text/plain` on both doors, for symmetry.** Rejected. A server exporter
has no CORS problem to solve and no reason to send it, so the only thing
symmetry would buy is a second way to reach the JSON decoder on the path a
secret key opens.

## Research

**Sentry's ingest does exactly this, and says why.** The envelope endpoint's own
specification: "To minimize the necessity for `CORS` preflights it's acceptable
to send `text/plain`, `multipart/form-data` and `application/x-www-form-urlencoded`
as well", beside `application/x-sentry-envelope` as the official type
([Sentry develop — Envelopes](https://develop.sentry.dev/sdk/data-model/envelopes/),
read 2026-09-01). Worth stating precisely: that is Sentry's **server** accepting
the safelisted types. Their current browser SDK does not use `sendBeacon` —
`browser.sentry-cdn.com/8.0.0/bundle.min.js` contains `keepalive` and no
`sendBeacon` — so their motivation is the CORS preflight itself rather than the
beacon specifically.

**Datadog's shipped browser SDK does use the beacon, with `text/plain`.** Read
out of the published bundle (`unpkg.com/@datadog/browser-rum/bundle/datadog-rum.js`,
2026-09-01) rather than from documentation, because the documentation does not
say. The payload is built as newline-joined JSON and then:

```js
e = "string" == typeof t.output ? t.output : new Blob([t.output], {type: "text/plain"})
```

and sent with `navigator.sendBeacon(e, n.data)`, falling back to
`fetch(…, {method:"POST", body: e.data, mode:"cors"})`. A raw string handed to
`sendBeacon` is transmitted as `text/plain;charset=UTF-8`; the compressed case is
an explicit `text/plain` `Blob`. So Datadog's intake takes JSON under a
`text/plain` header, which is this decision, already shipped by somebody else.

**The problem is a known one upstream, and unfixed.**
[opentelemetry-js#3062](https://github.com/open-telemetry/opentelemetry-js/issues/3062)
records the same failure — "sendBeacon initialize the request with `credential
mode` set to `true` with a non-simple `content-type`" — and notes the Zipkin
exporter avoided it by sending `text/plain`. Current OTel JS has removed the
beacon transport altogether: `@opentelemetry/otlp-exporter-base@0.222.0`'s
`build/esm/transport/` holds `fetch-transport.js`, `http-exporter-transport.js`
and their types, and no `send-beacon-transport.js`. **So this affordance helps
our own SDK (P1-39) and does not help a customer wiring up stock OTel JS** —
that exporter sends `application/json` over fetch and will keep preflighting.

**Not found, stated as not found:** no vendor documents the cookie consequence
of the safelisted path. The reasoning in §4 and the consequences below is ours,
from the Beacon and Fetch specifications, not cited to anybody.

## Consequences

### What it buys

A browser SDK can flush on `pagehide`. Watched end to end, in Chrome, against the
dev stack: a `text/plain` beacon fired from a `pagehide` handler as the page was
replaced was accepted `200`, and its span is a row in ClickHouse —
`trace_id 0089dddd…`, `span_name unload-flush`, under the site key's own project.
The evidence is in "What was verified".

### The preflight is gone, and here is precisely what that changes

**It removes no defence, because the preflight was never one.** ADR 0087 §3 made
`OPTIONS` answer permissively to *every* origin on purpose — a preflight carries
the credential's header *name* and never its value, so the door cannot know whose
key it will be. Nothing was ever refused there. What follows is what does change.

- **The body is on the wire before anything can judge it.** A cross-origin
  `application/json` export cost the sender a round trip before the body; a
  `text/plain` one does not. The gate order is unchanged — authenticate, then
  origin, then limiter, then decode — so a refused beacon still spends no rate
  budget, is never parsed and never reaches Kafka. But the bytes arrive, up to
  `RECEIVER_MAX_DECODED_BODY_BYTES`, and are read off the socket before the first
  gate answers. **The realistic size of this is small**: `Max-Age: 7200` cached
  the preflight for two hours, so even before this change a sustained sender paid
  one `OPTIONS` per origin per two hours. What is lost is one round trip per two
  hours, not a filter.
- **What a scraped key costs its owner is unchanged.** 0087's ceiling stands: an
  attacker who forges the `Origin` header can spend the key's per-second limit at
  up to `RECEIVER_MAX_DECODED_BODY_BYTES` per request until the key is retired —
  1000/s × 16 MiB on today's defaults. A non-browser attacker never sent a
  preflight in the first place, because curl does not. For an *in-browser*
  attacker the effort drops from "one cached preflight" to "nothing", which is
  not a meaningful reduction of an effort that was already near zero.
- **The origin check is now the only thing between a page and the door — and it
  already was.** `Origin` is a forbidden request header, so page script cannot
  set it, and the gate refuses an origin the key does not list before the limiter
  runs. That property is untouched by this change, and so is its limit, which
  0087 states and this record repeats: **a non-browser client sends any header it
  likes**, so the origin list bounds abuse by other people's *web pages* and is
  not an authentication boundary.

### The new cost, and it is a deployment constraint

**A beacon now arrives carrying the visitor's cookies for the receiver's own
host.** `sendBeacon` fixes credentials mode to `include`; on the preflighted path
the request never left the browser, so nothing was sent, and on the safelisted
path it is sent. Declining `Access-Control-Allow-Credentials` does not prevent
this — see §4 above.

Three things follow.

1. **The receiver must never read, log or forward `Cookie`, and today it does
   not.** `callerOf` in `gates.go` reads `RemoteAddr`, `User-Agent` and `Origin`
   and nothing else, and the refusal line carries only those. This is now a
   property to keep rather than an accident.
2. **The ingest hostname must not share a cookie domain with the console.** A
   cookie scoped to a parent domain would be sent to a child, so
   `ingest.nexovue.tech` under a console cookie set for `.nexovue.tech` would
   receive our own users' session cookies on every unload beacon. **This is not
   settled.** `deploy/caddy/Caddyfile` line 10 still reads
   `TODO(hostname): one name for everything, or separate ingest and app names?`,
   and this decision turns that from a routing preference into a privacy
   requirement.
3. **A visitor's cookies for the *customer's* site are not affected.** They are
   scoped to the customer's host and are never sent to ours.

### The refusal a beacon receives is invisible, and 0087 promised otherwise

0087's headline benefit was that "every refusal a browser receives is readable by
the script that caused it". That does not hold for a beacon: `sendBeacon` returns
only whether the request was *queued* — it returned `true` for the export that
then failed with `net::ERR_FAILED` — and exposes no status, no body and no
outcome. A customer whose origin list is wrong gets nothing at all from the
unload path.

**The mitigation is the SDK's shape, and it is a constraint on P1-39, not a fix
here:** normal-operation exports go by `fetch`, where 0087's readable 403 still
arrives and a misconfigured origin is diagnosable from the first export; the
beacon is used only at unload, by which point the origin has already been proven
by every export before it. If P1-39 ever beacons as its *primary* transport, a
misconfigured customer will see silence.

### Smaller costs

- **A refusal on the browser door is now reachable with no browser-side
  ceremony**, from any page on the internet. It is cheap — credential cache, an
  origin list walk, no decode, no limiter, no Kafka — but it is not free, and it
  is **not measured**.
- **A third media type reaches the JSON decoder.** One codec, three keys into it,
  so there is no second parsing path to keep in step — but a reader of
  `codec.go` now has to know why `text/plain` is there, which is why the comment
  names the CORS safelist and this ADR rather than describing the map.

## A second defect, fixed in the same change: the log and the wire disagreed on a status

Pre-existing, and unrelated to beacons except that a `text/plain` refusal was how
it was noticed. `export()` in `handler.go` called `writeStatus(…415…)` directly
while `record()` derived the logged status from the rpc code, and
`INVALID_ARGUMENT` maps to 400. So a refused media type answered **415 on the
wire** and wrote **`status=400 reason=invalid`** in the log. A log line that
misreports a status is how an incident gets diagnosed wrongly: the reader
believes a malformed batch was rejected and goes looking for the sender's
payload, when the door in fact refused the header.

**The root was that the status had two expressions**, and the fix gives it one:
`refusal` gained an `httpStatus` field with `answeredAs(…)`, and both
`writeRefusal` and `record` read `refusal.status()`. The three failures OTLP/HTTP
spells more precisely than the rpc code does — 415 for an unsupported content
type, 415 for an unsupported content encoding, 413 for an oversized body — were
**all three** affected, not the one the symptom showed, and all three are fixed.
`writeBodyFailure` was deleted and replaced by `bodyRefusal`, which builds the
refusal that is then both written and logged, so the two cannot drift again.

## What was verified

**Tests.** `internal/otlp` run with `-v -count=1` and the output counted:
**144 passed, 0 failed**, up from **131** on the same package with the new tests
stashed out — 5 new test functions and 8 new subtest cases, 13 new passing
assertions. Counts read off the output, not assumed.

`just check` was run and watched. Every Go module in the workspace reported
`ok`, no `FAIL` anywhere in the Go output, and `ruff`, `mypy`, `biome`, `tsc`
and the formatters all passed. **It did not pass overall**, and the reason is
environmental rather than this change: the six errors are all in
`apps/api/tests/test_synthetics_live.py`, a suite that asserts on real prober
output and says so —

> no synthetic readings in ClickHouse for the last two days. This suite asserts
> on real prober output, so an empty store is a failure here, not a pass —
> check that the scheduler is running.

Confirmed rather than assumed: `metrics_gauge` holds **0** rows for
`synthetic.check.status`, and the prober is behind the `doors` compose profile,
which is not up. The dev stack's volumes were recreated at 08:04 today by
another agent. The rest of the Python suite is **1406 passed**.

**Five negative controls, each broken deliberately, watched to fail, restored:**

| control | watched |
|---|---|
| `text/plain` removed from `browserContentTypes` | 3 tests failed |
| `text/plain` added to `serverContentTypes` | the server-door refusal test failed: `status = 200, want 415` |
| `text/plain` mapped to a codec that parses nothing | both non-JSON cases failed: `status = 200, want 400` |
| `record` reverted to deriving the status from the rpc code | all three cases failed: `logged status = 400, want 415` / `want 413` |
| `writeRefusal` reverted to the same | four tests failed: `status = 400, want 415` and `want 413` |

**Live, in a real browser** — Chrome 148.0.7778.280 driving a page served from
`http://localhost:8899`, against the dev receiver on `localhost:14318` (that
container restarted, nothing else). The dev stack's volumes had been recreated
at 08:04, so `site_keys` was empty; the key
`nvp_live_beacon0089beacon0089be`, `allowed_origins = {http://localhost:8899}`,
was created for this proof and removed after it.

- **The defect, reproduced:** a beacon typed `application/json` produced
  `OPTIONS → 204 No Content` followed by `POST [FAILED: net::ERR_FAILED]`, with
  the `Access-Control-Allow-Credentials` message quoted in the context above.
- **The fix, watched:** three `text/plain` beacons, all `POST … → 200 OK`, and
  **no `OPTIONS` before any of them** — one preflight in the whole session, the
  one belonging to the failed JSON attempt. The receiver logged
  `path=/browser/v1/traces status=200 items=1 origin=http://localhost:8899` with
  a real Chrome user agent.
- **The unload flush, watched:** a beacon fired from a `pagehide` handler as the
  page navigated away was accepted at `08:16:01.238`.
- **The row that landed:** both beacon spans are rows in ClickHouse under the
  site key's own tenancy — `tenant_id 01a05c00-a6aa-…`, `project_id
  01a05c00-a6c5-…` — including `trace_id 0089dddd0089dddd0089dddd0089dddd`,
  `span_name unload-flush`, `received_at 2026-09-01 08:16:01.237139384`.
- **The server door refuses it, live:** `POST /v1/traces` with
  `Content-Type: text/plain;charset=UTF-8` answered **415** `unsupported content
  type`, while the same body on `/browser/v1/traces` answered **200**.

**One thing the proof had to work around, recorded because it is not obvious.**
The first two beacons were accepted by the receiver and never reached ClickHouse:
the trace-assembly pipeline's sampler drops healthy traces
(`sample/policy.go`, `keepsHealthy`), and a one-span browser trace is healthy.
The spans were re-sent carrying `status.code = 2`, which `KeepErrors` always
keeps, and then landed. **This is the sampler behaving correctly, not a defect** —
but it means a browser SDK's ordinary page-view spans will be sampled like any
other trace, which is worth knowing before P1-39 measures anything.

## What was not verified

- **No SDK sent any of this.** Every beacon was hand-written JSON from a test
  page. There is no browser SDK yet (P1-39), so nothing has exercised batching,
  compression, a real unload race, or a payload near the 64 KiB beacon ceiling.
- **The cookie consequence was not observed**, only derived. The dev receiver is
  on its own host and no cookie exists for it, so no request in this work carried
  one. The deployment constraint in the consequences is reasoning, not a
  measurement.
- **Nothing was measured under load**, and the cost of a refused body arriving
  without a preflight is not quantified.
- **`gzip` under `text/plain` was not exercised.** `Content-Encoding` is a
  separate header the body reader handles, and a beacon cannot set it, so a
  compressed beacon would have to carry an already-compressed body — which is
  what Datadog's `text/plain` `Blob` case is. Untested here.

## Open questions

- **Whether P1-39's SDK should beacon on unload only, or beacon by default.**
  This record argues for the former, on the diagnosability cost above, and does
  not decide it.
- **The ingest hostname's cookie domain.** Raised above; belongs with the Caddy
  routing decision 0087 already left open.
- **The per-key rate ceiling** stays open exactly as 0087 and 0086 left it. This
  change does not move it.
