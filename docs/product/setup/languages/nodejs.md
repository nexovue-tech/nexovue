# Node.js

Node has zero-code instrumentation: a module loaded before your application
patches the libraries you already use — HTTP, Express, Fastify, `pg`, `mysql2`,
`ioredis`, `mongodb`, AWS SDK and many more — and exports spans without a line
of your code changing.

Start there. Write your own spans later, once you can see the automatic ones.

## Already exporting OpenTelemetry

Set the three variables and restart. Nothing else changes.

```
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

## Starting from nothing

### 1. Install

```bash
npm install --save @opentelemetry/api @opentelemetry/auto-instrumentations-node
```

`@opentelemetry/api` is the interface your own code would use to create spans.
`@opentelemetry/auto-instrumentations-node` is the bundle of library
instrumentations plus a register entry point that wires up an SDK for you.

### 2. Load it before your application

```bash
node --require '@opentelemetry/auto-instrumentations-node/register' app.js
```

`--require` matters: the instrumentation patches modules as they are loaded, so
it has to run before your application requires `express` or `pg`. Loading it
from inside `app.js` is too late for anything already imported at the top of the
file.

If your start command is not yours to change — a framework CLI, a process
manager, an image you did not build — use the environment variable instead. It
does the same thing:

```
NODE_OPTIONS=--require @opentelemetry/auto-instrumentations-node/register
```

### 3. Configure it

```
OTEL_SERVICE_NAME=checkout
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

`OTEL_SERVICE_NAME` is the name you will look for in nexovue. Without it every
unnamed service in your estate shares one row called `(unknown)`.

### 4. Restart and look

The onboarding screen waits for your first span and says so when it arrives. If
it does not, [troubleshooting.md](../troubleshooting.md).

## ESM

`--require` works for CommonJS. If your application is ESM — `"type": "module"`,
or `.mjs` files — the loader hook is the equivalent:

```bash
node --import '@opentelemetry/auto-instrumentations-node/register' app.mjs
```

`--import` exists from Node 20.6. On older versions the flag was
`--experimental-loader`, and instrumentation coverage for ESM has historically
been narrower than for CommonJS. If spans for a particular library do not appear
under ESM and do under CommonJS, that is the reason rather than anything at the
nexovue end.

## Your own spans

Once the automatic spans are arriving, add your own where the automatic ones do
not explain enough — a queue consumer, a batch job, a branch you want to see the
cost of.

```js
import { trace } from "@opentelemetry/api";

const tracer = trace.getTracer("checkout");

export async function settle(order) {
  return tracer.startActiveSpan("settle_order", async (span) => {
    try {
      span.setAttribute("order.id", order.id);
      return await chargeCard(order);
    } catch (error) {
      span.recordException(error);
      span.setStatus({ code: 2 }); // ERROR
      throw error;
    } finally {
      span.end();
    }
  });
}
```

Two things to keep in mind. `span.end()` in a `finally` block, because a span
that is never ended is never exported and the trace it belongs to waits for it.
And a span marked with an error status is one nexovue keeps whatever the
sampling rate says, which is the point of marking it.

## Logs

Node's OpenTelemetry logs support is newer than its traces support. If your logs
already go somewhere that works, leaving them there and sending only traces is a
reasonable place to stop — the console's request rates, error rates and
latencies all come from spans.

When you do send logs, the correlation that makes them useful is the trace id,
which the automatic instrumentation puts in context for you.

## Common mistakes

**Nothing is patched.** The register module was loaded after the application.
Check that `--require` or `NODE_OPTIONS` is on the command that actually starts
Node, not inside a script it calls.

**Spans stop at process exit.** A short-lived script exits before the batch
processor flushes. For a CLI or a job, flush explicitly before exiting rather
than relying on the batch interval.

**A bundler removed the instrumentation.** Bundling a server application with
esbuild or webpack breaks module patching, because the modules being patched no
longer exist by name at runtime. Either do not bundle the server, or use the
instrumentation libraries directly rather than the automatic bundle.
