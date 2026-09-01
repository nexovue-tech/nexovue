// Command rebalanceprobe writes uniquely identified spans and logs straight to
// the broker, so that what a worker stores can be counted exactly against what
// the broker accepted — the measurement ADRs 0079 and 0085 both record as
// missing for a consumer-group rebalance across two workers.
//
// It produces envelopes rather than posting OTLP at the receiver for one
// reason: the receiver keys log and metric records by tenant id, so every
// record from one tenant lands on one partition. A rebalance can then only ever
// hand that partition to a single worker, and the thing under test never
// happens. Spans do not have that problem — they are keyed by trace id — but
// both signals are produced the same way here so one run measures both.
//
// The load is deliberately plain: no retries, no backpressure games, one
// envelope per trace exactly as the receiver splits them. What varies during a
// run is the worker fleet, which is driven from outside this tool.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sync/errgroup"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

type settings struct {
	brokers    []string
	tenantID   string
	projectID  string
	spansTopic string
	logsTopic  string

	spanPartitions int
	logPartitions  int

	duration          time.Duration
	tracesPerSecond   int
	spansPerTrace     int
	logMessagesPerSec int
	logsPerMessage    int
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "rebalanceprobe: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := readSettings()
	if err != nil {
		return err
	}

	runID, err := newRunID()
	if err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// The producer outlives the run's deadline: it is cancelled only by an
	// interrupt, so records still buffered when the run ends are acknowledged
	// rather than aborted and miscounted as failures.
	broker, err := dial(ctx, cfg.brokers)
	if err != nil {
		return err
	}
	defer broker.close()

	spans := &tally{rowsPerMessage: cfg.spansPerTrace}
	logs := &tally{rowsPerMessage: cfg.logsPerMessage}

	fmt.Fprintf(os.Stderr, "run %s: %s of spans and logs to %d and %d partitions\n",
		runID, cfg.duration, cfg.spanPartitions, cfg.logPartitions)

	// The two loops share a deadline so the run ends at one instant whichever
	// signal is slower to pace.
	deadline, cancel := context.WithTimeout(ctx, cfg.duration)
	defer cancel()

	// A rate of zero leaves that signal out entirely, which is what isolates
	// one pipeline when the question is about that pipeline alone.
	group, groupCtx := errgroup.WithContext(deadline)
	if cfg.tracesPerSecond > 0 {
		group.Go(func() error { return produceSpans(groupCtx, broker, cfg, runID, spans) })
	}
	if cfg.logMessagesPerSec > 0 {
		group.Go(func() error { return produceLogs(groupCtx, broker, cfg, runID, logs) })
	}
	if err := group.Wait(); err != nil && !errors.Is(err, context.DeadlineExceeded) {
		return err
	}

	// Flush on a context of its own: the run's deadline has passed, and records
	// already handed to the client still have to be acknowledged before the
	// tally means anything.
	flushCtx, endFlush := context.WithTimeout(context.Background(), flushTimeout)
	defer endFlush()
	if err := broker.flush(flushCtx); err != nil {
		return err
	}

	return writeReport(os.Stdout, runID, cfg, spans, logs)
}

// flushTimeout bounds the wait for outstanding acknowledgements. Generous: a
// report written before they land would undercount what was accepted, which is
// the one direction that fakes a loss.
const flushTimeout = 60 * time.Second

// produceSpans sends one trace per tick, round-robin across the topic's
// partitions so every partition of the group carries the run.
func produceSpans(ctx context.Context, broker *producer, cfg settings, runID string, into *tally) error {
	return pace(ctx, cfg.tracesPerSecond, func(sequence uint32) error {
		partition := int32(sequence % uint32(cfg.spanPartitions)) //nolint:gosec // partition counts are small
		payload, err := traceRequest(runID, partition, sequence, cfg.spansPerTrace, time.Now())
		if err != nil {
			return err
		}
		message, err := envelopeFor(cfg.tenantID, cfg.projectID, nexovuev1.Signal_SIGNAL_SPANS, payload)
		if err != nil {
			return err
		}
		broker.send(cfg.spansTopic, partition, message, into)
		return nil
	})
}

func produceLogs(ctx context.Context, broker *producer, cfg settings, runID string, into *tally) error {
	return pace(ctx, cfg.logMessagesPerSec, func(sequence uint32) error {
		partition := int32(sequence % uint32(cfg.logPartitions)) //nolint:gosec // partition counts are small
		first := sequence * uint32(cfg.logsPerMessage)           //nolint:gosec // record counts are small
		payload, err := logRequest(runID, partition, first, cfg.logsPerMessage, time.Now())
		if err != nil {
			return err
		}
		message, err := envelopeFor(cfg.tenantID, cfg.projectID, nexovuev1.Signal_SIGNAL_LOGS, payload)
		if err != nil {
			return err
		}
		broker.send(cfg.logsTopic, partition, message, into)
		return nil
	})
}

// pace calls send at a steady rate until the context ends, handing it a
// sequence number that never repeats within a run. A tick missed because the
// previous send was slow is skipped rather than queued: the report counts what
// the broker took, so a slower run measures the same thing.
func pace(ctx context.Context, perSecond int, send func(sequence uint32) error) error {
	ticker := time.NewTicker(time.Second / time.Duration(perSecond))
	defer ticker.Stop()

	var sequence uint32
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if err := send(sequence); err != nil {
				return err
			}
			sequence++
		}
	}
}

// report is the run's own half of the measurement: what the broker accepted,
// and the identities the stored rows are counted under.
type report struct {
	Run             string `json:"run"`
	SpanService     string `json:"span_service"`
	LogService      string `json:"log_service"`
	SpanMessages    int64  `json:"span_messages_accepted"`
	SpanMessagesBad int64  `json:"span_messages_failed"`
	SpansAccepted   int64  `json:"spans_accepted"`
	LogMessages     int64  `json:"log_messages_accepted"`
	LogMessagesBad  int64  `json:"log_messages_failed"`
	LogsAccepted    int64  `json:"logs_accepted"`
	SpanPartitions  int    `json:"span_partitions"`
	LogPartitions   int    `json:"log_partitions"`
}

func writeReport(to *os.File, runID string, cfg settings, spans, logs *tally) error {
	body := report{
		Run:             runID,
		SpanService:     spanService(runID),
		LogService:      logService(runID),
		SpanMessages:    spans.messagesAccepted.Load(),
		SpanMessagesBad: spans.messagesFailed.Load(),
		SpansAccepted:   spans.rowsAccepted.Load(),
		LogMessages:     logs.messagesAccepted.Load(),
		LogMessagesBad:  logs.messagesFailed.Load(),
		LogsAccepted:    logs.rowsAccepted.Load(),
		SpanPartitions:  cfg.spanPartitions,
		LogPartitions:   cfg.logPartitions,
	}
	encoder := json.NewEncoder(to)
	encoder.SetIndent("", "  ")
	return encoder.Encode(body)
}

func readSettings() (settings, error) {
	var cfg settings
	var brokers string

	flag.StringVar(&brokers, "brokers", "localhost:19092", "comma-separated broker list")
	flag.StringVar(&cfg.tenantID, "tenant", "", "tenant (organization) id the records belong to")
	flag.StringVar(&cfg.projectID, "project", "", "project id the records belong to")
	flag.StringVar(&cfg.spansTopic, "spans-topic", "spans-raw", "topic the span envelopes go to")
	flag.StringVar(&cfg.logsTopic, "logs-topic", "logs-raw", "topic the log envelopes go to")
	flag.IntVar(&cfg.spanPartitions, "span-partitions", 12, "partitions the span topic has")
	flag.IntVar(&cfg.logPartitions, "log-partitions", 4, "partitions the log topic has")
	flag.DurationVar(&cfg.duration, "duration", 5*time.Minute, "how long to produce for")
	flag.IntVar(&cfg.tracesPerSecond, "traces-per-second", 20, "span envelopes per second; 0 sends no spans")
	flag.IntVar(&cfg.spansPerTrace, "spans-per-trace", 5, "spans in one envelope")
	flag.IntVar(&cfg.logMessagesPerSec, "log-messages-per-second", 20, "log envelopes per second; 0 sends no logs")
	flag.IntVar(&cfg.logsPerMessage, "logs-per-message", 10, "log records in one envelope")
	flag.Parse()

	cfg.brokers = splitBrokers(brokers)

	var problems []error
	if len(cfg.brokers) == 0 {
		problems = append(problems, errors.New("-brokers is required"))
	}
	if cfg.tenantID == "" {
		problems = append(problems, errors.New("-tenant is required"))
	}
	if cfg.projectID == "" {
		problems = append(problems, errors.New("-project is required"))
	}
	for name, value := range map[string]int{
		"-span-partitions": cfg.spanPartitions, "-log-partitions": cfg.logPartitions,
		"-spans-per-trace": cfg.spansPerTrace, "-logs-per-message": cfg.logsPerMessage,
	} {
		if value <= 0 {
			problems = append(problems, fmt.Errorf("%s is %d, want more than 0", name, value))
		}
	}
	for name, rate := range map[string]int{
		"-traces-per-second": cfg.tracesPerSecond, "-log-messages-per-second": cfg.logMessagesPerSec,
	} {
		if rate < 0 {
			problems = append(problems, fmt.Errorf("%s is %d, want zero or more", name, rate))
		}
	}
	if cfg.tracesPerSecond == 0 && cfg.logMessagesPerSec == 0 {
		problems = append(problems, errors.New("both rates are zero, so there is nothing to produce"))
	}
	if cfg.duration <= 0 {
		problems = append(problems, fmt.Errorf("-duration is %s, want more than 0", cfg.duration))
	}
	return cfg, errors.Join(problems...)
}

func splitBrokers(raw string) []string {
	var brokers []string
	for _, broker := range strings.Split(raw, ",") {
		if trimmed := strings.TrimSpace(broker); trimmed != "" {
			brokers = append(brokers, trimmed)
		}
	}
	return brokers
}
