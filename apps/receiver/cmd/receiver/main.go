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

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/twmb/franz-go/pkg/kgo"
	"github.com/valkey-io/valkey-go"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/buildinfo"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/config"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/health"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ingest"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/logging"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ratelimit"
)

// Readiness check names; they are what a red /readyz names.
const (
	dependencyKafka    = "kafka"
	dependencyPostgres = "postgres"
	dependencyValkey   = "valkey"
)

func main() {
	// `receiver -health` is the container healthcheck: distroless has no shell to
	// run one with, so the binary checks itself. Uses the default port rather than
	// config, because a config failure is already a hard exit.
	health.RunSelfCheckIfAsked(os.Args, config.DefaultHealthAddr)

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

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	dependencies, err := dial(ctx, cfg)
	if err != nil {
		return err
	}
	defer dependencies.close()

	registry := health.NewRegistry()
	registry.Register(health.Check{Name: dependencyKafka, Probe: dependencies.kafka.Ping})
	registry.Register(health.Check{Name: dependencyPostgres, Probe: dependencies.postgres.Ping})
	registry.Register(health.Check{Name: dependencyValkey, Probe: dependencies.pingValkey})

	admin := health.NewServer(cfg.HealthAddr, health.Handler(info, registry))
	door := otlp.NewServer(cfg.OTLPHTTPAddr, otlp.Handler(otlp.Options{
		Authenticator: auth.New(auth.NewPostgresKeys(dependencies.postgres), auth.Options{
			CacheTTL:        cfg.APIKeyCacheTTL,
			CacheMaxEntries: cfg.APIKeyCacheMaxEntries,
		}),
		Limiter:             ratelimit.New(ratelimit.NewValkeyCounter(dependencies.valkey), cfg.RateLimitPerSecond),
		Publisher:           ingest.NewPublisher(dependencies.kafka, cfg.SpansTopic),
		Logger:              log,
		MaxBodyBytes:        cfg.MaxBodyBytes,
		MaxDecodedBodyBytes: cfg.MaxDecodedBodyBytes,
	}))

	log.InfoContext(ctx, "receiver starting",
		slog.String("revision", info.Revision),
		slog.String("health_addr", cfg.HealthAddr),
		slog.String("otlp_http_addr", cfg.OTLPHTTPAddr),
		slog.String("spans_topic", cfg.SpansTopic),
	)

	return serveUntilSignal(ctx, log, []*http.Server{admin, door}, cfg.ShutdownTimeout)
}

// dependencies are the clients the receiver holds open for its whole life.
type dependencies struct {
	kafka    *kgo.Client
	postgres *pgxpool.Pool
	valkey   valkey.Client
}

// dial opens every client, closing whatever it already opened if a later one
// fails, so a boot failure never leaves a connection behind.
func dial(ctx context.Context, cfg config.Config) (opened *dependencies, err error) {
	opened = &dependencies{}
	defer func() {
		if err != nil {
			opened.close()
			opened = nil
		}
	}()

	opened.kafka, err = kgo.NewClient(
		kgo.SeedBrokers(cfg.KafkaBrokers...),
		// Stated rather than inherited from the library default: trace assembly
		// depends on every span of a trace hashing to one partition, and that
		// default has changed before.
		kgo.RecordPartitioner(kgo.StickyKeyPartitioner(nil)),
	)
	if err != nil {
		return nil, fmt.Errorf("kafka client: %w", err)
	}

	if opened.postgres, err = postgresPool(ctx, cfg); err != nil {
		return nil, err
	}

	options, err := valkey.ParseURL(cfg.RedisURL)
	if err != nil {
		return nil, bootFailure("valkey url", err)
	}
	if opened.valkey, err = valkey.NewClient(options); err != nil {
		return nil, bootFailure("valkey client", err)
	}
	return opened, nil
}

// postgresPool sizes the pool from configuration rather than taking pgx's
// CPU-derived default, which has nothing to do with this workload.
func postgresPool(ctx context.Context, cfg config.Config) (*pgxpool.Pool, error) {
	poolConfig, err := pgxpool.ParseConfig(cfg.PostgresDSN)
	if err != nil {
		return nil, bootFailure("postgres dsn", err)
	}
	poolConfig.MaxConns = cfg.PostgresMaxConns
	poolConfig.MinConns = cfg.PostgresMinConns

	pool, err := pgxpool.NewWithConfig(ctx, poolConfig)
	if err != nil {
		return nil, bootFailure("postgres pool", err)
	}
	return pool, nil
}

// bootFailure strips the password out of a driver error. Boot failures print to
// stderr, and a DSN in a container log outlives the process that wrote it.
func bootFailure(what string, err error) error {
	return fmt.Errorf("%s: %s", what, logging.RedactCredentials(err.Error()))
}

func (d *dependencies) close() {
	// Called on a partly-dialled set as well as a complete one.
	if d.kafka != nil {
		d.kafka.Close()
	}
	if d.postgres != nil {
		d.postgres.Close()
	}
	if d.valkey != nil {
		d.valkey.Close()
	}
}

func (d *dependencies) pingValkey(ctx context.Context) error {
	return d.valkey.Do(ctx, d.valkey.B().Ping().Build()).Error()
}

// serveUntilSignal runs every listener until the process is asked to stop, then
// drains them all within the configured budget.
func serveUntilSignal(
	ctx context.Context,
	log *slog.Logger,
	servers []*http.Server,
	shutdownTimeout time.Duration,
) error {
	failed := make(chan error, len(servers))
	for _, server := range servers {
		go func() {
			if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
				failed <- fmt.Errorf("listening on %s: %w", server.Addr, err)
			}
		}()
	}

	select {
	case err := <-failed:
		return err
	case <-ctx.Done():
		log.Info("shutdown requested, draining")
	}

	// WithoutCancel keeps the context values while dropping the cancellation
	// that just fired, so the drain gets its own full budget.
	drainCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), shutdownTimeout)
	defer cancel()

	var drainErr error
	for _, server := range servers {
		if err := server.Shutdown(drainCtx); err != nil {
			drainErr = errors.Join(drainErr, fmt.Errorf("draining %s: %w", server.Addr, err))
		}
	}
	if drainErr != nil {
		return drainErr
	}
	log.Info("receiver stopped")
	return nil
}
