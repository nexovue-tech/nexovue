# Go

Go has no zero-code agent. There is no equivalent of Node's `--require` or
Python's `opentelemetry-instrument`: Go is compiled and statically linked, so
there is nothing to patch at load time. Instrumenting a Go service means writing
about twenty lines of setup once, and then wrapping your HTTP handlers and
clients.

That is more work than the other languages on the first day and less work
afterwards, because there is nothing to go wrong at startup.

## Already exporting OpenTelemetry

The OTLP exporter packages read the standard variables themselves, so if your
service builds its exporter with `otlptracehttp.New(ctx)` and no explicit
options, setting these and restarting is the whole change.

```
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
```

If your code passes `otlptracehttp.WithEndpoint(...)` explicitly, that wins over
the environment. Either remove the option or change the literal — the variable
alone will not move anything.

If your exporter is `otlptracegrpc`, change the import to `otlptracehttp`.
nexovue does not publish a gRPC port on the hosted endpoint today.

## Starting from nothing

### 1. Install

```bash
go get go.opentelemetry.io/otel \
       go.opentelemetry.io/otel/sdk \
       go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp \
       go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp
```

### 2. Start a tracer provider

```go
package telemetry

import (
	"context"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

// Start wires tracing from the environment and returns the shutdown to defer.
// Nothing here names nexovue: the exporter reads OTEL_EXPORTER_OTLP_ENDPOINT
// and OTEL_EXPORTER_OTLP_HEADERS, and the resource reads OTEL_SERVICE_NAME.
func Start(ctx context.Context) (func(context.Context) error, error) {
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, err
	}

	attributes, err := resource.New(ctx,
		resource.WithFromEnv(),
		resource.WithTelemetrySDK(),
		resource.WithHost(),
	)
	if err != nil {
		return nil, err
	}

	provider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(attributes),
	)
	otel.SetTracerProvider(provider)

	// Without this, an incoming traceparent header is ignored and every service
	// starts its own trace — so a request crossing three services is three
	// traces instead of one.
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{},
	))

	return provider.Shutdown, nil
}
```

### 3. Call it from `main`, and shut it down

```go
func main() {
	ctx := context.Background()

	shutdown, err := telemetry.Start(ctx)
	if err != nil {
		log.Fatalf("tracing: %v", err)
	}
	// Shutdown flushes. Without it a process that exits promptly sends nothing,
	// because the batch processor is still holding the spans.
	defer func() {
		if err := shutdown(context.Background()); err != nil {
			log.Printf("tracing shutdown: %v", err)
		}
	}()

	// ... serve ...
}
```

### 4. Wrap what you want traced

Inbound:

```go
handler := otelhttp.NewHandler(mux, "api")
http.ListenAndServe(":8080", handler)
```

Outbound, so a call to another service continues the same trace rather than
starting a new one:

```go
client := &http.Client{Transport: otelhttp.NewTransport(http.DefaultTransport)}
```

Pass the request's context through — `http.NewRequestWithContext(ctx, ...)` —
or the transport has nothing to attach the span to.

### 5. Configure it

```
OTEL_SERVICE_NAME=checkout
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
```

`resource.WithFromEnv()` is what reads `OTEL_SERVICE_NAME` and
`OTEL_RESOURCE_ATTRIBUTES`. Leave it out of the resource and the variable does
nothing, and everything you send is grouped under `(unknown)`.

`OTEL_EXPORTER_OTLP_PROTOCOL` is not needed here, because the import chose the
protocol: `otlptracehttp` is HTTP whatever the variable says. Setting it anyway
is harmless and keeps your deployment configuration identical across languages.

## Your own spans

```go
var tracer = otel.Tracer("checkout")

func Settle(ctx context.Context, order Order) error {
	ctx, span := tracer.Start(ctx, "settle_order")
	defer span.End()

	span.SetAttributes(attribute.String("order.id", order.ID))

	if err := chargeCard(ctx, order); err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		return err
	}
	return nil
}
```

`defer span.End()` on the line after `Start`, always. A span that is never ended
is never exported, and the trace it belongs to waits for it.

`SetStatus(codes.Error, ...)` is worth the line: nexovue keeps every errored
trace whatever the sampling rate says, so marking the error is what guarantees
the trace is there when you go looking for it.

## Instrumentation libraries

The contrib repository has wrappers for the common libraries, and they are
ordinary Go packages rather than magic:

| Library | Package under `go.opentelemetry.io/contrib/instrumentation` |
|---|---|
| `net/http` | `net/http/otelhttp` |
| gRPC | `google.golang.org/grpc/otelgrpc` |
| `database/sql` | `github.com/XSAM/otelsql` (outside contrib) |
| AWS SDK v2 | `github.com/aws/aws-sdk-go-v2/otelaws` |

## eBPF auto-instrumentation

There is an experimental project, `go.opentelemetry.io/auto`, that attaches to a
running Go binary with eBPF and emits spans without recompiling it. It needs
elevated privileges and it supports a limited set of libraries.

It is a reasonable thing to try against a binary you cannot rebuild. It is not
the path to start on for a service whose source you control.

## Common mistakes

**Spans are created and never arrive.** The provider was never shut down, or the
process exits on a path that skips the `defer`. Check the shutdown runs.

**Every service shows its own trace.** The propagator was not set. Step 2's
`SetTextMapPropagator` is the fix, and it is needed on the sending and the
receiving side.

**`service.name` is `(unknown)`.** `resource.WithFromEnv()` is missing, or the
resource was built without it and passed to the provider anyway.

**The endpoint variable is ignored.** An explicit `WithEndpoint` option in the
code overrides the environment. Options beat variables, in every OpenTelemetry
SDK.
