# AWS Lambda

Lambda is the one platform where setting the two variables is not enough, and
the reason is worth understanding before you configure anything: **Lambda
freezes the execution environment the moment your handler returns.**

Every OpenTelemetry SDK buffers spans in a background thread and sends them on an
interval — around 5 seconds by default. In a Lambda function that thread is
frozen for the entire gap between invocations, which might be hours, and the
environment is eventually reclaimed without ever thawing. The spans go nowhere,
no error is raised, and the function looks instrumented.

There are two ways round it.

## Option 1: the OpenTelemetry Lambda layer

A layer that attaches the language agent and a Collector extension. The
extension runs as a Lambda extension, which means Lambda gives it a chance to
flush after your handler returns and before the freeze. This is the arrangement
the layer exists to provide, and it is the one to use unless you have a reason
not to.

### Attach the layer

Layer ARNs are per region and per version, and are published in the releases of
`open-telemetry/opentelemetry-lambda` on GitHub. Take the ARN for your region
and runtime from there rather than copying one from any documentation, including
this page — a stale ARN fails at deploy time with an unhelpful message.

### Set the wrapper

```
AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-handler     # Node.js, Java
AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument  # Python
```

`AWS_LAMBDA_EXEC_WRAPPER` is Lambda's own variable for a script that runs before
your handler. The layer provides the script; the variable is how Lambda finds
it.

### Point the Collector at nexovue

The extension's Collector receives on `localhost:4318` and needs a config saying
where to forward. Put a `collector.yaml` at the root of your deployment package:

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: localhost:4318

processors:
  batch:
    timeout: 1s

exporters:
  otlphttp/nexovue:
    endpoint: https://ingest.nexovue.com
    headers:
      x-nexovue-key: ${env:NEXOVUE_KEY}

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/nexovue]
```

and tell the extension where it is:

```
OPENTELEMETRY_COLLECTOR_CONFIG_URI=/var/task/collector.yaml
NEXOVUE_KEY=<from Secrets Manager, see below>
OTEL_SERVICE_NAME=order-webhook
```

`timeout: 1s` on the batch processor rather than the default. A Lambda
invocation is short, and a batch processor holding spans for 5 seconds inside a
200 ms invocation is the original problem in a different container.

> **Check the variable name against the layer version you attach.** The
> Collector extension's configuration variable has been spelled
> `OPENTELEMETRY_COLLECTOR_CONFIG_FILE` in older layer releases and
> `OPENTELEMETRY_COLLECTOR_CONFIG_URI` in newer ones. The release notes for the
> layer you attach say which.

## Option 2: no layer, flush before returning

If you would rather not add a layer, configure the SDK in your own code with a
processor that exports synchronously, or force a flush at the end of every
invocation.

```js
// Node, at the end of the handler
await provider.forceFlush();
```

```python
# Python, at the end of the handler
trace.get_tracer_provider().force_flush()
```

This is simpler to reason about and it costs latency on every invocation: the
handler now waits for an HTTPS round trip to nexovue before returning. For a
function behind an API Gateway serving a user, that is real. For a queue
consumer or a scheduled job, it is usually not.

## The key

Lambda environment variables are visible to anyone with
`lambda:GetFunctionConfiguration`, and they appear in the console and in
CloudFormation output. Read the key from Secrets Manager or Parameter Store at
cold start instead, and cache it outside the handler:

```js
let headers;
async function nexovueHeaders() {
  headers ??= { "x-nexovue-key": await readSecret("/nexovue/ingest-key") };
  return headers;
}
```

Outside the handler, so the secret is read once per execution environment rather
than once per invocation. A secret read on every invocation is a latency cost and
a bill.

## Cold starts

The layer adds to cold start time — the agent has to load and the Collector
extension has to start. How much depends on the runtime and is worth measuring
against your own function rather than assuming.

If cold start latency is the constraint, Option 2 with a manually configured SDK
and only the instrumentation you actually need is lighter than the full
automatic bundle.

## What to expect in nexovue

A Lambda trace usually starts at API Gateway or at the event source rather than
in your function, so the function's span has a parent it did not create. That is
correct and it is what makes the trace show the queue wait or the gateway
latency alongside your own.

Set `OTEL_SERVICE_NAME` per function. `AWS_LAMBDA_FUNCTION_NAME` is not read as a
service name by default, and every unnamed function in your account otherwise
lands in the same `(unknown)` row.
