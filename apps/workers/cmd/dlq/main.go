// Command dlq lists the dead-letter topics and replays a parked record to the
// topic it came from (group 9, C5; docs/operations/dead-letters.md).
//
//	dlq list   -topic logs-dlq [-limit 50]
//	dlq replay -topic logs-dlq -from 12 -to 14
//
// Shipped in the worker image (`/dlq`), so it runs beside the worker with the
// same KAFKA_BROKERS. Nothing it reads is printed beyond the headers: a parked
// payload is unmasked customer data.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/dlqtool"
)

// The dead-letter topics have one partition each (apps/workers/internal/setup/topics.go).
const partition = int32(0)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "dlq:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		return errors.New("usage: dlq list|replay -topic <name>-dlq [...]")
	}
	flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
	topic := flags.String("topic", "", "the dead-letter topic, e.g. logs-dlq")
	limit := flags.Int("limit", 50, "list: the newest this many")
	from := flags.Int64("from", -1, "replay: the first offset")
	to := flags.Int64("to", -1, "replay: the last offset, inclusive")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if !strings.HasSuffix(*topic, "-dlq") {
		return errors.New("-topic must name a dead-letter topic, ending -dlq")
	}

	brokers := strings.Split(envOr("KAFKA_BROKERS", "localhost:19092"), ",")
	client, err := kgo.NewClient(kgo.SeedBrokers(brokers...))
	if err != nil {
		return err
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()

	start, end, err := bounds(ctx, client, *topic)
	if err != nil {
		return err
	}
	switch args[0] {
	case "list":
		first := max(start, end-int64(*limit))
		return each(ctx, client, *topic, first, end-1, func(record *kgo.Record) error {
			p := dlqtool.Describe(record)
			fmt.Printf("%d\t%s\t%s\t%s[%d]@%d\ttenant=%s\t%s\n", p.Offset,
				p.ParkedAt.Format(time.RFC3339), p.ConsumerGroup, p.SourceTopic,
				p.SourcePartition, p.SourceOffset, p.TenantID, clipped(p.Reason))
			return nil
		})
	case "replay":
		if *from < start || *to < *from || *to >= end {
			return fmt.Errorf("-from/-to must lie within the retained offsets %d..%d", start, end-1)
		}
		replayed := 0
		err := each(ctx, client, *topic, *from, *to, func(record *kgo.Record) error {
			back, err := dlqtool.Replay(record)
			if err != nil {
				fmt.Fprintln(os.Stderr, "skipped:", err)
				return nil
			}
			if err := client.ProduceSync(ctx, back).FirstErr(); err != nil {
				return fmt.Errorf("offset %d: %w", record.Offset, err)
			}
			replayed++
			return nil
		})
		fmt.Printf("replayed %d record(s)\n", replayed)
		return err
	default:
		return fmt.Errorf("unknown command %q: list or replay", args[0])
	}
}

func bounds(ctx context.Context, client *kgo.Client, topic string) (int64, int64, error) {
	admin := kadm.NewClient(client)
	starts, err := admin.ListStartOffsets(ctx, topic)
	if err != nil {
		return 0, 0, err
	}
	ends, err := admin.ListEndOffsets(ctx, topic)
	if err != nil {
		return 0, 0, err
	}
	first, ok := starts.Lookup(topic, partition)
	last, ok2 := ends.Lookup(topic, partition)
	if !ok || !ok2 {
		return 0, 0, fmt.Errorf("no partition %d in %s", partition, topic)
	}
	return first.Offset, last.Offset, nil
}

// each hands every record from..to (inclusive) to fn, in order.
func each(ctx context.Context, client *kgo.Client, topic string, from, to int64, fn func(*kgo.Record) error) error {
	if to < from {
		return nil
	}
	client.AddConsumePartitions(map[string]map[int32]kgo.Offset{
		topic: {partition: kgo.NewOffset().At(from)},
	})
	for {
		fetches := client.PollFetches(ctx)
		if err := fetches.Err0(); err != nil {
			return err
		}
		for _, record := range fetches.Records() {
			if record.Offset > to {
				return nil
			}
			if err := fn(record); err != nil {
				return err
			}
			if record.Offset == to {
				return nil
			}
		}
	}
}

// clipped keeps a listing to one readable line: a store's refusal can quote the
// whole value it refused.
func clipped(reason string) string {
	const longest = 200
	if len(reason) <= longest {
		return reason
	}
	return reason[:longest] + "…"
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
