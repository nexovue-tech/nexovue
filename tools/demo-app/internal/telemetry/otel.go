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
	"errors"
	"fmt"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploghttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	logglobal "go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/propagation"
	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.43.0"
)

// shutdownTimeout gives the exporter a chance to flush on the way out, so the
// last few spans of a run are not lost.
const shutdownTimeout = 5 * time.Second

// metricInterval is how often the meter provider exports. Shorter than the SDK
// default so a demo run shows a metric without waiting a minute for it.
const metricInterval = 15 * time.Second

// Start installs the global trace, log and meter providers and returns one
// shutdown function that flushes all three.
//
// All three read the same OTEL_EXPORTER_OTLP_* variables, so a customer
// configures one endpoint and one key and gets every signal — which is the
// claim onboarding makes, and this app is where it is walked.
func Start(ctx context.Context, serviceName, version string) (func(context.Context) error, error) {
	traceExporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, fmt.Errorf("otlp trace exporter: %w", err)
	}
	logExporter, err := otlploghttp.New(ctx)
	if err != nil {
		return nil, fmt.Errorf("otlp log exporter: %w", err)
	}
	metricExporter, err := otlpmetrichttp.New(ctx)
	if err != nil {
		return nil, fmt.Errorf("otlp metric exporter: %w", err)
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
		sdktrace.WithBatcher(traceExporter),
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

	// The logs SDK is still beta, so its version is pinned and read on upgrade.
	// A record emitted inside a span carries that span's trace and span ids
	// automatically, which is what makes "logs for this trace" work at all.
	logs := sdklog.NewLoggerProvider(
		sdklog.WithProcessor(sdklog.NewBatchProcessor(logExporter)),
		sdklog.WithResource(res),
	)
	logglobal.SetLoggerProvider(logs)

	meters := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metricExporter,
			sdkmetric.WithInterval(metricInterval))),
		sdkmetric.WithResource(res),
	)
	otel.SetMeterProvider(meters)

	// Shut down in the order the data flows, and keep going after a failure:
	// a trace exporter that will not flush must not cost us the logs as well.
	return func(ctx context.Context) error {
		ctx, cancel := context.WithTimeout(ctx, shutdownTimeout)
		defer cancel()
		return errors.Join(
			provider.Shutdown(ctx),
			logs.Shutdown(ctx),
			meters.Shutdown(ctx),
		)
	}, nil
}
