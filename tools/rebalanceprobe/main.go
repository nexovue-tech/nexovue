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
//
// # Recording a run, before you clean up
//
// Capture the numbers to files first. The cleanup below is irreversible, and a
// figure nobody wrote down cannot be recovered from a deleted row.
//
// This is a repair, not a precaution. The 2026-09-05 counter measurement left
// its `service_edges_1m` figure disputed — +13 by subtraction against the +14 a
// reviewer derives from `+70 spans / 5 spans-per-trace` — and neither can now be
// settled, because the output was never saved and the rows were deleted here.
// ADR 0094 records the disagreement in the place it should have recorded an
// answer.
//
// The report is JSON on stdout and the progress line goes to stderr, so one
// redirect captures what was produced and nothing else:
//
//	go run ./tools/rebalanceprobe -duration 6m -spans-topic spans-probe > <run>-produced.json
//
// Then the other side of the subtraction — what was stored — under the same
// predicates as the DELETEs below, so the rows counted and the rows removed
// cannot drift apart:
//
//	SELECT 'spans' AS what, count() AS n FROM spans WHERE service_name = 'nvprobe-spans-<run>'
//	UNION ALL SELECT 'logs', count() FROM logs WHERE service_name = 'nvprobe-logs-<run>'
//	UNION ALL SELECT 'trace_summaries', count() FROM trace_summaries WHERE startsWith(trace_id, '<run>')
//	UNION ALL SELECT 'span_trace_lookup', count() FROM span_trace_lookup WHERE startsWith(trace_id, '<run>')
//	UNION ALL SELECT 'endpoint_stats_1m.request_count', sum(request_count) FROM endpoint_stats_1m WHERE service_name = 'nvprobe-spans-<run>'
//	UNION ALL SELECT 'service_edges_1m.call_count', sum(call_count) FROM service_edges_1m WHERE client_service = 'nvprobe-spans-<run>' OR server_service = 'nvprobe-spans-<run>'
//	UNION ALL SELECT 'metrics_sum', count() FROM metrics_sum WHERE service_name = 'nvprobe-spans-<run>'
//
// One UNION ALL and no ORDER BY, deliberately: ClickHouse applies a trailing
// ORDER BY to the last branch alone, not to the union, and fails on the alias.
//
// Where a figure is a delta, take it once before the arm and once after and keep
// both. `usage_daily` needs that most, because it cannot be separated from real
// traffic — its baseline is knowable only before the run.
//
// # Cleaning up after a run
//
// A run leaves rows in three places, and the third one is billable — the usage
// pipeline records every distinct service name it meters, so each run adds two
// rows to a customer's service count:
//
//	-- ClickHouse: every trace id starts with the run id, so no run touches another
//	DELETE FROM spans            WHERE service_name = 'nvprobe-spans-<run>';
//	DELETE FROM logs             WHERE service_name = 'nvprobe-logs-<run>';
//	DELETE FROM trace_summaries  WHERE startsWith(trace_id, '<run>');
//	DELETE FROM span_trace_lookup WHERE startsWith(trace_id, '<run>');
//	DELETE FROM endpoint_stats_1m WHERE service_name = 'nvprobe-spans-<run>';
//	DELETE FROM service_edges_1m WHERE client_service = 'nvprobe-spans-<run>'
//	                                OR server_service = 'nvprobe-spans-<run>';
//	DELETE FROM metrics_sum      WHERE service_name = 'nvprobe-spans-<run>';
//	-- Postgres: the billable one
//	DELETE FROM usage_daily_services WHERE service_name LIKE 'nvprobe-%';
//	DELETE FROM usage_ingest_offsets WHERE topic = '<topic>';
//
// Run against a topic of its own — `-spans-topic`, with the workers pointed at
// it by `KAFKA_SPANS_TOPIC` and `WORKERS_CONSUMER_GROUP` — and the watermark
// tables need clearing too, keyed by that topic. They are KeeperMap, which
// refuses a lightweight DELETE; `ALTER TABLE … DELETE` is what works:
//
//	ALTER TABLE span_written_offsets DELETE WHERE startsWith(partition_key, '<topic>');
//	ALTER TABLE span_ingest_offsets  DELETE WHERE startsWith(partition_key, '<topic>');
//
// `usage_daily` sums bytes per tenant and day and cannot be separated from real
// traffic, so produce against a tenant whose usage numbers nobody is reading.
//
// Every statement above was run on 2026-09-01 to clear five runs, and each
// table verified back to zero.
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
		message, payloadBytes, err := envelopeFor(cfg.tenantID, cfg.projectID, nexovuev1.Signal_SIGNAL_SPANS, payload)
		if err != nil {
			return err
		}
		broker.send(cfg.spansTopic, partition, message, payloadBytes, into)
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
		message, payloadBytes, err := envelopeFor(cfg.tenantID, cfg.projectID, nexovuev1.Signal_SIGNAL_LOGS, payload)
		if err != nil {
			return err
		}
		broker.send(cfg.logsTopic, partition, message, payloadBytes, into)
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
// and the identities the stored rows are counted under. The payload bytes are
// the denominator for `usage_daily.bytes`, which is what the free tier ends on
// (ADR 0083) — a rebalance that over-counts them over-bills.
//
// Each counter stage gets its own denominator by name rather than leaving the
// reader to work it out: the shape of a probe trace (one CLIENT, the rest
// SERVERs) means every accepted span is `metrics_sum`'s, the caller alone is
// `service_edges_1m`'s, and the SERVERs are `endpoint_stats_1m`'s.
type report struct {
	Run                 string `json:"run"`
	SpanService         string `json:"span_service"`
	LogService          string `json:"log_service"`
	PeerService         string `json:"peer_service"`
	SpanMessages        int64  `json:"span_messages_accepted"`
	SpanMessagesBad     int64  `json:"span_messages_failed"`
	SpansAccepted       int64  `json:"spans_accepted"`
	ServerSpansAccepted int64  `json:"server_spans_accepted"`
	EdgeCallsAccepted   int64  `json:"edge_calls_accepted"`
	SpanPayloadBytes    int64  `json:"span_payload_bytes_accepted"`
	LogMessages         int64  `json:"log_messages_accepted"`
	LogMessagesBad      int64  `json:"log_messages_failed"`
	LogsAccepted        int64  `json:"logs_accepted"`
	LogPayloadBytes     int64  `json:"log_payload_bytes_accepted"`
	SpanPartitions      int    `json:"span_partitions"`
	LogPartitions       int    `json:"log_partitions"`
}

// reportFor builds the run's report from what the broker acknowledged.
func reportFor(runID string, cfg settings, spans, logs *tally) report {
	// One span of every accepted trace is its caller, so the accepted messages
	// are the edge denominator and what is left over is the endpoint rollup's.
	callers := spans.messagesAccepted.Load()
	return report{
		Run:                 runID,
		SpanService:         spanService(runID),
		LogService:          logService(runID),
		PeerService:         peerService(runID),
		SpanMessages:        callers,
		SpanMessagesBad:     spans.messagesFailed.Load(),
		SpansAccepted:       spans.rowsAccepted.Load(),
		ServerSpansAccepted: spans.rowsAccepted.Load() - callers,
		EdgeCallsAccepted:   callers,
		SpanPayloadBytes:    spans.payloadBytesAccepted.Load(),
		LogMessages:         logs.messagesAccepted.Load(),
		LogMessagesBad:      logs.messagesFailed.Load(),
		LogsAccepted:        logs.rowsAccepted.Load(),
		LogPayloadBytes:     logs.payloadBytesAccepted.Load(),
		SpanPartitions:      cfg.spanPartitions,
		LogPartitions:       cfg.logPartitions,
	}
}

func writeReport(to *os.File, runID string, cfg settings, spans, logs *tally) error {
	encoder := json.NewEncoder(to)
	encoder.SetIndent("", "  ")
	return encoder.Encode(reportFor(runID, cfg, spans, logs))
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
