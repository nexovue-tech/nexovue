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

	traffic := otlp.NewTraffic()

	// One set of collaborators for both transports: the gates behind them are
	// the same code, and a second Options would be a second pipeline.
	door := otlp.Options{
		Authenticator: auth.New(auth.NewPostgresKeys(dependencies.postgres), auth.Options{
			CacheTTL:        cfg.APIKeyCacheTTL,
			CacheMaxEntries: cfg.APIKeyCacheMaxEntries,
		}),
		Limiter:             ratelimit.New(ratelimit.NewValkeyCounter(dependencies.valkey), cfg.RateLimitPerSecond),
		Publisher:           ingest.NewPublisher(dependencies.kafka, cfg.SpansTopic, cfg.ProduceTimeout),
		Logger:              log,
		Traffic:             traffic,
		MaxBodyBytes:        cfg.MaxBodyBytes,
		MaxDecodedBodyBytes: cfg.MaxDecodedBodyBytes,
	}

	admin := health.NewServer(cfg.HealthAddr, health.Handler(info, registry))
	overHTTP := otlp.NewServer(cfg.OTLPHTTPAddr, otlp.Handler(door))
	overGRPC := otlp.NewGRPCServer(cfg.OTLPGRPCAddr, door)

	log.InfoContext(ctx, "receiver starting",
		slog.String("revision", info.Revision),
		slog.String("health_addr", cfg.HealthAddr),
		slog.String("otlp_http_addr", cfg.OTLPHTTPAddr),
		slog.String("otlp_grpc_addr", cfg.OTLPGRPCAddr),
		slog.String("spans_topic", cfg.SpansTopic),
	)

	// The heartbeat: without it a receiver serving nothing and a receiver
	// serving everything write the same empty log.
	go traffic.LogSummaryEvery(ctx, log, cfg.IngestSummaryInterval)

	return serveUntilSignal(ctx, log, []listener{
		httpListener(admin),
		httpListener(overHTTP),
		grpcListener(overGRPC),
	}, cfg.ShutdownTimeout)
}

// listener is one served port as the start-and-drain sequence sees it. The OTLP
// doors do not share a server type, and every listener drains on one budget.
type listener struct {
	addr string
	// serve blocks until the listener stops, returning nil for a clean stop.
	serve    func() error
	shutdown func(context.Context) error
}

func httpListener(server *http.Server) listener {
	return listener{
		addr: server.Addr,
		serve: func() error {
			if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
				return err
			}
			return nil
		},
		shutdown: server.Shutdown,
	}
}

func grpcListener(server *otlp.GRPCServer) listener {
	return listener{
		addr:     server.Addr(),
		serve:    server.ListenAndServe,
		shutdown: server.Shutdown,
	}
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
		// franz-go buffers without limit by default — looser than Kafka's own
		// client, which bounds buffer.memory at 32 MiB. Unbounded, a broker
		// outage grows the receiver until it dies instead of refusing work.
		kgo.MaxBufferedBytes(int(cfg.KafkaMaxBufferedBytes)),
		// And it retries a record forever by default. A delivery deadline is
		// what turns a stuck publish into a 503 the sender can act on, which is
		// the only ending that does not lose the batch.
		kgo.RecordDeliveryTimeout(cfg.ProduceTimeout),
		// franz-go caps a record batch at Kafka's 1 MiB default, well under what
		// the door accepts, so a single large trace was refused here and retried
		// by the sender forever. The producer must carry anything we accepted.
		kgo.ProducerBatchMaxBytes(int32(cfg.MaxRecordBytes())),
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
	listeners []listener,
	shutdownTimeout time.Duration,
) error {
	failed := make(chan error, len(listeners))
	for _, served := range listeners {
		go func() {
			if err := served.serve(); err != nil {
				failed <- fmt.Errorf("listening on %s: %w", served.addr, err)
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
	for _, served := range listeners {
		if err := served.shutdown(drainCtx); err != nil {
			drainErr = errors.Join(drainErr, fmt.Errorf("draining %s: %w", served.addr, err))
		}
	}
	if drainErr != nil {
		return drainErr
	}
	log.Info("receiver stopped")
	return nil
}
