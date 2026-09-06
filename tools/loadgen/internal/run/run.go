// Package run wires the generator, the measuring proxy and the lag tracker into
// one load test and reports what it observed.
package run

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	telemetrygen "github.com/open-telemetry/opentelemetry-collector-contrib/cmd/telemetrygen/pkg"
	"github.com/open-telemetry/opentelemetry-collector-contrib/cmd/telemetrygen/pkg/traces"

	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/browserdoor"
	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/config"
	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/lag"
	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/measure"
)

// ingestKeyHeader authenticates every export. It is set on the generator rather
// than added by the proxy, so the key is on the wire from the real client and
// the receiver's auth and rate-limit gates are inside the measured path.
const ingestKeyHeader = "x-nexovue-key"

// Load runs one load test and returns everything it measured.
func Load(ctx context.Context, cfg config.Config, log *slog.Logger) (Report, error) {
	tracker := lag.NewTracker(lag.Options{
		URL:          cfg.ClickHouse.URL,
		User:         cfg.ClickHouse.User,
		Password:     cfg.ClickHouse.Password,
		Database:     cfg.ClickHouse.Database,
		ServiceName:  cfg.ServiceName,
		PollInterval: cfg.LagPollInterval,
	})

	recorder := measure.NewRecorder()
	proxy := measure.NewProxy(measure.Options{
		Target:         cfg.Endpoint,
		Recorder:       recorder,
		Sink:           tracker,
		Logger:         log,
		RequestTimeout: cfg.RequestTimeout,
		SinkInterval:   cfg.LagSampleInterval,
		Connections:    cfg.Workers,
	})

	proxyAddr, stopProxy, err := proxy.Serve(ctx, cfg.ProxyAddr)
	if err != nil {
		return Report{}, err
	}
	defer func() {
		if err := stopProxy(); err != nil {
			log.Warn("measuring proxy shutdown", slog.Any("error", err))
		}
	}()

	trackerCtx, stopTracker := context.WithCancel(ctx)
	trackerDone := make(chan struct{})
	go func() {
		defer close(trackerDone)
		tracker.Run(trackerCtx)
	}()

	log.Info("load starting",
		slog.String("receiver", cfg.Endpoint),
		slog.String("via", proxyAddr),
		slog.Duration("duration", cfg.Duration),
		slog.Int("workers", cfg.Workers),
		slog.Float64("offered_spans_per_second", cfg.SpanRate),
		slog.Int("batch_size", cfg.BatchSize),
		slog.Int("spans_per_trace", cfg.SpansPerTrace()),
	)

	// Door 3 runs beside the spans rather than after them, because a receiver
	// under one door's load is not the receiver a customer has.
	//
	// It goes straight at the receiver and not through the measuring proxy: the
	// proxy's numbers are the span latency the §4 budgets are written about, and
	// mixing a second signal into them would report a figure that means neither.
	browserDone := make(chan browserdoor.Result, 1)
	go func() {
		browserDone <- driveBrowserDoor(ctx, cfg, log)
	}()

	if err := traces.Start(generatorConfig(cfg, proxyAddr)); err != nil {
		stopTracker()
		<-trackerDone
		return Report{}, fmt.Errorf("generating traces: %w", err)
	}
	browser := <-browserDone

	log.Info("load finished, waiting for spans to become queryable",
		slog.Duration("drain_timeout", cfg.LagDrainTimeout))
	awaitDrain(ctx, trackerDone, stopTracker, cfg.LagDrainTimeout)

	return Report{
		Config:     cfg,
		Transport:  recorder.Result(),
		Lag:        tracker.Result(),
		LagBoundBy: cfg.LagPollInterval,
		Browser:    browser,
	}, nil
}

// driveBrowserDoor runs door 3 for the length of the load, or reports nothing
// when the run is not configured for it.
//
// A failure here does not fail the run: the span budgets are what `just load`
// exists to measure, and losing the browser door's numbers must not cost them.
func driveBrowserDoor(ctx context.Context, cfg config.Config, log *slog.Logger) browserdoor.Result {
	if !cfg.Browser.Enabled() {
		log.Info("browser door skipped: no site key configured")
		return browserdoor.Result{}
	}
	result, err := browserdoor.Drive(ctx, browserdoor.Options{
		Endpoint:           cfg.Endpoint,
		SiteKey:            cfg.Browser.SiteKey,
		Origin:             cfg.Browser.Origin,
		ServiceName:        cfg.Browser.ServiceName,
		Release:            cfg.Browser.Release,
		Visitors:           cfg.Browser.Visitors,
		PageLoadsPerSecond: cfg.Browser.PageLoadsPerSecond,
		ErrorRate:          cfg.Browser.ErrorRate,
		Duration:           cfg.Duration,
		Timeout:            cfg.RequestTimeout,
	}, log)
	if err != nil {
		log.Warn("browser door did not run", slog.Any("error", err))
	}
	return result
}

// awaitDrain lets storage catch up after the load stops, then stops the tracker
// whether or not everything arrived.
func awaitDrain(ctx context.Context, done <-chan struct{}, stop context.CancelFunc, timeout time.Duration) {
	timer := time.NewTimer(timeout)
	defer timer.Stop()

	select {
	case <-timer.C:
	case <-ctx.Done():
	}
	stop()
	<-done
}

// generatorConfig translates our settings into telemetrygen's, which throttles
// per worker and in spans rather than traces.
func generatorConfig(cfg config.Config, proxyAddr string) *traces.Config {
	generator := traces.NewConfig()
	generator.CustomEndpoint = proxyAddr
	generator.UseHTTP = true
	generator.Insecure = true
	generator.HTTPPath = tracesPath
	generator.ServiceName = cfg.ServiceName
	generator.Headers = map[string]any{ingestKeyHeader: cfg.IngestKey}
	generator.WorkerCount = cfg.Workers
	generator.Rate = cfg.RatePerWorker()
	generator.TotalDuration = telemetrygen.DurationWithInf(cfg.Duration)
	generator.NumChildSpans = cfg.ChildSpans
	generator.Batch = true
	generator.BatchSize = cfg.BatchSize
	generator.Timeout = cfg.RequestTimeout
	// Without this a refused export ends the run, and a refusal is a result we
	// want reported rather than a reason to stop measuring.
	generator.AllowExportFailures = true
	return generator
}

// tracesPath is OTLP's fixed path for the spans signal.
const tracesPath = "/v1/traces"
