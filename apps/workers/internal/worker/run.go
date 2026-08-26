// Package worker boots one worker binary. Configuration, logging, dependency
// probes and graceful shutdown are identical across the four binaries; only the
// pipeline each one runs differs, and that arrives with M1/M2.
package worker

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

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/buildinfo"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/health"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/logging"
)

// Readiness check names; these are what a red /readyz names.
const (
	dependencyKafka      = "kafka"
	dependencyClickHouse = "clickhouse"
)

// Main is the whole body of every worker binary: it runs the named worker and
// turns any failure into a message on stderr and a non-zero exit.
func Main(serviceName string) {
	if err := run(serviceName); err != nil {
		// Config failures happen before the logger exists, so stderr is the
		// only channel guaranteed to be there.
		fmt.Fprintf(os.Stderr, "%s: %v\n", serviceName, err)
		os.Exit(1)
	}
}

func run(serviceName string) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	log := logging.New(logging.Options{
		Level:   cfg.LogLevel,
		Format:  cfg.LogFormat,
		Service: serviceName,
		Env:     cfg.Env,
	})
	info := buildinfo.For(serviceName)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	kafka, err := kgo.NewClient(kgo.SeedBrokers(cfg.KafkaBrokers...))
	if err != nil {
		return fmt.Errorf("kafka client: %w", err)
	}
	defer kafka.Close()

	// New rather than Dial: a store that is down at boot must show up as a red
	// readiness probe, not as a crash loop.
	store, err := chpool.New(ctx, clickHouseOptions(cfg))
	if err != nil {
		return fmt.Errorf("clickhouse pool: %w", err)
	}
	defer store.Close()

	registry := health.NewRegistry()
	registry.Register(health.Check{Name: dependencyKafka, Probe: kafka.Ping})
	registry.Register(health.Check{Name: dependencyClickHouse, Probe: store.Ping})

	server := health.NewServer(cfg.HealthAddr, health.Handler(info, registry))
	log.InfoContext(ctx, "worker starting",
		slog.String("revision", info.Revision),
		slog.String("health_addr", cfg.HealthAddr),
		slog.String("clickhouse_address", cfg.ClickHouse.Address),
	)

	return serveUntilSignal(ctx, log, server, cfg.ShutdownTimeout)
}

func clickHouseOptions(cfg config.Config) chpool.Options {
	return chpool.Options{
		ClientOptions: ch.Options{
			Address:  cfg.ClickHouse.Address,
			Database: cfg.ClickHouse.Database,
			User:     cfg.ClickHouse.User,
			Password: cfg.ClickHouse.Password,
		},
		// Set explicitly: the library default is one connection per CPU, which
		// is a different number on every machine the worker runs on.
		MaxConns: cfg.ClickHouseMaxConns,
	}
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
	log.Info("worker stopped")
	return nil
}
