# 0096 — A browser flow's only way onto the network is a proxy that judges the address

- **Status:** Accepted
- **Date:** 2026-09-04
- **Closes:** the first of the three findings P1-53's security pass left outstanding — *"browser
  synthetic checks have no SSRF boundary at all"*.

## Context

A synthetic check makes **our** infrastructure fetch a URL a customer chose and hands back what it
found. Unguarded, that is a request-forgery primitive: the attacker cannot reach `postgres:5432` or
`169.254.169.254` themselves, so they make our prober reach it and report.

**The HTTP check has a real boundary and the browser check had none.**
`probes/addresses.py` installs `ConnectGuard.open_socket` as libcurl's `OPENSOCKETFUNCTION` — a
socket factory libcurl calls once per resolved candidate, *after* DNS and *before* connect, with
`FRESH_CONNECT`/`FORBID_REUSE` so no pooled connection skips it. 27 tests, including live cases
against `127.0.0.1.nip.io`. Chromium has no such hook: it resolves its own names and opens its own
sockets, and `probes/browser.py` called `page.goto(step.url or target)` with nothing in between.

**`nip.io` is what makes it trivial.** It is a public DNS service whose hostnames contain the
address they answer with, so `127.0.0.1.nip.io` is an ordinary public name that resolves to
127.0.0.1. Run today against `apps/api`'s creation-time validator:

```
ACCEPTED http://127.0.0.1.nip.io:5432/
ACCEPTED http://169.254.169.254.nip.io/
REFUSED  http://127.0.0.1:5432/  -> A target must be a public address, not a private one.
REFUSED  http://postgres:5432/   -> That hostname names nexovue's own infrastructure.
```

That is not a bug in `checks/targets.py`. Its docstring says outright that it refuses literal
private addresses and deliberately never resolves a name, because resolving would answer a question
about *now* and the fetch happens minutes later. It is right, and it assumed a second boundary
downstream. There wasn't one.

**And everything is on one network.** `deploy/compose/` has no `networks:` block in any of its
three files, so every container shares the default one. From the browser worker, `postgres:5432`,
`clickhouse:8123`, `kafka:9092` and `redis:6379` are all reachable.

### The negative control, watched, both arms, inside the real container

Run inside `nexovue-scheduler-browser-1` on the dev stack, 2026-09-04, with the working tree mounted
over the image. Arm 1 launches the browser exactly the way `browser.py` launched it before this
change — `playwright.chromium.launch()`, then `page.goto(url)`. Arm 2 is `browser.perform`. The
loopback service serves a random secret and records every hit; `clickhouse` is nexovue's own
ClickHouse on the shared compose network, at `172.19.0.7`.

| target | arm 1: no boundary | arm 2: `browser.perform` |
|---|---|---|
| `http://127.0.0.1.nip.io:P/` | hits `["/"]`, **secret read** | hits `[]`, refused `127.0.0.1` |
| `http://127.0.0.1:P/` (literal) | hits `["/"]`, **secret read** | hits `[]`, refused `127.0.0.1` |
| `https://127.0.0.1.nip.io:P/` | TCP opened, `ERR_SSL_PROTOCOL_ERROR` | hits `[]`, refused `127.0.0.1` |
| `http://clickhouse:8123/ping` | **page read `Ok.` from our ClickHouse** | refused `172.19.0.7` |
| `http://169.254.169.254/…` | request left, timed out | refused `169.254.169.254` |
| `http://169.254.169.254.nip.io/…` | request left, `ERR_CONNECTION_REFUSED` | refused `169.254.169.254` |

Arm 2's message in every row is `Target resolved to a non-public address and was not contacted: …`,
and `up` is `false` with `steps_passed` 0.

**The ClickHouse row is the finding.** It is not a synthetic loopback service standing in for a real
one — it is a browser check reading nexovue's own database over the compose network and reporting
what it found. The literal-loopback row is the second finding: a `127.0.0.1` URL is one Chromium
sends direct, past any proxy, unless the implicit bypass is subtracted — so arm 2 refusing it is
proof `<-loopback>` was in force. The argv below shows exactly one `--proxy-bypass-list`, and
Playwright adds its own only when ours is absent, so the one in force is ours.

The service's own hit list is the assertion that matters: "the check failed" is not the claim, "the
service was never contacted" is.

## Decision

**Chromium is launched behind a forward proxy that resolves the name, judges every candidate
address, and connects to the literal address it judged.** One process, one boundary, reusing
`is_publicly_routable` — the rule the HTTP check already applies.

### 1. Why a proxy and not network isolation

A proxy is the only mechanism that closes the DNS-rebinding window inside Chromium. **Resolving and
connecting are one step in `_open_checked`**: `getaddrinfo` returns candidates, each is judged, and
`sock_connect` is handed the exact tuple that was judged. Nothing re-resolves in between. That is
the same property `ConnectGuard` has, obtained the only way Chromium allows.

Network isolation was the obvious alternative and it cannot work cleanly here — see Alternatives.

### 2. Chromium's egress, and what each switch holds

`browser.launch_options` is the whole of it. A page that reports "refused" is not evidence — CORS
blocks *reading* a response the request still made — so every arm below was measured with raw TCP
and UDP listeners on loopback recording what actually arrived, inside the browser container.

- **`proxy: {server, bypass: "<-loopback>"}`.** Chromium *implicitly* bypasses any proxy for
  `localhost`, `*.localhost`, `[::1]`, `127.0.0.1/8`, `169.254/16` and `[FE80::]/10`; `<-loopback>`
  subtracts that rule
  ([net/docs/proxy.md](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)).
  Note what is in that list: **the cloud metadata range bypasses proxies by default**. Playwright
  adds `<-loopback>` by itself today, but `shouldProxyLoopback` in its driver
  (`playwright/driver/package/lib/coreBundle.js:38045`, read in this repo's venv) returns false the
  moment `PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK` is set. **A security boundary must
  not rest on an environment variable someone else reads**, so we set it ourselves. Passing it does
  not duplicate Playwright's — the same helper sees our `<-loopback>` in the bypass list and stops
  adding its own, and the launched process carries exactly one (below).
- **`--force-webrtc-ip-handling-policy` *and* `--webrtc-ip-handling-policy`, both
  `disable_non_proxied_udp`.** WebRTC sends UDP straight to an address a page names, which no HTTP
  proxy carries. Measured with a UDP socket on loopback as the page's STUN server:

  | arm | UDP datagrams to loopback | ICE candidates | gathering state |
  |---|---|---|---|
  | shipped options | 0 | 0 | `complete` |
  | WebRTC switches removed | **5** | 1 | stuck at `gathering` |

  The two switches are the same policy under two names, and **the binary decides which one exists**.
  On Playwright 1.62 / Chromium 151, `strings` over the two builds it ships:

  | build | `force-webrtc-ip-handling-policy` | `webrtc-ip-handling-policy` |
  |---|---|---|
  | `chrome-headless-shell` | present | absent |
  | Chrome for Testing (framework binary) | absent | present |

  Chromium ignores an unknown switch in silence, so passing one is a boundary that reopens with no
  error the day the binary changes. Both are passed.
- **`headless: True`, pinned rather than left to the default.** Playwright's own selector is
  `options.headless ? "chromium-headless-shell" : "chromium"` (`coreBundle.js:43106`). Pinning it
  means the binary that runs is the binary the live suite and the browser image measure — confirmed
  from the running process's argv, which is `…/chromium_headless_shell-1234/chrome-linux/headless_shell`.
- **`--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1`.** Chromium's own resolver answers
  "no such name" for everything but the proxy's address; the `EXCLUDE` is what lets it still find
  the proxy. This is Chromium's documented catch-all and the construction Playwright itself emits
  for socks5 proxies (`coreBundle.js:43082`). **It is honoured by this binary, not silently
  ignored** — with no proxy configured, `http://example.com/` loads without the rule and raises
  `net::ERR_NAME_NOT_RESOLVED` with it. It costs proxied loads nothing, because for an HTTP proxy
  Chromium never asks its own resolver at all: *"name resolution is always deferred to the proxy"*
  (net/docs/proxy.md). The negative control's `clickhouse` row is that in action — the **name**
  reached the proxy, which resolved it to `172.19.0.7` and refused it.

The launched process's own argv, read from `/proc` while it ran, carries exactly one of each:

```
--headless
--proxy-server=http://127.0.0.1:36555
--proxy-bypass-list=<-loopback>
--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1
--force-webrtc-ip-handling-policy=disable_non_proxied_udp
--webrtc-ip-handling-policy=disable_non_proxied_udp
```

### 2a. The ways onto the network that are not `page.goto`

A boundary that holds only for top-level navigation is not a boundary. Behind the shipped options,
in the container, with the loopback service recording HTTP hits and a raw TCP listener recording
connections:

| what the page did | outcome | reached loopback |
|---|---|---|
| `new WebSocket('ws://127.0.0.1:P/')` | `error` | no connection |
| `new WebSocket('ws://127.0.0.1.nip.io:P/')` | `error` | no connection |
| `fetch('http://127.0.0.1:P/…')` | `Failed to fetch` | no hit |
| `fetch('http://127.0.0.1.nip.io:P/…')` | `Failed to fetch` | no hit |
| `<img src="http://127.0.0.1:P/…">` | `error` | no hit |

And four spellings of loopback that a name-based check would miss, each run through the whole probe
and each refused with no hit: `http://2130706433:P/` (decimal) and `http://0177.0.0.1:P/` (octal)
both refused at `127.0.0.1`; `http://[::1]:P/` at `::1`; `http://[::ffff:127.0.0.1]:P/` at
`::ffff:127.0.0.1`. That last one matters because `ipaddress` does not report an IPv4-mapped
address as loopback — it is `is_global` that refuses it, which is why `is_publicly_routable`
insists on both tests.

Both schemes are covered, and they fail differently, which is why both are tested live. Plain HTTP:
the refusal comes back as a **403 page**, so `goto` succeeds and only the proxy's own word fails the
step. HTTPS: the browser asks for `CONNECT host:port`, the proxy resolves and judges *before*
opening the tunnel, and Chromium raises `ERR_TUNNEL_CONNECTION_FAILED` — which reads as the site
being down, so the refusal replaces that message.

### 3. Fail closed, in both directions

- **A proxy that cannot listen stops the flow.** `_drive` enters `with PublicAddressProxy() as
  proxy:` *before* importing Playwright, so no browser can exist without its boundary. Watched in
  the container with `start` forced to raise: `perform` raised `OSError: cannot listen`, and the
  loopback service recorded no hit.
- **A proxy that is not answering fails the navigation.** Chromium launched as the probe launches
  it, with nothing listening at the proxy address. Every target failed and none went direct:

  | target | outcome | reached loopback |
  |---|---|---|
  | `http://127.0.0.1:P/` | `net::ERR_PROXY_CONNECTION_FAILED` | no hit |
  | `http://127.0.0.1.nip.io:P/` | `net::ERR_PROXY_CONNECTION_FAILED` | no hit |
  | `http://clickhouse:8123/ping` | `net::ERR_PROXY_CONNECTION_FAILED` | — |
  | `https://example.com/` | `net::ERR_PROXY_CONNECTION_FAILED` | — |

  The first row is the one that matters: a *literal* loopback URL is what Chromium would have sent
  direct if `<-loopback>` were dropped, and with a dead proxy it fails instead of loading. The last
  row is the control — `example.com` loads fine from this container when the proxy is alive.

**Raising rather than publishing a failed flow is deliberate.** `http_check.perform`'s contract is
the precedent: it "raises only if pycurl itself cannot be configured, which is a bug here rather
than news about the target." A proxy that cannot bind is the same kind of news. `_config_failure`
publishes a failed flow because a bad *config* is the customer's to see; a broken worker is not, and
publishing "your site is down" for it would be a false measurement. The cost is recorded below.

### 4. A refusal is reported only when it cost the request something

`_open_checked` records a refused candidate **only on the path where nothing answered.** A name
resolving to one private and one public address connects to the public one, never touches the
private one, and is reported as the ordinary success it is.

The rebinding window this closes was measured directly, by instrumenting the proxy's own resolver
inside the container so that it answers publicly once and then privately for ever after — the
attack. `_open_checked` asked **once**, saw `104.20.23.154`, and connected to `104.20.23.154`: the
exact tuple it judged. The poisoned second answer was never requested, because nothing asks twice.
The mirror case, private answered first, resolved once and connected to **nothing at all** — 403,
`refusals: ["169.254.169.254"]`.

This is the existing rule, not a new one. `http_check.perform` reads `guard.refusal_reason()` only
inside `except pycurl.error` — a successful HTTP check ignores refused candidates entirely. Recording
them on the success path made **the same target pass as an HTTP check and fail as a browser check**,
and split-horizon DNS leaking an internal A record publicly is an ordinary customer misconfiguration,
not an attempt on our network.

### 5. One request per client connection, enforced rather than asserted

The proxy judges an address per *connection*. A second request arriving on that connection — for a
different host — would be relayed to the first host's origin. Chromium does not pipeline, so this
was never reachable, but `Connection: close` to the *origin* does not stop a *client* doing it, and
the comment claiming it did was wrong.

So the head now carries its own bound: `body_bytes` from `Content-Length`, and exactly that many
bytes are forwarded before the proxy stops reading the client. A request the proxy cannot bound is
refused with 400 rather than relayed blind — `Transfer-Encoding` (whose framing this proxy does not
parse) and two disagreeing `Content-Length` values (request smuggling's own shape).

### 5a. A head the proxy cannot read is refused, on both branches

`parse_request`'s contract is that it reads a head or refuses to act on it, and `_handle` catches
that refusal and answers `400`. Three inputs broke it, and they were found by asking the parser
directly rather than by reading it:

```
GET http://example.com:abc/    -> ValueError: Port could not be cast to integer value as 'abc'
GET http://example.com:99999/  -> ValueError: Port out of range 0-65535
GET http://[::1/               -> ValueError: Invalid IPv6 URL
```

`urlsplit` and `SplitResult.port` raise a bare `ValueError`, which nothing catches. Watched:
`ERROR asyncio: Unhandled exception in client_connected_cb`, and the client got a dropped
connection instead of an answer. The same input was refused cleanly as a `CONNECT` — because that
branch parses the authority by hand — and leaked as a `GET`. One rule for both schemes now:
`_absolute_form` turns any parse failure into `MalformedRequestError`, and `_port_in_range` is the
single place that says what a port is, used by both branches.

**That range check is not the port *policy* question**, which stays open below. `MIN_PORT`/
`MAX_PORT` say only which numbers are ports at all; nothing here says which of them a flow may
reach. No connection was ever made on this path, so this is a contract and robustness defect rather
than a hole in the boundary — but a proxy that drops a connection instead of answering is a proxy
whose failures cannot be read.

### 6. Compose, per file

The boundary is application code and needs no compose change to work. What each file needed:

- **`docker-compose.yaml`** — one fix, and it is why this matters at all. The dev browser worker
  mounted the host source at `/app/src`, a path nothing reads: `Dockerfile.browser`'s `WORKDIR` is
  `/app` and the source lands at `/app/apps/scheduler`. So that worker ran its **build-time copy**
  however the working tree changed — you could edit the boundary and the container would not have
  it. The mount now targets `/app/apps/scheduler/src`. **Confirmed in the running container:**
  `nexovue_scheduler.__file__` is `/app/apps/scheduler/src/nexovue_scheduler/__init__.py`, the
  mounted-only `browser_proxy.py` imports, `sys.path` is
  `[…/site-packages, /app/packages/contracts/gen/python, /app/apps/scheduler/src]`, and `/app/src`
  does not exist. Every measurement in this ADR was then taken through that mount.
- **`docker-compose.app.yaml`** — no change. It builds `Dockerfile.browser` from the tree, so it
  gets the boundary from the image. Worth knowing: `scheduler-browser` has **no profile** here, so
  it is in the default service set — `just dev-app` starts a browser worker, unlike `just dev`.
  Until this change, that meant the production replica ran browser checks with no egress boundary.
- **`docker-compose.onprem.yaml`** — a `TODO(browser-image)` added, and the defect it names left
  unfixed. `scheduler-browser` is given `image: *image-base`, the single `${NEXOVUE_REGISTRY}/nexovue`
  image built from `apps/scheduler/Dockerfile`, which deliberately carries no Playwright and no
  Chromium — confirmed: `docker run --rm --entrypoint python nexovue/scheduler:dev -c "import
  playwright"` gives `ModuleNotFoundError: No module named 'playwright'`. That worker starts, takes
  a browser check and fails on the import. It needs its own image and the registry path for one is
  the file's existing `TODO(registry)`, so the fix is a decision, not an edit. It is now marked in
  the file, which it was not: a service that cannot possibly work read as one that could.

All three files were re-validated with `docker compose config -q` after the edits.

**No `networks:` block was added.** Segmenting the compose network is a real improvement and it is
not this ADR's: it would not have closed this hole (§ Alternatives), and doing it properly means
deciding which of eleven services may speak to which, which is a separate change with its own
blast radius.

## Alternatives considered

**Network isolation — put the browser worker on its own network with no route to our services.**
Rejected, and it is worth being precise about why, because it is the first thing anyone suggests.
The browser worker is a **Celery worker**: it needs Redis to receive jobs and the control plane's
Postgres for check config. Those are two of the four services being protected. An isolation that
lets it reach exactly the things worth stealing is not an isolation. It would also have done nothing
about `169.254.169.254`, which is not on any Docker network, and nothing about a customer's own
internal address reachable from our egress IP. It is a good hardening for the *other* containers and
belongs in its own change.

**Re-resolving the name in Python and passing the address to Chromium.** Rejected — it is the
rebinding hole with extra steps. Chromium would resolve again on its own and could get a different
answer, which is exactly what `addresses.py`'s docstring exists to say.

**A Playwright `route` handler or CDP request interception.** Rejected. Both judge a *URL*, not an
address, so they cannot see what the name resolves to at connect time; they can be turned off by
page-level code paths that bypass the network stack's interception (workers, prefetch); and they
would be a second boundary with a second rule to keep in step with `is_publicly_routable`. One rule,
tested once, was the point.

**A SOCKS5 proxy instead of HTTP.** Rejected. It buys nothing here — an HTTP proxy already defers
name resolution to the proxy for every `http`, `https`, `ws` and `wss` URL, per Chromium's own
documentation — and it costs the plain-HTTP 403 page, which is what makes a refusal legible to a
customer reading their own check.

**A third-party proxy (Squid, tinyproxy, mitmproxy).** Rejected. The rule that must be applied is
`is_publicly_routable` — two named-flag tests *and* `is_global`, because neither is a superset of
the other (multicast, NAT64 wrapping a v4 private address, carrier-grade NAT at 100.64/10 carrying
Alibaba's metadata endpoint). Expressing that as an ACL in someone else's config language is a
second copy of the rule that drifts, plus a container to run and patch, to replace ~400 lines that
call the function directly.

**Refusing a name that answers with any private address, even when a public one also answers.**
Rejected, on consistency: `http_check` does not, so the same target would pass one check type and
fail the other. See § 4, and the open question below.

## Consequences

**What this buys.** A browser flow cannot reach an address the public internet cannot reach,
however the name it uses resolves and however many hops the navigation takes. One rule, one
function, shared with the HTTP check: `ConnectGuard.refusal_reason`'s body was **moved into**
`addresses.refusal_reason_for` and deleted from its old home, so a refusal reads identically whether
libcurl or Chromium was asking.

**What it costs.**

- **A proxy that cannot bind makes the check go silent, not down.** `perform` raises, nothing is
  published, and `max_retries=2` does not apply — that covers only the explicit `self.retry` inside
  `_publish_or_retry`. This codebase's own words are that "a measurement that never reached the
  broker is indistinguishable from a check that never ran". The argument for it is § 3; it remains a
  real cost and it is the one thing here most worth revisiting.
- **Every byte of every browser flow now goes through a Python asyncio relay.** Measured, in the
  container, five runs each of launch-plus-`goto` against `https://example.com/`: direct median
  **0.376s** (`0.586, 0.369, 0.405, 0.376, 0.350`), through the proxy median **0.425s** (`0.495,
  0.460, 0.414, 0.418, 0.425`). About **50ms**, which includes starting and stopping the proxy per
  flow. Browser checks run at one per worker on a 1-per-minute cadence, so it does not matter here.
  It is not a throughput measurement: no flow with a large body or many subresources was timed.
- **A request body the proxy cannot bound is refused.** A flow whose page uploads with
  `Transfer-Encoding: chunked` — a streaming `fetch`, which a synthetic flow has no reason to do —
  gets a 400 it would previously have got a response to.
- **Refusals are attributed to a step only approximately.** Chromium fetches subresources
  asynchronously, so a refusal caused by step N can land while step N+1 is running and be reported
  against it. `take_refusals`'s docstring used to claim more than that and now does not. What is
  exact is that the connection was refused before it was opened.
- **The proxy will tunnel to any port on any public address.** No worse than before — the browser
  could reach anything, public or not — and the same as the HTTP check, which sets no port bounds
  either. But it does make our prober a usable port scanner and connection relay against third
  parties. Open question below.
- **One live test depends on `httpbin.org`.** `test_a_redirect_to_a_private_address_is_refused`
  needs a *public* URL that redirects to a private one, and a redirect served from loopback would be
  refused at the first hop. When httpbin is down the suite goes red for a reason that has nothing to
  do with the boundary. It passed in every run here (1.39s).

**What was verified, and watched.**

Everything below was run and its output read. Where a number appears it was read from that output,
not from an exit code.

*Against the running dev stack, inside `nexovue-scheduler-browser-1`:*

- **The premise.** From that container, `postgres:5432`, `clickhouse:8123`, `kafka:9092`,
  `redis:6379`, `api:8000` and `seaweedfs:9333` are all REACHABLE on `nexovue_default`; there is no
  `networks:` block in any compose file. `127.0.0.1.nip.io` resolves to `127.0.0.1` and
  `169.254.169.254.nip.io` to `169.254.169.254` from inside it.
- **The negative control**, both arms, six targets — the table in Context. The ClickHouse arm read
  `Ok.` from our own database without the boundary and was refused at `172.19.0.7` with it.
- **Fail closed**, both directions — the tables in § 3.
- **The rebinding window**, by instrumenting the resolver: one resolution per request, connected to
  the judged tuple — § 4.
- **`--host-resolver-rules` is honoured, not ignored**: without a proxy, `http://example.com/` loads
  without the rule and gives `net::ERR_NAME_NOT_RESOLVED` with it.
- **The WebRTC switches hold five UDP datagrams**, and the escape hatches that are not `page.goto`
  hold — § 2 and § 2a.
- **The launched process's argv**, from `/proc` while it ran — § 2.
- **The whole production path.** Two real Celery tasks dispatched to `run_flow` on the `browser`
  queue and run by the worker: `up=False steps_passed=0 steps_total=1 failed_step=goto` for
  `http://127.0.0.1.nip.io:8123/ping`, and `up=True steps_passed=1` for `https://example.com/`. The
  envelope was then read off Kafka's `logs-raw` topic carrying `error.type` `EgressRefusedError` and
  `step 'goto' failed: Target resolved to a non-public address and was not contacted: 127.0.0.1`,
  and the worker landed it in ClickHouse — `nexovue.logs` held exactly three rows for that tenant:

  ```
  INFO   nexovue-prober  browser flow finished: 0 of 1 steps passed
  INFO   nexovue-prober  browser flow finished: 1 of 1 steps passed
  ERROR  nexovue-prober  step 'goto' failed: Target resolved to a non-public address and was
                         not contacted: 127.0.0.1
  ```

  The boundary is legible all the way to storage, not only in a test. (Those three rows were deleted
  afterwards; the table was empty before.)
- **The compose mount fix** — § 6.
- **Cost** — five runs each way, § Consequences.

*On the host, with the repo's venv (Python 3.13.15; the container runs 3.14, and both were exercised):*

- **The creation-time acceptance that makes this reachable**, re-run: `ACCEPTED
  http://127.0.0.1.nip.io:5432/`, `ACCEPTED http://169.254.169.254.nip.io/`, and a step URL of
  `http://127.0.0.1.nip.io:8123/` accepted by `validate_step_urls`; `REFUSED` for the three literal
  and internal-name forms.
- **The two Playwright claims, read in the driver bundle**, not recalled: `shouldProxyLoopback`
  (`coreBundle.js:38045`) returns false on `PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK` and
  skips its own `<-loopback>` when ours is in the bypass list; `getExecutableName` is
  `options.headless ? "chromium-headless-shell" : "chromium"`.
- **The two-spellings claim, by `strings` over both shipped binaries** — the table in § 2. The
  Chrome for Testing switch lives in the framework binary, not the 52KB launcher stub.
- **Six new tests watched failing before the fix that makes them pass** (§ 5a), including the
  `ERROR asyncio: Unhandled exception in client_connected_cb` traceback that showed the connection
  being dropped rather than answered.
- **Counts read from the output.** `test_browser_proxy.py` **43 passed** (37 before the § 5a fix);
  `test_browser_flow.py` **19 passed**; `test_browser_egress_live.py` **7 passed in 5.25s** —
  durations that show real browser launches and real network; `test_connect_guard.py` still
  collects **27**. Whole scheduler suite **186 passed in 19.74s**, against 180 before the § 5a fix
  and **117** before this work — 117 + 69 new (43 + 19 + 7) = 186 exactly. An earlier draft of this
  line said 169, which was never a count this suite produced; it is corrected here because this is
  the section that promises every number was read from the command's own output. `mypy apps/scheduler`: success, **47 source files**. `ruff check` and
  `ruff format --check` on `apps/scheduler`: clean, 49 files.

**What an earlier draft of this ADR claimed that this pass did not reproduce.** The WebRTC arm with
the switches removed gathered **1** ICE candidate here, not 2. The datagram count (5), the states
and every other figure held.

**What was still not verified.**

- **No live DNS server was made to rebind.** § 4's proof instruments the proxy's own resolver, which
  establishes the property that matters — one resolution, connect to the judged tuple — but the
  resolver was ours, not a real authoritative server changing its answer between two queries. A
  server that could would need the container's `/etc/resolv.conf` pointed at it; it was not built.
  Nothing in the design depends on a second query, which is why this was judged sufficient.
- **`--host-resolver-rules` is proven honoured, not proven load-bearing.** Removing it changed no
  outcome in any arm, because with an HTTP proxy Chromium never asks its own resolver. It is kept as
  defence in depth: a resolver that cannot answer cannot leak an answer if that ever changes.
- **`is_publicly_routable` itself is unchanged and inherits its own coverage** from
  `test_connect_guard.py` (27 tests). This ADR adds a second caller, not a second rule.
- **IPv6-only origins were never exercised.** The candidate loop handles them, the parse and the
  `::1` / `::ffff:127.0.0.1` refusals are covered, but no test reaches a v6-only *public* host.
- **The relay was not load-tested.** The 50ms figure is one small page, five times.
- **A refusal landing after the final step's drain is still missed.** `_judge_after_the_last_step`
  drains once, immediately, with no settle time; a subresource refused 100ms later is not reported
  and the flow can read `up`. The connection was still never made — this is a reporting gap, not a
  boundary gap — but it is a real one and it was not closed.
- **A flow with an empty `steps: []` reports `up` with zero steps.** Pre-existing; `parse_steps`
  returns nothing to run and nothing enforces a minimum. Unchanged by this work, noted here because
  this pass is where it was noticed.

## Still open, and not decided here

- **A proxy that cannot bind: raise, or publish a failed flow?** § 3 keeps `http_check`'s precedent.
  The counter-argument — that an unpublished result is this system's worst outcome in its own words
  — is good enough that it should be Raj's call, not an inherited default.
- **Port *policy* on `CONNECT`.** § 5a bounds a port to 1–65535, which is only what a port is. Which
  of those a flow may reach is undecided: the proxy will tunnel to any port on any public address,
  which matches the HTTP check but makes our prober a usable port scanner against third parties.
- **The `httpbin.org` dependency in the live suite.** The honest options are a public redirector we
  control or dropping the redirect test; both are decisions, not fixes.
- **`pollers/postgres_connect.py` is the same root, untouched.** `connect_read_only` calls
  `asyncpg.connect(host=target.host, port=target.port, …)` with no address check and no port bounds,
  and the host comes straight from a tenant's integration `config` JSONB
  (`pollers/integrations.py:189` checks only that it is a string; the API stores the blob
  unvalidated — `integrations/schemas.py:26` is a bare `config: dict[str, Any]`). A tenant can
  point a Postgres integration at `clickhouse:8123` or
  `169.254.169.254:80`; `ssl="require"` bounds what can be read back, not what can be connected to,
  and the 10s `CONNECT_TIMEOUT_SECONDS` makes the error and the timing a port-scan oracle inside our
  network. **It needs a different mechanism from this proxy** — asyncpg resolves the name itself, so
  a check before `connect` reopens the rebinding window unless the connection is made to the judged
  literal, which then collides with TLS certificate verification against the hostname. That is real
  design work, not a one-liner, and it is not attempted here.

## Should browser checks be exposed to customers?

They already are, and that is the finding. There is no feature flag between a customer and a browser
check: `CheckType = Literal["http", "browser"]` accepts one at creation, `beat.py` routes
`check.type == "browser"` to `BROWSER_TASK`, `celery_app.py` routes that name to the `browser`
queue, and the worker runs it. The only gate is whether a `scheduler-browser` container is running,
and in `docker-compose.app.yaml` it has no profile, so it is in the default set. The comment on
`CheckType` says browser is *"accepted here before the browser queue runs it"* — the code disagrees
with the comment, and the code wins.

So the recommendation is per deployment, not per feature:

- **Dev and the production replica** — safe once this change is committed. It is proven here against
  the real container, and before it, a browser check pointed at `127.0.0.1.nip.io` read our own
  ClickHouse.
- **Any deployment running the pre-change code with a browser worker up** — that is a live
  request-forgery primitive and should have browser checks refused at creation until it is updated.
- **On-prem** — browser checks cannot run at all (§ 6), so exposing the check type there offers a
  customer a check that will only ever fail. That is a product decision, not this ADR's.

## Edits this change owes, outside the directories it was scoped to

- **`docs/architecture/system-architecture.md` §6** needs a decisions-log row for this ADR.
- **`apps/api/src/nexovue_api/checks/targets.py`**'s docstring still says *"The boundary is
  `nexovue_scheduler.probes.addresses`"*, singular. There are two now, and that file is the one a
  reader goes to first.
- **`apps/api/src/nexovue_api/checks/schemas.py`**'s comment on `CheckType` says browser checks are
  accepted "before the browser queue runs it". The queue runs it; see the section above.
- **`docs/delivery/phase-1-plan.md`**'s P1-53 row still lists this as one of three findings
  outstanding.
