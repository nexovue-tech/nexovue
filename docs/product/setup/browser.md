# Browser monitoring

The browser SDK reports what your visitors actually experience: how long the
page took to become usable, the Core Web Vitals, and the JavaScript errors that
never reach your server logs.

It is a separate setup from your services, with a separate kind of key, and the
reason is worth one paragraph.

## Why the browser uses a different key

A web page cannot keep a secret. Anything your page holds, every visitor holds —
view source is all it takes. Putting your `nvk_live_` ingest key in a page would
publish it, and that key can write into your project until you revoke it.

So browser telemetry uses a **site key**: a different credential, in a different
table, reaching a different door, and able to do exactly one thing — accept
browser telemetry for its own project. It looks like this:

```
nvp_live_xxxxxxxxxxxxxxxxxxxxxx
```

One letter apart from the secret kind, and the letter is the difference: `nvk`
is the secret server key, `nvp` is the public page key. A site key is meant to be
in your page source. It is not a secret, and nexovue stores it in the clear
rather than hashed, because there is nothing to hide.

What restricts it is the **origin**, not the key. Your browser stamps every
request with the page's origin, page script cannot forge that header, and a site
key accepts telemetry only from origins you listed on it.

That is genuine protection against other people's web pages using your key — the
realistic risk for a value lying in public — and it is not an authentication
boundary. A script outside a browser can send any origin it likes. This is why a
site key can do so little: the narrowness of what it grants is the protection,
not the origin list.

## 1. Mint a site key

**Settings → API keys**, the site keys section. You must list at least one
allowed origin — a key with an empty list refuses every request, because origin
is the only thing restricting it.

An origin is a scheme, a host, and a port if it is not the default. Nothing else:

| Allowed | Not allowed | Why |
|---|---|---|
| `https://app.example.com` | `https://app.example.com/` | Trailing slash. An origin has no path |
| `http://localhost:3000` | `https://app.example.com/dashboard` | A path is not part of an origin |
| `https://*.example.com` | `https://*.com` | A wildcard needs at least two labels |
| `https://example.com:8443` | `example.com` | The scheme is part of the origin |

Some specifics that catch people out:

- **`https://*.example.com` does not cover `https://example.com`.** Listing the
  subdomains is not listing the apex. If you serve both, list both.
- **`http` and `https` are different origins.** So are `example.com` and
  `www.example.com`.
- **Add your development origin.** `http://localhost:3000` — with the port,
  because a non-default port is part of the origin. Without it, nothing works
  locally and the failure looks like a broken key.
- Default ports are dropped: `https://x` and `https://x:443` are the same
  origin, and you only need one of them.
- Up to 20 origins per key. If you need more, that is usually two properties
  wanting two keys.

## 2. Install the SDK

```bash
npm install @nexovue/browser-sdk
```

```js
import { init } from "@nexovue/browser-sdk";

init({
  siteKey: "nvp_live_xxxxxxxxxxxxxxxxxxxxxx",
  endpoint: "https://ingest.nexovue.com",
  serviceName: "storefront",
  serviceVersion: "1.4.0",
});
```

Or as a script tag, which exposes a global called `nexovue`:

```html
<script src="https://cdn.jsdelivr.net/npm/@nexovue/browser-sdk@0.1.2/dist/nexovue-rum.min.js"></script>
<script>
  nexovue.init({
    siteKey: "nvp_live_xxxxxxxxxxxxxxxxxxxxxx",
    endpoint: "https://ingest.nexovue.com",
    serviceName: "storefront",
  });
</script>
```

`endpoint` is the same receiver origin your services use. The SDK appends
`/browser/v1/traces` and `/browser/v1/logs` itself — a separate path from the
server door, so no route can resolve both a secret and a public key.

Call `init` once, as early in the page as you can. Earlier means the page-load
span covers more of the load, and an error thrown before `init` runs is an error
nobody records.

### Options

| Option | Default | What it does |
|---|---|---|
| `siteKey` | required | The `nvp_live_` key |
| `endpoint` | required | The receiver origin, absolute `http` or `https` |
| `serviceName` | the page's hostname | What the property is called in nexovue |
| `serviceVersion` | none | Joins the telemetry to a release |
| `route` | none | The route template this page belongs to, such as `/products/{id}` |
| `captureQueryAndFragment` | `false` | Lets the query string and fragment leave the page |
| `flushIntervalMs` | 10000 | How often a batch is sent |
| `sessionIdleTimeoutMs` | 1800000 | 30 minutes of inactivity ends a session |
| `sessionMaxDurationMs` | 14400000 | 4 hours ends a session whatever the activity |
| `sampleRate` | `1` | The share of sessions, 0 to 1, whose page views and vitals are sent. Decided per session, so a kept session is whole; errors are always sent |

**`route` is worth setting.** It is the only page attribute that reaches an
indexed column, which is what lets you compare `/products/{id}` across every
product rather than seeing ten thousand distinct URLs. The SDK will not guess one
from the path, because a guess would be wrong for exactly the paths that matter.
In a single-page application, pass your router's matched pattern.

**`captureQueryAndFragment` is off on purpose.** Query strings carry session
tokens, search terms and, in one case that settled this default, a postal
address. Fragments are where an OAuth implicit flow puts an access token.
Masking in the pipeline matches attribute *names*, not values, so anything in a
URL is stored verbatim for the whole retention period. Turn it on only if you
know what is in your URLs.

## 3. Allow the ingest host in your Content-Security-Policy

If your site sends a CSP — and it should — the browser blocks the export unless
the ingest origin is in `connect-src`:

```
Content-Security-Policy: connect-src 'self' https://ingest.nexovue.com
```

A CSP block is invisible to page script. The SDK sees a network failure it
cannot distinguish from an ad blocker, and it will retry rather than tell you
what happened. The browser's own devtools console names the directive, which is
where this one is actually diagnosed.

## What it collects

| | |
|---|---|
| A page-load span | Named `page_view`, spanning navigation start to load |
| Core Web Vitals | LCP, CLS, INP and the rest, as log records |
| JavaScript errors | Uncaught exceptions and unhandled promise rejections |
| Sessions | 30 minutes idle or 4 hours absolute, whichever comes first |

It does not record clicks, keystrokes, form contents or session replay. Nothing
it sends contains the content of the page.

Batches go every 10 seconds, and on page unload through `navigator.sendBeacon`,
which is the only kind of request that survives a page going away. That is why a
visitor who reads one page and closes the tab is still reported.

## The handle `init` returns

```js
const rum = init({ ... });

rum.active        // false if the configuration was refused
rum.flush()       // send what is buffered now
rum.diagnostics() // what has gone wrong inside the SDK, for support
rum.shutdown()    // detach every listener and stop
```

`init` never throws and never returns undefined, even when it refuses to start.
An observability script that breaks the page it observes is worse than no
observability, so every entry point is wrapped and a failure inside the SDK is
counted rather than raised.

That is also why `rum.active` is worth checking in development: a refused
configuration is silent in every way except the console message and this flag.

## When it will not start

The SDK writes one message to the browser console, prefixed `[nexovue]`, for
each thing only you can fix. It says it once rather than on every occurrence.

**"that is a SECRET ingest key"** — you pasted the `nvk_live_` key into the page.
It refuses to start, and the key is now in your page source: revoke it under
Settings → API keys and mint a new one before doing anything else.

**"siteKey must be a public site key"** — the value is not `nvp_live_` plus 22
letters or digits. Usually a truncated copy.

**"endpoint must be an absolute http or https URL"** — a relative path, or a
missing scheme.

## When it starts but nothing arrives

**HTTP 403, "this ingest key is not allowed from this origin".** The origin your
page is served from is not on the key's list. The browser's network tab shows
the `Origin` header it actually sent; compare it character for character with
what is on the key, including the port and the scheme.

**HTTP 403, "public ingest key presented without a browser Origin".** The
request did not come from a browser. A browser sends `Origin` on every POST,
same-origin ones included, so this means something other than a page is using
the key.

**HTTP 401.** The key is wrong, revoked, or from a different project.

**A CORS error with no status.** Either the CSP above, or an ad blocker, or a
typo in `endpoint` — a wrong path under the ingest host is answered outside the
CORS wrapper, so the page sees an opaque failure rather than a 404.

The console's site key row is the other half of the diagnosis: if it reads
"Never used", no page has ever successfully authenticated with it.

## Errors that say only "Script error."

If your JavaScript is served from a different origin than the page — a CDN — the
browser refuses to tell any error handler anything about an exception thrown
inside it. The message becomes `Script error.`, with no stack and no file. That
is the browser protecting cross-origin scripts, and it is not something the SDK
can work around.

Two things fix it, and you need both:

1. `crossorigin="anonymous"` on the `<script>` tag.
2. `Access-Control-Allow-Origin` on the response your CDN serves the script with.

Without them your error reporting will be a wall of identical `Script error.`
rows. With them you get the message, the file and the stack.

## Your monthly allowance

Browser traffic has its own share of your plan's monthly ingest volume, separate
from your servers'. A busy page cannot spend the allowance your services need,
and a refusal at the browser door says so specifically rather than telling you
your whole month is gone.

If you see a 429 with no `Retry-After`, that share is spent, and the message
names the date it returns.
