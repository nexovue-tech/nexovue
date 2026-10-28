// Command setup prepares ClickHouse and Kafka before any service starts (docs/changes/ready-to-deploy,
// T1). Every deployment runs it first; running it again changes nothing.
//
//	setup                    the ClickHouse schema, then the Kafka topics
//	setup clickhouse         the schema only
//	setup topics             the topics only
//	setup clickhouse-down N  revert the newest N migrations (development only)
//
// Reads CLICKHOUSE_DSN (native protocol), POSTGRES_DSN (for the migration lock), KAFKA_BROKERS and
// SETUP_MIGRATIONS_DIR; the topic sizes may be changed with KAFKA_SPAN_PARTITIONS,
// KAFKA_OTHER_PARTITIONS, KAFKA_RETENTION_MS and KAFKA_MAX_MESSAGE_BYTES.
package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/setup"
)

// The image carries the migrations here (deploy/images/nexovue-pipeline.Dockerfile).
const defaultMigrationsDir = "/migrations/clickhouse"

// Far above any sensible count, and well inside int32.
const maxPartitions = 1024

// Long enough for a schema change on a large table; a stuck run still ends.
const runTimeout = 30 * time.Minute

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "setup:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	ctx, cancel := context.WithTimeout(context.Background(), runTimeout)
	defer cancel()

	command := "all"
	if len(args) > 0 {
		command = args[0]
	}
	switch command {
	case "all":
		if err := migrateClickHouse(ctx); err != nil {
			return err
		}
		return ensureTopics(ctx)
	case "clickhouse":
		return migrateClickHouse(ctx)
	case "topics":
		return ensureTopics(ctx)
	case "clickhouse-down":
		steps := 1
		if len(args) > 1 {
			parsed, err := strconv.Atoi(args[1])
			if err != nil || parsed < 1 {
				return errors.New("clickhouse-down takes a positive number of steps")
			}
			steps = parsed
		}
		return revertClickHouse(ctx, steps)
	default:
		return fmt.Errorf("unknown command %q: use clickhouse, topics or clickhouse-down", command)
	}
}

func migrateClickHouse(ctx context.Context) error {
	return withLedger(ctx, func(ledger setup.Ledger, migrations []setup.Migration) error {
		applied, err := setup.Up(ctx, ledger, migrations)
		if err != nil {
			return err
		}
		fmt.Printf("clickhouse: %d migration(s) applied, %d known\n", applied, len(migrations))
		return nil
	})
}

func revertClickHouse(ctx context.Context, steps int) error {
	return withLedger(ctx, func(ledger setup.Ledger, migrations []setup.Migration) error {
		reverted, err := setup.Down(ctx, ledger, migrations, steps)
		if err != nil {
			return err
		}
		fmt.Printf("clickhouse: %d migration(s) reverted\n", reverted)
		return nil
	})
}

func withLedger(ctx context.Context, use func(setup.Ledger, []setup.Migration) error) error {
	migrations, err := setup.Load(os.DirFS(envOr("SETUP_MIGRATIONS_DIR", defaultMigrationsDir)))
	if err != nil {
		return err
	}
	target, err := config.ParseClickHouseDSN(os.Getenv("CLICKHOUSE_DSN"))
	if err != nil {
		return err
	}
	postgres := os.Getenv("POSTGRES_DSN")
	if postgres == "" {
		return errors.New("POSTGRES_DSN is required: it holds the lock that keeps two setups apart")
	}

	release, err := setup.HoldMigrationLock(ctx, postgres)
	if err != nil {
		return err
	}
	defer release()

	pool, err := chpool.Dial(ctx, chpool.Options{ClientOptions: ch.Options{
		Address:  target.Address,
		Database: target.Database,
		User:     target.User,
		Password: target.Password,
	}})
	if err != nil {
		return fmt.Errorf("connect to ClickHouse: %w", err)
	}
	defer pool.Close()
	return use(setup.NewClickHouseLedger(pool, setup.LedgerTable), migrations)
}

func ensureTopics(ctx context.Context) error {
	brokers := os.Getenv("KAFKA_BROKERS")
	if brokers == "" {
		return errors.New("KAFKA_BROKERS is required")
	}
	settings, err := topicSettings()
	if err != nil {
		return err
	}
	client, err := kgo.NewClient(kgo.SeedBrokers(strings.Split(brokers, ",")...))
	if err != nil {
		return fmt.Errorf("connect to Kafka: %w", err)
	}
	defer client.Close()

	results, err := setup.EnsureTopics(ctx, kadm.NewClient(client), settings)
	for _, result := range results {
		state := "exists"
		if result.Created {
			state = "created"
		}
		fmt.Printf("topic %s: %s\n", result.Name, state)
	}
	return err
}

func topicSettings() (setup.TopicSettings, error) {
	settings := setup.DefaultTopicSettings
	for _, override := range []struct {
		variable string
		apply    func(int64)
	}{
		{"KAFKA_SPAN_PARTITIONS", func(v int64) { settings.SpanPartitions = int32(v) }},   //nolint:gosec // capped at maxPartitions below
		{"KAFKA_OTHER_PARTITIONS", func(v int64) { settings.OtherPartitions = int32(v) }}, //nolint:gosec // capped at maxPartitions below
		{"KAFKA_RETENTION_MS", func(v int64) { settings.RetentionMillis = v }},
		{"KAFKA_MAX_MESSAGE_BYTES", func(v int64) { settings.MaxMessageBytes = v }},
	} {
		raw := os.Getenv(override.variable)
		if raw == "" {
			continue
		}
		value, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || value < 1 {
			return settings, fmt.Errorf("%s must be a positive whole number", override.variable)
		}
		if strings.HasSuffix(override.variable, "_PARTITIONS") && value > maxPartitions {
			return settings, fmt.Errorf("%s may be at most %d", override.variable, maxPartitions)
		}
		override.apply(value)
	}
	return settings, nil
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
