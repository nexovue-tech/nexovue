// Package usage counts what each tenant ingested, per day, into the control
// plane's `usage_daily` table. Nothing else writes that table, so without this
// the usage screen reports zeros and no bill has a basis.
//
// It counts what ARRIVED, not what was stored. The job consumes the ingestion
// topics themselves, which sit before tail sampling, so a tenant whose healthy
// traces are nine-tenths discarded is still metered on everything they sent.
// That is what the control plane already claims these counters mean —
// apps/api/src/nexovue_api/usage/router.py says re-counting telemetry would
// report "what survived sampling and retention rather than what arrived" — and
// what the pipeline invariant in apps/workers/CLAUDE.md says about deriving
// before sampling. Counting at the ClickHouse sink instead would meter roughly
// a seventh of a customer's traces, in the customer's favour.
//
// It is its own consumer group per topic rather than a stage inside the three
// write pipelines, so a slow control plane cannot slow ingest: the meter falls
// behind on its own offsets and catches up.
package usage

import (
	"fmt"
	"time"

	logsvc "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metricsvc "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// Counts is what one message carried, in the four units `usage_daily` records.
//
// Bytes is the OTLP payload the customer sent, not the Kafka record: the
// envelope around it is our own metadata, and metering a customer on our
// wrapper would meter our own overhead. It is the same measurement ADR 0056
// costed the pricing model against — bytes on the ingestion topic.
type Counts struct {
	Spans        int64
	Logs         int64
	MetricPoints int64
	Bytes        int64
}

// add folds one message's counts into a running total.
func (c *Counts) add(other Counts) {
	c.Spans += other.Spans
	c.Logs += other.Logs
	c.MetricPoints += other.MetricPoints
	c.Bytes += other.Bytes
}

// empty reports whether anything was counted, which is what lets a flush skip
// a tenant that produced nothing.
func (c Counts) empty() bool {
	return c.Spans == 0 && c.Logs == 0 && c.MetricPoints == 0 && c.Bytes == 0
}

// Signal is one ingestion topic this job meters: which envelope it accepts,
// which counter the payload feeds, and where the topic's settings live.
//
// A value rather than a name in a switch, so adding a fourth signal is adding a
// var and nothing else.
type Signal struct {
	// Name is the pipeline name in every log line this job writes.
	Name string

	wire     nexovuev1.Signal
	topic    func(config.Config) topic
	countOne func(payload []byte) (Counts, error)
}

// topic is one signal's broker settings, read from the shared worker config.
type topic struct {
	name          string
	consumerGroup string
	dlqTopic      string
	maxRows       int
	maxDelay      time.Duration
}

// usageGroupSuffix keeps this job's consumer group distinct from the group that
// writes the same topic to ClickHouse. Two groups read every message: one to
// store it, one to meter it.
const usageGroupSuffix = "-usage"

// The three signals a tenant can send us. Their names are log fields rather
// than WORKER_PIPELINES values — see Options in pipeline.go for why this job
// has no name of its own yet.
var (
	// Spans meters trace ingestion, before tail sampling discards any of it.
	Spans = Signal{
		Name: "usage-counting-spans",
		wire: nexovuev1.Signal_SIGNAL_SPANS,
		topic: func(cfg config.Config) topic {
			return topic{
				name:          cfg.Spans.Topic,
				consumerGroup: cfg.Spans.ConsumerGroup + usageGroupSuffix,
				dlqTopic:      cfg.Spans.DLQTopic,
				maxRows:       cfg.Spans.MaxRows,
				maxDelay:      cfg.Spans.MaxDelay,
			}
		},
		countOne: countSpans,
	}

	// Logs meters log ingestion. Nothing discards logs, so stored and received
	// agree here; the meter reads the topic anyway, so all three signals are
	// counted at one place and one time.
	Logs = Signal{
		Name: "usage-counting-logs",
		wire: nexovuev1.Signal_SIGNAL_LOGS,
		topic: func(cfg config.Config) topic {
			return topic{
				name:          cfg.Logs.Topic,
				consumerGroup: cfg.Logs.ConsumerGroup + usageGroupSuffix,
				dlqTopic:      cfg.Logs.DLQTopic,
				maxRows:       cfg.Logs.MaxRows,
				maxDelay:      cfg.Logs.MaxDelay,
			}
		},
		countOne: countLogs,
	}

	// Metrics meters customer-sent OTLP metrics, counted in data points because
	// that is the unit the five metric tables store a row per (ADR 0066).
	Metrics = Signal{
		Name: "usage-counting-metrics",
		wire: nexovuev1.Signal_SIGNAL_METRICS,
		topic: func(cfg config.Config) topic {
			return topic{
				name:          cfg.MetricIngest.Topic,
				consumerGroup: cfg.MetricIngest.ConsumerGroup + usageGroupSuffix,
				dlqTopic:      cfg.MetricIngest.DLQTopic,
				maxRows:       cfg.MetricIngest.MaxRows,
				maxDelay:      cfg.MetricIngest.MaxDelay,
			}
		},
		countOne: countMetricPoints,
	}
)

// AllSignals is every topic a full worker meters.
var AllSignals = []Signal{Spans, Logs, Metrics}

// Topic is the ingestion topic this signal is metered from. The store is
// keyed by it, so the caller that builds one needs the name.
func (s Signal) Topic(cfg config.Config) string { return s.topic(cfg).name }

// meter decodes one message and returns who it belonged to and what it carried.
// Every failure is a *consume.Rejection, so a caller reporting one always knows
// which tenant's message it could not read.
func (s Signal) meter(message []byte) (*nexovuev1.Envelope, Counts, error) {
	envelope, err := consume.DecodeEnvelope(message, s.wire)
	if err != nil {
		return nil, Counts{}, err
	}
	counts, err := s.countOne(envelope.GetPayload())
	if err != nil {
		return nil, Counts{}, consume.RejectedBy(envelope, err)
	}
	return envelope, counts, nil
}

// countSpans counts the spans in one OTLP trace export.
func countSpans(payload []byte) (Counts, error) {
	var request tracesvc.ExportTraceServiceRequest
	if err := proto.Unmarshal(payload, &request); err != nil {
		return Counts{}, fmt.Errorf("decode OTLP payload: %w", err)
	}

	counts := Counts{Bytes: int64(len(payload))}
	for _, resource := range request.GetResourceSpans() {
		for _, scope := range resource.GetScopeSpans() {
			counts.Spans += int64(len(scope.GetSpans()))
		}
	}
	return counts, nil
}

// countLogs counts the log records in one OTLP log export.
func countLogs(payload []byte) (Counts, error) {
	var request logsvc.ExportLogsServiceRequest
	if err := proto.Unmarshal(payload, &request); err != nil {
		return Counts{}, fmt.Errorf("decode OTLP payload: %w", err)
	}

	counts := Counts{Bytes: int64(len(payload))}
	for _, resource := range request.GetResourceLogs() {
		for _, scope := range resource.GetScopeLogs() {
			counts.Logs += int64(len(scope.GetLogRecords()))
		}
	}
	return counts, nil
}

// countMetricPoints counts the data points in one OTLP metric export.
//
// A point, not a metric: one metric carries a point per attribute combination,
// and the point is what becomes a stored row. Counting metrics instead would
// meter a thousand-series histogram the same as a single gauge.
func countMetricPoints(payload []byte) (Counts, error) {
	var request metricsvc.ExportMetricsServiceRequest
	if err := proto.Unmarshal(payload, &request); err != nil {
		return Counts{}, fmt.Errorf("decode OTLP payload: %w", err)
	}

	counts := Counts{Bytes: int64(len(payload))}
	for _, resource := range request.GetResourceMetrics() {
		for _, scope := range resource.GetScopeMetrics() {
			for _, metric := range scope.GetMetrics() {
				counts.MetricPoints += pointsIn(metric)
			}
		}
	}
	return counts, nil
}

// pointsIn counts one metric's data points across every OTLP shape. An unknown
// shape counts zero rather than guessing: metric-ingest cannot store it either,
// so metering it would charge for something we never wrote.
func pointsIn(metric *metricspb.Metric) int64 {
	switch data := metric.GetData().(type) {
	case *metricspb.Metric_Gauge:
		return int64(len(data.Gauge.GetDataPoints()))
	case *metricspb.Metric_Sum:
		return int64(len(data.Sum.GetDataPoints()))
	case *metricspb.Metric_Histogram:
		return int64(len(data.Histogram.GetDataPoints()))
	case *metricspb.Metric_ExponentialHistogram:
		return int64(len(data.ExponentialHistogram.GetDataPoints()))
	case *metricspb.Metric_Summary:
		return int64(len(data.Summary.GetDataPoints()))
	default:
		return 0
	}
}
