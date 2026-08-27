// Package worker boots the worker process. It runs whichever pipelines its
// configuration selects; the pipelines themselves arrive with M1/M2.
package worker

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"slices"
	"syscall"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/buildinfo"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/health"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/logging"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/traceassembly"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/projectconfig"
)

// Readiness check names; these are what a red /readyz names.
const (
	dependencyKafka      = "kafka"
	dependencyClickHouse = "clickhouse"
	dependencyPostgres   = "postgres"
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

	// Optional: without it the pipeline stages run on their built-in defaults
	// rather than on per-project rules.
	control, err := controlPlanePool(ctx, cfg)
	if err != nil {
		return err
	}
	if control != nil {
		defer control.Close()
	}
	stageOptions := pipelineOptions(ctx, cfg, control, log)

	registry := health.NewRegistry()
	registry.Register(health.Check{Name: dependencyKafka, Probe: kafka.Ping})
	registry.Register(health.Check{Name: dependencyClickHouse, Probe: store.Ping})
	if control != nil {
		registry.Register(health.Check{Name: dependencyPostgres, Probe: control.Ping})
	}

	server := health.NewServer(cfg.HealthAddr, health.Handler(info, registry))
	log.InfoContext(ctx, "worker starting",
		slog.String("revision", info.Revision),
		slog.String("health_addr", cfg.HealthAddr),
		slog.String("clickhouse_address", cfg.ClickHouse.Address),
		slog.String("pipelines", config.JoinPipelines(cfg.Pipelines)),
	)

	// Buffered so the first failure is recorded even if nothing is selecting
	// on it yet, and so reporting one never blocks a pipeline's exit.
	failures := make(chan error, 1)
	stopPipelines, err := startPipelines(ctx, cfg, store, log, stageOptions, failures)
	if err != nil {
		return err
	}
	defer stopPipelines()

	return serveUntilSignal(ctx, log, server, failures, cfg.ShutdownTimeout)
}

// startPipelines launches every pipeline this process is configured to run and
// returns the function that releases them.
func startPipelines(
	ctx context.Context,
	cfg config.Config,
	store *chpool.Pool,
	log *slog.Logger,
	stageOptions traceassembly.Options,
	failures chan<- error,
) (func(), error) {
	var started []func()
	stop := func() {
		for _, release := range started {
			release()
		}
	}

	if slices.Contains(cfg.Pipelines, config.PipelineTraceAssembly) {
		spans, err := traceassembly.New(cfg, store, log, stageOptions)
		if err != nil {
			stop()
			return nil, err
		}
		started = append(started, spans.Close)
		go reportFailure(failures, func() error { return spans.Run(ctx) })
	}

	return stop, nil
}

// reportFailure runs a pipeline and hands its failure to the shutdown path. A
// full channel means an earlier pipeline already failed, and that first cause
// is the one worth reporting.
func reportFailure(failures chan<- error, run func() error) {
	if err := run(); err != nil {
		select {
		case failures <- err:
		default:
		}
	}
}

// controlPlanePool opens the connection the pipeline reads per-project settings
// from, or returns nil when none is configured.
func controlPlanePool(ctx context.Context, cfg config.Config) (*pgxpool.Pool, error) {
	if !cfg.Postgres.Configured() {
		return nil, nil
	}
	poolConfig, err := pgxpool.ParseConfig(cfg.Postgres.DSN)
	if err != nil {
		// A DSN in a container log outlives the process that wrote it.
		return nil, fmt.Errorf("postgres dsn: %s", logging.RedactCredentials(err.Error()))
	}
	poolConfig.MaxConns = cfg.Postgres.MaxConns
	poolConfig.MinConns = cfg.Postgres.MinConns

	pool, err := pgxpool.NewWithConfig(ctx, poolConfig)
	if err != nil {
		return nil, fmt.Errorf("postgres pool: %s", logging.RedactCredentials(err.Error()))
	}
	return pool, nil
}

// pipelineOptions builds the per-project settings source and starts refreshing
// it. Without a control-plane connection the stages fall back to their own
// defaults, which is said out loud rather than assumed.
func pipelineOptions(
	ctx context.Context,
	cfg config.Config,
	control *pgxpool.Pool,
	log *slog.Logger,
) traceassembly.Options {
	if control == nil {
		log.WarnContext(ctx, "no control-plane connection: per-project masking and sampling rules are not loaded",
			slog.String("variable", "POSTGRES_DSN"))
		return traceassembly.Options{}
	}

	defaults := projectconfig.Settings{
		Masking: mask.Defaults(),
		Sampling: sample.DefaultPolicy(
			cfg.Sampling.SlowThreshold, cfg.Sampling.HealthyPercent),
	}
	cache := projectconfig.NewCache(defaults)
	refresher := projectconfig.NewRefresher(
		projectconfig.NewPostgresReader(control), cache, defaults, cfg.Masking.ProjectRefresh, log)
	go refresher.Run(ctx)

	return traceassembly.Options{Masking: cache, Sampling: cache}
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
	pipelineFailures <-chan error,
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
	case err := <-pipelineFailures:
		return fmt.Errorf("pipeline: %w", err)
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
