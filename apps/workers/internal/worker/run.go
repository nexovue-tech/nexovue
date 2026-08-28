// Package worker boots the worker process. It runs whichever pipelines its
// configuration selects; the pipelines themselves arrive with M1/M2.
package worker

import (
	"context"
	"encoding/base64"
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
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/logwriting"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/metricwriting"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/traceassembly"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/projectconfig"
)

// Readiness check names; these are what a red /readyz names.
const (
	dependencyKafka      = "kafka"
	dependencyClickHouse = "clickhouse"
	dependencyPostgres   = "postgres"

	// Readiness has to answer "is this worker doing its job", not merely "are
	// its dependencies reachable". A pipeline that stops leaves every dependency
	// probe green while nothing is consumed.
	checkTraceAssembly = "trace-assembly"
	checkLogWriting    = "log-writing"
	checkMetricIngest  = "metric-ingest"
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
	liveness := health.NewRegistry()
	registry.Register(health.Check{Name: dependencyKafka, Probe: kafka.Ping})
	registry.Register(health.Check{Name: dependencyClickHouse, Probe: store.Ping})
	if control != nil {
		registry.Register(health.Check{Name: dependencyPostgres, Probe: control.Ping})
	}

	server := health.NewServer(cfg.HealthAddr, health.Handler(info, liveness, registry))
	log.InfoContext(ctx, "worker starting",
		slog.String("revision", info.Revision),
		slog.String("health_addr", cfg.HealthAddr),
		slog.String("clickhouse_address", cfg.ClickHouse.Address),
		slog.String("pipelines", config.JoinPipelines(cfg.Pipelines)),
	)

	// Buffered so the first failure is recorded even if nothing is selecting
	// on it yet, and so reporting one never blocks a pipeline's exit.
	failures := make(chan error, 1)
	stopPipelines, err := startPipelines(ctx, cfg, store, log, stageOptions, registry, liveness, failures, stop)
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
	registry *health.Registry,
	liveness *health.Registry,
	failures chan<- error,
	shutdown context.CancelFunc,
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
		// Both: a stopped pipeline means restart me, and also means not ready.
		registry.Register(health.Check{Name: checkTraceAssembly, Probe: spans.Running})
		liveness.Register(health.Check{Name: checkTraceAssembly, Probe: spans.Running})
		go reportFailure(ctx, failures, shutdown, log, config.PipelineTraceAssembly, func() error { return spans.Run(ctx) })
	}

	// One masker over the same rules trace assembly reads, so a project's
	// masking means one thing across every signal. Built here rather than per
	// pipeline: rules are cached per project, and three caches of one answer
	// are three chances to disagree.
	masker := mask.New(maskingRules(stageOptions))

	if slices.Contains(cfg.Pipelines, config.PipelineLogWriting) {
		// Both constructors refuse a nil masker rather than defaulting to a
		// no-op, so this is the only place that decision is made.
		logs, err := logwriting.New(cfg, store, log, logwriting.Options{
			Masking:   masker,
			Retention: stageOptions.Retention,
		})
		if err != nil {
			stop()
			return nil, err
		}
		started = append(started, logs.Close)
		registry.Register(health.Check{Name: checkLogWriting, Probe: logs.Running})
		liveness.Register(health.Check{Name: checkLogWriting, Probe: logs.Running})
		go reportFailure(ctx, failures, shutdown, log, config.PipelineLogWriting, func() error { return logs.Run(ctx) })
	}

	if slices.Contains(cfg.Pipelines, config.PipelineMetricIngest) {
		metrics, err := metricwriting.New(cfg, store, log, metricwriting.Options{
			Masking:   masker,
			Retention: stageOptions.Retention,
		})
		if err != nil {
			stop()
			return nil, err
		}
		started = append(started, metrics.Close)
		registry.Register(health.Check{Name: checkMetricIngest, Probe: metrics.Running})
		liveness.Register(health.Check{Name: checkMetricIngest, Probe: metrics.Running})
		go reportFailure(ctx, failures, shutdown, log, config.PipelineMetricIngest, func() error { return metrics.Run(ctx) })
	}

	return stop, nil
}

// maskingRules is the rules source every signal masks against — the project
// config cache in a real deployment, and the shipped defaults when nothing has
// told the worker otherwise. Mirrors what trace assembly does with the same
// option, so the three pipelines cannot mask against different rules.
func maskingRules(opts traceassembly.Options) mask.RulesSource {
	if opts.Masking != nil {
		return opts.Masking
	}
	return mask.StaticRules(mask.Defaults())
}

// reportFailure runs a pipeline and hands its ending to the shutdown path.
//
// A pipeline that *returns at all* while the worker is still meant to be running
// is a failure, whatever it returned. Treating only a non-nil error that way left
// a hole the chaos test walked straight into: after a broker outage the loop
// ended, the process stayed up, and the worker sat for nine minutes holding its
// partitions and consuming nothing. Why it returned matters for the fix; that it
// returned is enough to know the worker is finished.
//
// A full channel means an earlier pipeline already failed, and that first cause
// is the one worth reporting.
func reportFailure(
	ctx context.Context,
	failures chan<- error,
	shutdown context.CancelFunc,
	log *slog.Logger,
	name config.Pipeline,
	run func() error,
) {
	err := run()
	if err == nil && ctx.Err() == nil {
		err = fmt.Errorf("%s returned while the worker was still running", name)
	}
	if err == nil {
		return
	}

	// Said out loud, because this is the line that explains a restart.
	log.Error("pipeline ended; the worker cannot continue",
		slog.String("pipeline", string(name)),
		slog.String("error", err.Error()),
	)

	// Two independent paths out, because one is not enough. The channel carries
	// the cause so the exit reports *why*; cancelling guarantees the process
	// actually leaves even if nothing is listening on the channel. A worker that
	// keeps running with a dead pipeline holds its partitions and consumes
	// nothing, which is the failure this whole milestone kept rediscovering.
	select {
	case failures <- err:
	default:
	}
	shutdown()
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
		// A project the refresher has not seen yet keeps the deployment
		// default rather than zero, which would expire its rows on arrival.
		RawRetentionDays:     cfg.Retention.RawDays,
		DerivedRetentionDays: cfg.Retention.DerivedDays,
	}
	cache := projectconfig.NewCache(defaults)
	refresher := projectconfig.NewRefresher(
		projectconfig.NewPostgresReader(control), cache, defaults, cfg.Masking.ProjectRefresh, log,
		maskSecret(cfg, log))
	go refresher.Run(ctx)

	return traceassembly.Options{Masking: cache, Sampling: cache, Retention: cache}
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
		// A pipeline that failed cancels the context as well as sending its
		// cause; prefer the cause, so the exit explains itself.
		select {
		case err := <-pipelineFailures:
			return fmt.Errorf("pipeline: %w", err)
		default:
		}
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

// maskSecret decodes the deployment-wide masking secret, if one is set.
//
// The secret is derived per project rather than used directly. A single
// deployment-wide key would give one person the same pseudonym in every
// tenant's data, so anyone able to read two tenants could join them — which is
// the tenant-isolation invariant leaking through a privacy feature. Deriving
// per project costs a customer the ability to correlate one person across their
// own projects, and a project is an environment (ADR 0042), so that is
// correlating across staging and production: not a thing anyone needs.
func maskSecret(cfg config.Config, log *slog.Logger) []byte {
	if cfg.Masking.HashSecret == "" {
		log.Info("no masking hash secret configured; rules may blank a value but not " +
			"pseudonymise one")
		return nil
	}
	secret, err := base64.StdEncoding.DecodeString(cfg.Masking.HashSecret)
	if err != nil {
		// Validated at boot; unreachable without the config layer being wrong.
		log.Error("masking hash secret is not base64", slog.String("error", err.Error()))
		return nil
	}
	return secret
}
