# Nothing has arrived

Work through this in order. Each step splits the problem roughly in half, and
the first one is free.

## Step 1: has anything ever authenticated with your key?

**Settings → API keys.** Every key shows what its traffic looks like:

| What it says | What it means |
|---|---|
| **Never used** | Nothing has ever successfully authenticated with this key |
| **In use** | Something authenticated within the last hour |
| **Going quiet** | Nothing for a while, not long enough to be sure |
| **No traffic** | Nothing for over a day |

This one reading splits the whole problem.

**"Never used"** means no request has ever reached nexovue carrying a valid key.
Nothing downstream — sampling, storage, the console — can be the problem, because
nothing got in. Go to step 2.

**Anything else** means requests are arriving and authenticating. The problem is
past the door: what you are sending, or where you are looking for it. Skip to
step 6.

A caveat on the timestamp: it is deliberately coarse. The receiver records a use
only when it re-reads the key from the control plane, at most once every 30
seconds, so a key in constant use reads as "seconds ago" and never as "now".
That does not affect the four readings above.

## Step 2: can you reach the endpoint at all?

From the machine the service runs on — inside the container, inside the pod, on
the EC2 instance. Not from your laptop: your laptop's network is not the one
that matters.

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X GET https://ingest.nexovue.com/v1/traces
```

| Answer | Meaning |
|---|---|
| `405` | You reached the receiver. The path exists and takes POST only. This is the good answer |
| `404` | You reached the host but not an OTLP path. Your endpoint has the wrong path on it |
| Timeout, connection refused | Network. Step 3 |
| DNS failure | Network. Step 3 |
| TLS error | Step 3, last section |

`405 Method Not Allowed` is the result you want here. It proves the hostname
resolves, the network allows the connection, TLS completes, and the path is one
the receiver serves.

## Step 3: the network between you and nexovue

The connection is outbound HTTPS on 443. It fails for exactly the reasons any
other outbound HTTPS call fails, and all of them look the same from inside your
service: silence.

**Egress rules.** A Kubernetes NetworkPolicy with default-deny egress needs an
explicit allow for the ingest host on 443, and for DNS on 53. An AWS security
group needs an outbound rule. A task in a private subnet needs a NAT gateway or
a VPC endpoint.

**A corporate proxy.** If the host reaches the internet only through a proxy, the
exporter has to know. Whether an OpenTelemetry SDK honours `HTTPS_PROXY` varies
by language — Go's HTTP client does by default, Node's does not — so this is one
to test rather than assume. A Collector on the host, configured with the proxy,
is the reliable answer when the SDK will not cooperate.

**TLS interception.** A corporate middlebox that terminates and re-signs TLS
presents a certificate your runtime does not trust, and the export fails with a
certificate error. The fix is adding your organisation's CA to the runtime's
trust store, not disabling verification.

**Split-horizon DNS.** An internal resolver that does not know the public name
answers NXDOMAIN. The `curl` in step 2 catches this one immediately.

## Step 4: send one span by hand

This is the test that separates your instrumentation from everything else. If
this works and your service does not, the problem is in your service's
configuration, and nothing about nexovue is involved.

```bash
NEXOVUE_ENDPOINT=https://ingest.nexovue.com
NEXOVUE_KEY=nvk_live_xxxxxxxxxxxxxxxxxxxxxx

START=$(( $(date +%s) * 1000000000 ))
END=$(( START + 1000000 ))

curl -i -X POST "$NEXOVUE_ENDPOINT/v1/traces" \
  -H "content-type: application/json" \
  -H "x-nexovue-key: $NEXOVUE_KEY" \
  -d "{
    \"resourceSpans\": [{
      \"resource\": {\"attributes\": [
        {\"key\": \"service.name\", \"value\": {\"stringValue\": \"curl-check\"}}
      ]},
      \"scopeSpans\": [{
        \"spans\": [{
          \"traceId\": \"$(openssl rand -hex 16)\",
          \"spanId\": \"$(openssl rand -hex 8)\",
          \"name\": \"curl-check\",
          \"kind\": 1,
          \"startTimeUnixNano\": \"$START\",
          \"endTimeUnixNano\": \"$END\",
          \"status\": {\"code\": 2}
        }]
      }]
    }]
  }"
```

**A successful export answers `HTTP/1.1 200` with the body `{}`.** An empty
response body is OTLP's way of saying every item was accepted.

Two details in that request are deliberate.

`"status": {"code": 2}` marks the span as errored. nexovue keeps every errored
trace regardless of sampling, so this one is guaranteed to be in storage rather
than merely accepted. Without it, a fast healthy trace from a project that has
already sent its first hundred has a nine-in-ten chance of being sampled out —
which would look exactly like a failure and would not be one.

`-i` prints the response headers. On a refusal, `Retry-After` is the difference
between "wait and it will work" and "waiting will not help".

### What a refusal tells you

Every failure body is a `google.rpc.Status` — OTLP's own error shape — with a
`message` written to be read by a person:

```json
{"code":16, "message":"invalid or missing x-nexovue-key credential"}
```

| Status | Message | What to do |
|---|---|---|
| `401` | `invalid or missing x-nexovue-key credential` | Step 5 |
| `403` | `this ingest key cannot be used from a browser; a public site key is what a web page sends` | A secret key was sent from a web page. Use a site key — [browser.md](browser.md) |
| `403` | `this ingest key is not allowed from this origin` | Browser door. Add the origin to the site key |
| `403` | `public ingest key presented without a browser Origin` | Browser door. A site key was used by something that is not a browser |
| `400` | `request is not a valid ExportTraceServiceRequest` | The body is not an OTLP trace export |
| `413` | `request body is larger than the configured limit` | Over 4 MiB compressed or 16 MiB decompressed. Reduce the batch size |
| `415` | `unsupported content type` | Use `application/x-protobuf` or `application/json` |
| `415` | `unsupported content encoding` | `gzip` or nothing. No other compression |
| `429` | `ingest rate limit exceeded for this key` | Over 1000 requests a second on this key. Carries `Retry-After`; back off |
| `429` | `the included monthly ingest volume for this plan is spent; ingestion resumes on …` | No `Retry-After`, because waiting until that date is the only thing that helps |
| `429` | `the included monthly browser ingest volume …` | The browser door's own share, not your whole plan |
| `503` | `ingestion buffer unavailable, retry` | Our side. Retry; `Retry-After` says when |

One exception to the shape: a `415` for an unsupported content type is answered
in protobuf rather than in the encoding you sent, because the encoding was never
resolved. A binary body on a `415` is that, not a second problem.

## Step 5: the key itself

A `401` is deliberately the same answer whether the key is missing, malformed or
unknown — telling an unauthenticated caller which would only help someone
guessing keys. So check all three yourself:

**It is the right shape.** `nvk_live_` followed by 22 letters and digits, 31
characters in total. A truncated copy-paste is the most common cause.

A key beginning `nvp_live_` is a site key and belongs in a page, not a server.
The server door refuses it on its format, before any lookup, so the answer is
the same generic `401` — there is nothing in the response to tell you that is
what happened. Check the first four characters of what you pasted.

**It is not revoked.** Revoked keys stop working within a minute and show as
withdrawn in the console. There is no un-revoke: mint a new one.

**The header is spelled right, and the variable is shaped right.** The header is
`x-nexovue-key`. The environment variable is not the key on its own:

```
# wrong — the variable holds name=value pairs, not a bare value
OTEL_EXPORTER_OTLP_HEADERS=nvk_live_xxxxxxxxxxxxxxxxxxxxxx

# right
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
```

**The variable actually reached the process.** This is the one that wastes the
most time. `docker exec <container> env | grep OTEL`, or
`systemctl show <unit> --property=Environment`, or the pod's `env` — look at what
the process has, not at what the config file says. A Kubernetes `$(NEXOVUE_KEY)`
reference that resolved to a literal string, a Compose `.env` that was not found,
an ECS secret that did not get assembled into a header: all three look exactly
like a bad key.

**Keys do not expire.** There is no expiry date and nothing times out. If a key
that worked stops working, it was revoked, or its project was deleted.

## Step 6: it authenticates, but you cannot find the data

You are past the door. Four things account for nearly all of it.

### The endpoint has a path on it

```
# wrong — the SDK appends /v1/traces itself, giving /v1/traces/v1/traces
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com/v1/traces

# right
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
```

If you must name a full path, the per-signal variables take one and do not
append: `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://ingest.nexovue.com/v1/traces`.
Use one form or the other, not both.

### `service.name` is not set

nexovue groups everything by `service.name` — the service list, the service map,
the per-service charts and your usage. A resource that does not carry one is
grouped as **`(unknown)`**, and every unnamed service you run lands in that one
row together.

So the data may well be there and you are looking for the wrong name. Check for
`(unknown)` in the service list before concluding anything is missing.

Set `OTEL_SERVICE_NAME`, per service. In Go, also check that your resource is
built with `resource.WithFromEnv()` — without it the variable is read by nobody.

### Sampling

nexovue samples for storage, after a trace is complete:

- every trace containing an error is kept
- every trace slower than 2 seconds is kept
- one healthy trace in ten is kept
- the first 100 traces a project ever sends are kept whatever the rest says

A fast, healthy, successful trace from an established project has a nine-in-ten
chance of not being in storage. That is working as intended, and it is why the
`curl` in step 4 marks its span as errored.

**Your counts are not affected.** Request rates, error rates and latency
percentiles are computed at ingest, from every trace, before anything is
discarded. The numbers on every screen are exact even though most individual
traces are not kept.

**Unless you sample upstream.** If your SDK has a head sampler, or your Collector
has a `tail_sampling` or `probabilistic_sampler` processor, you are discarding
traces before nexovue counts them — and then every rate in the console is wrong
by exactly your sampling factor, with nothing anywhere to say so. Turn off
upstream sampling and let nexovue decide. See [collector.md](collector.md).

### The clock

If your data appears in nexovue at the wrong time — bunched at "now" rather than
spread across the period it actually covers — the sending host's clock is wrong.

nexovue trusts a timestamp that is within **one minute in the future** and
within **seven days in the past** of when the export arrived. Outside that, the
data is attributed to the moment it was received instead.

Nothing is rejected and nothing is lost. The trade is deliberate: a timestamp far
in the future would otherwise hold a time bucket open indefinitely, and one far
in the past would land outside retention and be deleted. Re-attributing is the
only option that keeps the data.

What this looks like when it happens: a host whose clock drifted by an hour
reports everything as having happened at the moment it was received, so the
latency and rate charts are right in shape and wrong in position. Fix the clock —
NTP, or `chrony` — and it corrects itself for new data. Data already stored keeps
the time it was given.

A container inheriting a drifted host clock is the usual cause. So is a VM
resumed from a suspend.

## Step 7: still nothing

Collect these before asking, because they are what any answer will start from:

- what the `curl` in step 4 returned, headers included
- what the key's row says in Settings → API keys
- your `OTEL_*` variables as the process actually has them, with the key redacted
- whether a Collector is in the path, and if so what
  `otelcol_receiver_accepted_spans` and `otelcol_exporter_sent_spans` say

The last one is the fastest split when a Collector is involved: received going up
and sent staying flat is a nexovue-side or network problem; both flat means your
services are not reaching the Collector, and nothing past it matters yet.

## Failures that do not look like failures

Four things that produce silence rather than an error, worth knowing so you
recognise them:

**A container pointed at `localhost`.** Inside a container, `localhost` is the
container. An exporter aimed at `http://localhost:4318` when nexovue is on the
host connects to nothing and reports nothing useful. See
[platforms/docker.md](platforms/docker.md).

**A process that exits before flushing.** An SDK holds spans for a few seconds
before sending. A CLI, a job, or a service killed on SIGTERM without shutting the
tracer provider down loses whatever it was holding. This is why you see traces
from a long-running service and none from a cron job.

**A forking web server.** Gunicorn and uWSGI initialise in the parent and fork
workers; the exporter's background thread does not survive the fork. See
[languages/python.md](languages/python.md).

**gRPC against the HTTP endpoint.** An exporter configured for OTLP/gRPC,
pointed at the HTTP endpoint, fails in a way that names neither the protocol nor
the port. Set `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`. nexovue does not
publish a gRPC port on the hosted endpoint today.
