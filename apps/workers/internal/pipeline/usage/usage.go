// Package usage meters what each tenant ingested, per day, into the control
// plane. Two tables, because a bill and a guardrail are different numbers:
// `usage_daily` holds the counters — spans, logs, points, bytes — and
// `usage_daily_services` holds the billing unit the pricing model chose, the
// distinct `service.name` a project sent something for on a day. Nothing else
// writes either, so without this the usage screen reports zeros and no bill has
// a basis.
//
// It counts what ARRIVED, not what was stored. The job consumes the ingestion
// topics themselves, which sit before tail sampling, so a tenant whose healthy
// traces are nine-tenths discarded is still metered on everything they sent.
// That is what the control plane already claims these counters mean —
// apps/api/src/nexovue_api/usage/router.py says re-counting telemetry would
// report "what survived sampling and retention rather than what arrived" — and
// it is the same rule that makes the pipeline derive counters before sampling.
// Counting at the ClickHouse sink instead would meter roughly
// a seventh of a customer's traces, in the customer's favour.
//
// It is its own consumer group per topic rather than a stage inside the three
// write pipelines, so a slow control plane cannot slow ingest: the meter falls
// behind on its own offsets and catches up.
package usage

import (
	"fmt"
	"time"
	"unicode/utf8"

	logsvc "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metricsvc "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// Counts is what one message carried, in the units `usage_daily` records.
//
// Bytes is the OTLP payload the customer sent, not the Kafka record: the
// envelope around it is our own metadata, and metering a customer on our
// wrapper would meter our own overhead. It is the same measurement the pricing
// model was costed against — bytes on the ingestion topic.
//
// BrowserBytes is the part of Bytes that came through the browser door, which
// the plan's allowance budgets on its own: a public site key is
// printed in the customer's page, so anyone can spend the browser's share, and
// the door keeps that spending out of the server's share only if the meter
// records which door each byte came through.
type Counts struct {
	Spans        int64
	Logs         int64
	MetricPoints int64
	Bytes        int64
	BrowserBytes int64

	// ProfileSamples is metered and shown, never billed while profiling is in
	// preview (F17, D1): a profiles message adds nothing to Bytes, which is what
	// the allowance spends, and names no service, which is what the plan prices.
	ProfileSamples int64
}

// add folds one message's counts into a running total.
func (c *Counts) add(other Counts) {
	c.Spans += other.Spans
	c.Logs += other.Logs
	c.MetricPoints += other.MetricPoints
	c.Bytes += other.Bytes
	c.BrowserBytes += other.BrowserBytes
	c.ProfileSamples += other.ProfileSamples
}

// empty reports whether anything was counted, which is what lets a flush skip
// a tenant that produced nothing.
func (c Counts) empty() bool {
	return c.Spans == 0 && c.Logs == 0 && c.MetricPoints == 0 && c.Bytes == 0 &&
		c.ProfileSamples == 0
}

// Metered is one message: the five guardrail counters, and the services that
// carried them. The services are the bill — the plan prices per service
// monitored — and the counters are what shows the volume behind it.
type Metered struct {
	Counts

	// Services is the distinct `service.name` of every resource in this payload
	// that carried at least one span, log or point. A slice rather than a map:
	// a payload holds a handful of resources, and a map per message would
	// allocate on the hottest path this job has.
	Services []string
}

// UnnamedService is the service a resource carrying no `service.name` is billed
// as. Every one of them folds into this single bucket, so a sender that omits
// the attribute is one service rather than one per message — and parentheses
// are impossible in a real `service.name`, so it cannot collide with one. Same
// spelling as serviceedges.UnknownService, for the same reason.
const UnnamedService = "(unknown)"

// attrServiceName is the OTLP resource attribute the billing unit is counted
// from. Same key metricwriting promotes to its `service_name` column.
const attrServiceName = "service.name"

// maxServiceNameChars matches `usage_daily_services.service_name`, which is a
// varchar(255) — characters, not bytes, which is why the truncation below
// counts runes. OTLP puts no limit on an attribute value, so something has to.
const maxServiceNameChars = 255

// record adds a service to the set this message carried, ignoring one already
// there. Linear because the set is tiny; see the note on Services.
func (m *Metered) record(name string) {
	for _, seen := range m.Services {
		if seen == name {
			return
		}
	}
	m.Services = append(m.Services, name)
}

// attribute records a resource's service, but only if that resource actually
// carried something. A resource block with no spans in it is an empty export,
// not a service the customer ran: billing for one would charge for silence.
func (m *Metered) attribute(resource *resourcepb.Resource, carried int64) {
	if carried == 0 {
		return
	}
	m.record(serviceNameOf(resource))
}

// serviceNameOf is the billable name of one resource: its `service.name`,
// folded into UnnamedService when absent and truncated to what the column
// holds.
func serviceNameOf(resource *resourcepb.Resource) string {
	name := otlpvalue.StringOrEmpty(resource.GetAttributes(), attrServiceName)
	if name == "" {
		return UnnamedService
	}
	if utf8.RuneCountInString(name) > maxServiceNameChars {
		return string([]rune(name)[:maxServiceNameChars])
	}
	return name
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
	countOne func(payload []byte) (Metered, error)
}

// topic is one signal's broker settings, read from the shared worker config.
type topic struct {
	name          string
	consumerGroup string
	dlqTopic      string
	// Messages, not rows: a meter counts what arrived, not what was stored.
	maxMessages int
	// maxBytes bounds one fetch; zero takes the consume package's default.
	maxBytes int
	maxDelay time.Duration
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
				maxMessages:   cfg.Spans.MaxMessages,
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
				maxMessages:   cfg.Logs.MaxMessages,
				maxDelay:      cfg.Logs.MaxDelay,
			}
		},
		countOne: countLogs,
	}

	// Metrics meters customer-sent OTLP metrics, counted in data points because
	// that is the unit the five metric tables store a row per.
	Metrics = Signal{
		Name: "usage-counting-metrics",
		wire: nexovuev1.Signal_SIGNAL_METRICS,
		topic: func(cfg config.Config) topic {
			return topic{
				name:          cfg.MetricIngest.Topic,
				consumerGroup: cfg.MetricIngest.ConsumerGroup + usageGroupSuffix,
				dlqTopic:      cfg.MetricIngest.DLQTopic,
				maxMessages:   cfg.MetricIngest.MaxMessages,
				maxDelay:      cfg.MetricIngest.MaxDelay,
			}
		},
		countOne: countMetricPoints,
	}
)

// Profiles meters the profiles the receiver converted, counted in samples — and
// only counted (F17, D1). The eBPF profiler profiles a whole host, so every
// process on it arrives as a "service": charging bytes or naming services here
// would bill a customer for their machine's system processes.
var Profiles = Signal{
	Name: "usage-counting-profiles",
	wire: nexovuev1.Signal_SIGNAL_PROFILES,
	topic: func(cfg config.Config) topic {
		return topic{
			name:          cfg.ProfileIngest.Topic,
			consumerGroup: cfg.ProfileIngest.ConsumerGroup + usageGroupSuffix,
			dlqTopic:      cfg.ProfileIngest.DLQTopic,
			maxMessages:   cfg.ProfileIngest.MaxMessages,
			maxBytes:      cfg.ProfileIngest.MaxBytes,
			maxDelay:      cfg.ProfileIngest.MaxDelay,
		}
	},
	countOne: countProfileSamples,
}

// AllSignals is every topic a full worker meters.
var AllSignals = []Signal{Spans, Logs, Metrics, Profiles}

// Topic is the ingestion topic this signal is metered from. The store is
// keyed by it, so the caller that builds one needs the name.
func (s Signal) Topic(cfg config.Config) string { return s.topic(cfg).name }

// ConsumerGroup is the group this signal's meter reads its topic as.
func (s Signal) ConsumerGroup(cfg config.Config) string { return s.topic(cfg).consumerGroup }

// DeadLetterTopic is where this signal's meter parks what it cannot read.
func (s Signal) DeadLetterTopic(cfg config.Config) string { return s.topic(cfg).dlqTopic }

// meter decodes one message and returns who it belonged to and what it carried.
// Every failure is a *consume.Rejection, so a caller reporting one always knows
// which tenant's message it could not read.
func (s Signal) meter(message []byte) (*nexovuev1.Envelope, Metered, error) {
	envelope, err := consume.DecodeEnvelope(message, s.wire)
	if err != nil {
		return nil, Metered{}, err
	}
	metered, err := s.countOne(envelope.GetPayload())
	if err != nil {
		return nil, Metered{}, consume.RejectedBy(envelope, err)
	}
	metered.BrowserBytes = browserBytesOf(envelope.GetDoor(), metered.Bytes)
	return envelope, metered, nil
}

// browserBytesOf is how much of a message's payload the browser door's budget
// is charged: all of it when the envelope names the browser door, none
// otherwise. Every other entrance — the server door, the scheduler's doors, and
// an envelope that names none — is the server's, because a byte the browser
// door cannot spend is what the split protects.
func browserBytesOf(door nexovuev1.Door, payloadBytes int64) int64 {
	if door == nexovuev1.Door_DOOR_RUM {
		return payloadBytes
	}
	return 0
}

// countSpans counts the spans in one OTLP trace export, and the services they
// came from.
func countSpans(payload []byte) (Metered, error) {
	var request tracesvc.ExportTraceServiceRequest
	if err := proto.Unmarshal(payload, &request); err != nil {
		return Metered{}, fmt.Errorf("decode OTLP payload: %w", err)
	}

	metered := Metered{Counts: Counts{Bytes: int64(len(payload))}}
	for _, resource := range request.GetResourceSpans() {
		var spans int64
		for _, scope := range resource.GetScopeSpans() {
			spans += int64(len(scope.GetSpans()))
		}
		metered.Spans += spans
		metered.attribute(resource.GetResource(), spans)
	}
	return metered, nil
}

// countLogs counts the log records in one OTLP log export, and the services
// they came from. A service that only ever logs is still a service monitored.
func countLogs(payload []byte) (Metered, error) {
	var request logsvc.ExportLogsServiceRequest
	if err := proto.Unmarshal(payload, &request); err != nil {
		return Metered{}, fmt.Errorf("decode OTLP payload: %w", err)
	}

	metered := Metered{Counts: Counts{Bytes: int64(len(payload))}}
	for _, resource := range request.GetResourceLogs() {
		var records int64
		for _, scope := range resource.GetScopeLogs() {
			records += int64(len(scope.GetLogRecords()))
		}
		metered.Logs += records
		metered.attribute(resource.GetResource(), records)
	}
	return metered, nil
}

// countProfileSamples counts samples and nothing else: no bytes, no services (see
// Profiles).
func countProfileSamples(payload []byte) (Metered, error) {
	var samples nexovuev1.ProfileSamples
	if err := proto.Unmarshal(payload, &samples); err != nil {
		return Metered{}, fmt.Errorf("decode ProfileSamples payload: %w", err)
	}
	var metered Metered
	for _, batch := range samples.GetBatches() {
		metered.ProfileSamples += int64(len(batch.GetSamples()))
	}
	return metered, nil
}

// countMetricPoints counts the data points in one OTLP metric export.
//
// A point, not a metric: one metric carries a point per attribute combination,
// and the point is what becomes a stored row. Counting metrics instead would
// meter a thousand-series histogram the same as a single gauge.
func countMetricPoints(payload []byte) (Metered, error) {
	var request metricsvc.ExportMetricsServiceRequest
	if err := proto.Unmarshal(payload, &request); err != nil {
		return Metered{}, fmt.Errorf("decode OTLP payload: %w", err)
	}

	metered := Metered{Counts: Counts{Bytes: int64(len(payload))}}
	for _, resource := range request.GetResourceMetrics() {
		var points int64
		for _, scope := range resource.GetScopeMetrics() {
			for _, metric := range scope.GetMetrics() {
				points += pointsIn(metric)
			}
		}
		metered.MetricPoints += points
		metered.attribute(resource.GetResource(), points)
	}
	return metered, nil
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
