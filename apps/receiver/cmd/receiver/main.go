// Command receiver is the ingestion edge: it terminates OTLP, authenticates the
// caller, tags tenancy, normalizes to the internal envelope, and publishes to Kafka.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/buildinfo"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/config"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/health"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/logging"
)

// dependencyKafka is the readiness check name; it is what a red /readyz names.
const dependencyKafka = "kafka"

func main() {
	if err := run(); err != nil {
		// Config failures happen before the logger exists, so stderr is the
		// only channel guaranteed to be there.
		fmt.Fprintf(os.Stderr, "%s: %v\n", config.ServiceName, err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	log := logging.New(logging.Options{
		Level:   cfg.LogLevel,
		Format:  cfg.LogFormat,
		Service: config.ServiceName,
		Env:     cfg.Env,
	})
	info := buildinfo.For(config.ServiceName)

	kafka, err := kgo.NewClient(kgo.SeedBrokers(cfg.KafkaBrokers...))
	if err != nil {
		return fmt.Errorf("kafka client: %w", err)
	}
	defer kafka.Close()

	registry := health.NewRegistry()
	registry.Register(health.Check{Name: dependencyKafka, Probe: kafka.Ping})

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	server := health.NewServer(cfg.HealthAddr, health.Handler(info, registry))
	log.InfoContext(ctx, "receiver starting",
		slog.String("revision", info.Revision),
		slog.String("health_addr", cfg.HealthAddr),
		slog.String("otlp_http_addr", cfg.OTLPHTTPAddr),
	)

	return serveUntilSignal(ctx, log, server, cfg.ShutdownTimeout)
}

// serveUntilSignal runs the admin listener until the process is asked to stop,
// then drains it within the configured budget.
func serveUntilSignal(
	ctx context.Context,
	log *slog.Logger,
	server *http.Server,
	shutdownTimeout time.Duration,
) error {
	failed := make(chan error, 1)
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			failed <- err
		}
	}()

	select {
	case err := <-failed:
		return fmt.Errorf("health server: %w", err)
	case <-ctx.Done():
		log.Info("shutdown requested, draining")
	}

	// WithoutCancel keeps the context values while dropping the cancellation
	// that just fired, so the drain gets its own full budget.
	drainCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), shutdownTimeout)
	defer cancel()
	if err := server.Shutdown(drainCtx); err != nil {
		return fmt.Errorf("health server shutdown: %w", err)
	}
	log.Info("receiver stopped")
	return nil
}
