# @nexovue/browser-sdk

Real user monitoring for door 3, the browser door. It reports what real visitors
experienced on a customer's own site: Core Web Vitals, JavaScript errors, page
views, and a session id that joins them together.

It is the first thing this repository ships to somebody else's page, so the
first rule is that it can never break that page. Every entry point is wrapped,
every failure is counted rather than thrown, and a refused configuration hands
back an inert handle instead of `undefined`.

## Using it

```js
import { init } from "@nexovue/browser-sdk";

init({
  siteKey: "nvp_live_xxxxxxxxxxxxxxxxxxxxxx",
  endpoint: "https://ingest.nexovue.com",
});
```

Only `siteKey` and `endpoint` are required. Everything else has a default:

| option                 | default              | what it does                                                     |
| ---------------------- | -------------------- | ---------------------------------------------------------------- |
| `serviceName`          | the page's hostname  | names the property in the console; fills the `service_name` column |
| `serviceVersion`       | unset                | joins telemetry to a release                                       |
| `route`                | unset                | a route template such as `/products/{id}`; never guessed from the path |
| `captureQueryAndFragment` | `false`           | lets the query string and the fragment leave the page (see below)  |
| `sessionIdleTimeoutMs` | 30 minutes           | idle gap that ends a session                                       |
| `sessionMaxDurationMs` | 4 hours              | absolute cap on one session                                        |
| `flushIntervalMs`      | 10 seconds           | how often a visible page sends what it has buffered                |

`init` returns a handle with `active`, `flush()`, `diagnostics()` and
`shutdown()`. Calling it twice returns the first handle and says so once in the
console.

## Three things the customer must configure first

Without these the SDK is doing its job and still reporting nothing useful. All
three are on the customer's side and none can be worked around from script.

**1. Allowed origins on the site key.** A newly minted key has an empty list,
and an empty list allows nothing — every export is refused 403. Add every origin
the pages are served from, `http://localhost:PORT` for development included, and
both `https://example.com` and `https://*.example.com` if apex and subdomains are
both used. A wildcard does not match the apex.

**2. `crossorigin="anonymous"` on cross-origin scripts.** When a script served
from another origin throws, the browser replaces the whole error with the string
`Script error.` — no file, no line, no `Error` object — before any handler sees
it. Fixing it needs `crossorigin="anonymous"` on the `<script src=…>` tag *and*
an `Access-Control-Allow-Origin` header from the origin serving it. The SDK
detects that exact shape and prints the fix once.

**3. The ingest host in `connect-src`.** If the page sends a
`Content-Security-Policy`, the directive governs `fetch()` and
`navigator.sendBeacon()` alike, so a missing entry blocks both transports
silently.

## What it sends

Four kinds of record, on two of the door's three signal paths.

| record          | path                  | shape                                                        |
| --------------- | --------------------- | ------------------------------------------------------------ |
| web vital       | `/browser/v1/logs`    | one INFO log record per web-vitals callback, `eventName: browser.web_vital` |
| JS error        | `/browser/v1/logs`    | one ERROR log record, `eventName: exception`                  |
| page view       | `/browser/v1/traces`  | one root span named `page_view`, navigation start to load     |
| session         | —                     | `session.id` as an attribute on every record above            |

Vitals and errors are **log records, not span attributes**. Traces pass a tail
sampler that keeps errored and slow traces whole and only a percentage of healthy
ones; a fast page load is a healthy one-span trace, so vitals carried on it would
mostly be discarded. Nothing samples log records.

The page-view span has the same problem and it is **not solved yet**. Measured
against the dev stack on 2026-09-05: two `page_view` spans were accepted 200 and
neither reached the `spans` table, and neither was counted in
`endpoint_stats_1m` — the endpoint rollup counts only SERVER and CONSUMER spans
(`derive/endpoints.go`, `inbound`), and a page view is INTERNAL. So today a page
view is neither individually retrievable nor exactly counted. Whether the fix is
a page-view log record, a browser carve-out in the sampler, or a different span
kind is a decision that has not been taken.

Errors deliberately carry **no trace or span context**. The Errors screen counts
distinct `(trace id, span id)` pairs within a group, so stamping every error on a
page with the page-view span's context would report two hundred errors as one
occurrence.

Attribute names are in `src/semconv.ts`, spelled once each. That matters more
than it looks: the door parses OTLP/JSON with unknown fields discarded and
answers 200 either way, so a misspelling is silently dropped rather than
refused — only a test on this side would ever notice.

### `captureQueryAndFragment`

Off by default, and one switch rather than two because the query string and the
fragment are one exposure. With it off, every URL the SDK reports — on page
views, web vitals and errors alike — is **scheme, host and path**, and no
`url.query` attribute is sent.

Why it is off. Attribute masking in the pipeline matches attribute *keys*, not
values, so `session_token=abc123` in a URL would be stored verbatim for the whole
retention period whichever attribute carried it. And the fragment is worse than
the query: an OAuth implicit or hybrid flow returns `#access_token=…`, so a page
view a moment after sign-in would carry a bearer token.

This was measured, not feared. [ADR 0103](../../docs/architecture/decisions/0103-a-url-leaves-the-page-trimmed-to-its-path-unless-the-page-asked-otherwise.md)
has the rows: with the old option off, `url.query` was empty on every record and
`url.full` still carried a 2.4 KB query string containing an order and a postal
address, because it was `location.href`.

Turning it on sends both, with the OpenTelemetry conventions' sensitive
parameters — `X-Amz-Signature`, `X-Amz-Credential`, `X-Amz-Security-Token`,
`sig`, `X-Goog-Signature` — replaced by `REDACTED` with their keys kept. That
list is the presigned-URL list and it will not catch your own token names; if
your query strings carry secrets, leave this off and use `http.route` to tell
pages apart.

## How it gets there

Two transports, and the split is not a preference.

**`fetch` is the primary one**, because it is the only one whose refusals the
page can read. The door echoes the caller's Origin on every refusal and exposes
`Retry-After`, so a wrong key or an origin the key does not allow is diagnosable
from the very first export — and the SDK prints the door's own wording, which is
written to be the customer's whole diagnosis. The site key travels in the
`x-nexovue-key` header; the preflight is cached for two hours.

**`navigator.sendBeacon` is the unload transport**, because it is the only
request that survives the page going away. It can set no headers, so the key
travels in the `nexovue_site_key` query parameter, and its body must be
`text/plain`, which the door reads as OTLP/JSON. It reports nothing but whether
the browser queued it; a refusal falls back to `fetch(…, { keepalive: true })`
with the same safelisted shape.

The door accepts exactly four request headers — `content-type`,
`content-encoding`, `accept`, `x-nexovue-key` — so everything else the SDK has to
say travels in the payload as an attribute. There is no SDK-version header and no
session header, by construction.

Flushing happens on `visibilitychange` → hidden (the primary trigger, and the
last reliably observable moment on mobile), on `pagehide` (the Safari paths where
the first does not fire), and on a 10-second timer while the page is visible.
`unload` and `beforeunload` are **never** registered: either one makes a desktop
Chrome or Firefox page ineligible for the back/forward cache, which would slow
down the customer's own Back button. A test asserts this, web-vitals included.

## Size

The gate is 25 KB gzipped on the built script-tag bundle and
`scripts/measure-size.js` fails the build over it. Roughly 70% of that is fixed
by `@opentelemetry/sdk-trace-web` plus `web-vitals` before a line of our own code
is counted, so the headroom is small enough to lose by accident.

```
pnpm run build && pnpm run size
```

## Developing

```
pnpm --filter @nexovue/browser-sdk test        # vitest, then build, then the size gate
pnpm --filter @nexovue/browser-sdk typecheck
```

Tests are browser code, so each file that needs a DOM opens with
`// @vitest-environment jsdom`, the same as `apps/web`. `just check` runs the
suite through `test-web`.
