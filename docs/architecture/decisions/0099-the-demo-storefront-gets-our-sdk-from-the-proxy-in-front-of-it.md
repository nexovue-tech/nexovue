# 0099 — The demo storefront gets our SDK from the proxy in front of it

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** `tools/otel-demo/` only. This decides how a *demo* page carries our script; it
  says nothing about how a customer's page does, which is a script tag they add.

## Context

`just otel-demo up` is the daily traffic source, and door 3 is the one door it could not feed:
web vitals exist only in a browser, and no browser was reporting through our door.

The obvious place for a script tag is the frontend's own `pages/_document.tsx`. Two facts make
that unavailable:

**The frontend image is pulled, not built.** `otel-demo.sh` runs `compose up --no-deps
--quiet-pull` with no `--build`, so `ghcr.io/open-telemetry/demo:3.0.0-frontend` comes down
prebuilt. Editing the source means a full `npm ci && next build`, minutes and hundreds of MB, on
every version bump.

**The clone is never edited in place.** `tools/otel-demo/upstream/` is gitignored and pinned;
`otel-demo.sh` re-checks it out on drift. The established way to change upstream behaviour is a
patched copy of a config file, bind-mounted from the overlay directory — which is how Envoy and
nginx already get our ingest key, each with a header saying what it changed and to re-check it
when `UPSTREAM_REF` moves.

## Decision

**Envoy injects the script, and `image-provider` serves the bundle.**

The proxy in front of the shop is already a patched, bind-mounted config, so a Lua filter there is
the one place upstream's behaviour can be changed without a rebuild. The bundle is served by
`image-provider` — the demo's only static-file server, and the one Envoy already routes `/images/`
to — so the browser fetches and caches it like any other static file. No new container, no new
route, no change to the patched nginx template.

**The endpoint is `location.origin + "/otlp-http"`**, and that one string is the whole reason this
works twice over. Envoy's existing `/otlp-http/` route rewrites the prefix and forwards to our
receiver, so a person on `localhost:8080` and a container on `frontend-proxy:8080` each resolve it
to their own origin — and both are **same-origin**, so there is no preflight to answer. A browser
still sends `Origin` on a same-origin POST, which is what the door checks.

## Three traps, each measured

**Content-Length must be dropped before the body is buffered.** Envoy does not recompute it for a
Lua-modified body, and by the time `handle:body()` returns the headers have already gone
downstream — so a length written afterwards changes nothing. Measured with it written afterwards:
the header said `11549` while `11882` bytes were sent, which truncates the end of the page in any
client that believes it. Removing the header lets Envoy frame the response itself, which it does
as chunked.

**No `$` may appear in the injected JavaScript.** The image's entrypoint runs `envsubst` over this
file with no variable list, so a template literal would be eaten before Envoy ever saw it. The
only two on that line are the deliberate `${NEXOVUE_SITE_KEY}` and `${NEXOVUE_RUM_RELEASE}`.

**`accept-encoding` is stripped on document requests only.** Lua sees the bytes on the wire, so a
compressed body is unreadable. Scoped by `sec-fetch-dest: document`, which leaves `/images` and
`/otlp-http` alone, and the filter skips entirely if `content-encoding` is set anyway rather than
corrupting the page.

## Alternatives rejected

**Rebuild the frontend image with the tag in `_document.tsx`.** Rejected: it requires editing the
pinned clone, which the whole integration is built not to do, and a full Next build on every
version bump.

**Inline the 61 KB bundle in the Lua filter.** Rejected: it puts a minified blob inside a file
whose entire contract is "diff this against the clone when the ref moves", and re-sends 61 KB on
every page load instead of letting the browser cache it.

**A static-serving container of our own, plus an Envoy route.** Rejected: correct, and buys
nothing over `image-provider` while adding a container, a route and a fourth thing to keep in step
with upstream.

**Un-blank `PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and stop there.** Not rejected — it is
done, and it is ADR-worthy in its own right for giving browser-to-backend trace correlation. But
it is not a substitute: the demo's browser SDK sends **spans**, and the RUM screen reads **web
vital log records**. The two are additive and neither fills the other's screen. This is worth
stating because it looks like the cheaper version of the same thing, and is not.

## Consequences

The storefront's pages report Core Web Vitals and JavaScript errors, per page, with the release
the deploy event names. Verified: `cls`, `fcp` and `ttfb` across four storefront paths, under
`service.version 3.0.0-202609061201`.

**`flushIntervalMs` is 3s here against the SDK's 10s default.** Demo configuration through a public
option, not a product opinion: the demo's pages are short-lived.

**The RUM screen still needs a person, or `just load`.** k6's browser scenario was turned back on
— both reasons it had been disabled were wrong, it is one Chromium at 150 MiB of a 512 MiB limit —
but over 55 completed iterations **zero** exports reached the door, from our SDK or from upstream's
own `FrontendTracer`. Both are silenced equally, so this is k6's browser module and not something
the injection can fix. It stays on for the `browser_add_to_cart` spans it does produce.

The cost accepted: one more patched file to re-check when `UPSTREAM_REF` moves, and a filter that
rewrites HTML in the shop's own request path. There is no opt-out flag — one code path, fixed if
it breaks — which is a deliberate choice to keep the demo simple rather than configurable.
