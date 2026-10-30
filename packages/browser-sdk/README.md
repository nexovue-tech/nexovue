# @nexovue/browser-sdk

Real user monitoring for [nexovue](https://github.com/nexovue-tech/nexovue): page views, web vitals,
JavaScript errors and sessions from your users' browsers, sent as OpenTelemetry.

## Install

```bash
npm install @nexovue/browser-sdk
```

```ts
import { init } from "@nexovue/browser-sdk";

init({
  siteKey: "nvp_live_…",
  endpoint: "https://ingest.nexovue.example.com",
  serviceVersion: "1.4.2",
});
```

Or without a bundler, from a script tag. The file is `dist/nexovue-rum.min.js` in the package, and it
defines a global `nexovue`:

```html
<script src="https://cdn.jsdelivr.net/npm/@nexovue/browser-sdk@0.1.2/dist/nexovue-rum.min.js"></script>
<script>
  nexovue.init({ siteKey: "nvp_live_…", endpoint: "https://ingest.nexovue.example.com" });
</script>
```

## Before it sends anything

Create a **site key** in the console, under *Settings → Ingest keys*, and list every origin your pages
are served from — `http://localhost:5173` for development too, and both `https://example.com` and
`https://*.example.com` if you use both. A key with no origins refuses everything.

A site key (`nvp_live_`) is public: it can only send browser telemetry to one project. Never put a
secret ingest key (`nvk_live_`) in a page; `init` refuses one.

## Options

| Option | | Default |
|---|---|---|
| `siteKey` | the site key | required |
| `endpoint` | the origin your nexovue receives telemetry on | required |
| `serviceName` | names the site in the console | the page's hostname |
| `serviceVersion` | joins telemetry to a release | — |
| `route` | the route template, e.g. `"/products/{id}"`; never guessed from the URL | — |
| `captureQueryAndFragment` | send the query string and `#fragment`; off because they often carry tokens or personal data | `false` |
| `sampleRate` | share of sessions, 0 to 1, whose page views and vitals are sent; errors are always sent | `1` |
| `sessionIdleTimeoutMs` | a session ends after this long idle | 30 minutes |
| `sessionMaxDurationMs` | and never lasts longer than this | 4 hours |
| `flushIntervalMs` | how often buffered telemetry is sent | 10 seconds |

## The handle

`init` never throws. It returns a handle; `active` is `false` when the configuration was refused, and the
reason is in the browser console.

| | |
|---|---|
| `active` | whether telemetry is being collected |
| `flush()` | send what is buffered now |
| `shutdown()` | stop collecting and remove every listener |
| `diagnostics()` | counts of what went wrong inside the SDK, for support |

## License

Apache-2.0.
