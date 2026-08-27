// Package telemetry wires OpenTelemetry the way a customer would.
//
// There is nothing nexovue-specific in here. The exporter is configured purely
// by the standard OTEL_* environment variables — the same two lines our
// onboarding asks a customer to set:
//
//	OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:14318
//	OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_...
//
// That is the point of this app existing: the path we sell is the path we walk
// every day, so an awkward setup is felt here first.
package telemetry

import (
	"context"
	"fmt"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.43.0"
)

// shutdownTimeout gives the exporter a chance to flush on the way out, so the
// last few spans of a run are not lost.
const shutdownTimeout = 5 * time.Second

// Start installs the global tracer provider and returns its shutdown function.
func Start(ctx context.Context, serviceName, version string) (func(context.Context) error, error) {
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, fmt.Errorf("otlp exporter: %w", err)
	}

	// The semconv version must match the schema `resource.Default()` carries, or
	// Merge refuses with a conflicting-schema error. Bumping the SDK means
	// bumping this import in the same commit.
	res, err := resource.Merge(resource.Default(), resource.NewWithAttributes(
		semconv.SchemaURL,
		semconv.ServiceName(serviceName),
		semconv.ServiceVersion(version),
	))
	if err != nil {
		return nil, fmt.Errorf("resource: %w", err)
	}

	provider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(res),
		// The demo exists to produce traces, so it samples all of them. A real
		// service would leave this to the SDK default and let our tail sampling
		// decide what to keep.
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
	)
	otel.SetTracerProvider(provider)

	// W3C trace context is what carries a trace across the HTTP calls between
	// the three roles. Without it each service would start its own trace and
	// the waterfall would never show more than one hop.
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{},
	))

	return func(ctx context.Context) error {
		ctx, cancel := context.WithTimeout(ctx, shutdownTimeout)
		defer cancel()
		return provider.Shutdown(ctx)
	}, nil
}
