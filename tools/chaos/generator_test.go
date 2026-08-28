//go:build chaos

package chaos

// The log and metric load used by TestNoAcknowledgedLogOrMetricIsEverLost.
//
// It speaks OTLP/JSON rather than protobuf so this module keeps the empty
// dependency list that lets it run against any build of the stack: the receiver
// reads both encodings (internal/otlp/codec.go), and JSON is the one
// encoding/json can write. Every export carries the ingest key, so the auth and
// rate-limit gates are inside the measured path exactly as they are for spans.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const (
	receiverURL     = "http://localhost:14318"
	ingestKeyHeader = "x-nexovue-key"
	// The dev stack's ingest key, published in the seed data and in
	// tools/loadgen's defaults.
	ingestKey = "nvk_live_devlocaldevlocaldevloc" //nolint:gosec // dev-stack default

	contentTypeJSON = "application/json"

	// exportTimeout bounds one export so a wedged receiver cannot hang the run.
	// Longer than the receiver's 5s produce timeout, so a refusal is read as a
	// refusal rather than counted as unanswered.
	exportTimeout = 15 * time.Second
)

// The resource attribute the store promotes to a column, and the point
// attribute that identifies one generated record. Neither can be masked: no
// default rule matches the key, and the values carry no digit run long enough
// to be read as a payment card.
const (
	attrServiceName = "service.name"
	attrSequence    = "chaos_seq"
)

// verdict is what the receiver said about one export.
type verdict int

const (
	// answerAccepted: 2xx. The broker holds the batch, so every record in it
	// must become queryable.
	answerAccepted verdict = iota
	// answerRefused: non-2xx. Offered and not taken — the invariant working,
	// not a loss.
	answerRefused
	// answerUnread: no readable reply. It may have been published all the same,
	// so it bounds the excess rather than the shortfall.
	answerUnread
)

// tally is what the receiver answered for the records one door was offered,
// counted in identities rather than requests.
type tally struct {
	mu           sync.Mutex
	acknowledged int
	refused      int
	unanswered   int
}

func (t *tally) record(items int, answer verdict) {
	t.mu.Lock()
	defer t.mu.Unlock()
	switch answer {
	case answerAccepted:
		t.acknowledged += items
	case answerRefused:
		t.refused += items
	case answerUnread:
		t.unanswered += items
	}
}

// offered is everything handed to the receiver, however it answered. It is the
// ceiling on what may legitimately be in the store for this run.
func (t *tally) offered() int {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.acknowledged + t.refused + t.unanswered
}

// accepted is what the receiver said it had taken. Every one of these must be
// in the store, which is the whole of the assertion.
func (t *tally) accepted() int {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.acknowledged
}

func (t *tally) describe() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	return fmt.Sprintf("%d acknowledged, %d refused, %d unanswered",
		t.acknowledged, t.refused, t.unanswered)
}

// door is one OTLP/HTTP endpoint under load.
//
// idsPerRequest counts identities, not rows: a log export carries that many
// records, and a metric export carries that many *per shape*, so one accepted
// identity is one row owed by each of the five metric tables.
type door struct {
	path          string
	idsPerRequest int
	payload       func(service string, firstID int) []byte
}

var logsDoor = door{
	path:          "/v1/logs",
	idsPerRequest: logRecordsPerExport,
	payload:       logsPayload,
}

var metricsDoor = door{
	path:          "/v1/metrics",
	idsPerRequest: pointsPerShapePerExport,
	payload:       metricsPayload,
}

// drive offers load at one door until ctx ends, and returns what the receiver
// answered.
//
// It never retries a refused export. The OTel SDKs do, and that is what makes
// the span test's overcount open-ended; here a refused batch is simply offered
// and not taken, and its identities are never reused.
func (d door) drive(
	ctx context.Context,
	t *testing.T,
	service string,
	workers int,
	interval time.Duration,
) *tally {
	t.Helper()

	counted := new(tally)
	var nextID atomic.Int64
	client := &http.Client{Timeout: exportTimeout}

	var running sync.WaitGroup
	for range workers {
		running.Add(1)
		go func() {
			defer running.Done()
			ticker := time.NewTicker(interval)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					first := int(nextID.Add(int64(d.idsPerRequest))) - d.idsPerRequest
					counted.record(d.idsPerRequest, d.export(client, service, first))
				}
			}
		}()
	}
	running.Wait()
	return counted
}

// export posts one batch. The request context is deliberately not the run's:
// an export already on the wire must be allowed to read its answer, or a
// refusal would be miscounted as unanswered at the end of the load.
func (d door) export(client *http.Client, service string, firstID int) verdict {
	body := d.payload(service, firstID)
	request, err := http.NewRequest(http.MethodPost, receiverURL+d.path, bytes.NewReader(body))
	if err != nil {
		return answerUnread
	}
	request.Header.Set("Content-Type", contentTypeJSON)
	request.Header.Set(ingestKeyHeader, ingestKey)

	response, err := client.Do(request)
	if err != nil {
		return answerUnread
	}
	defer func() { _ = response.Body.Close() }()
	_, _ = io.Copy(io.Discard, response.Body)

	if response.StatusCode == http.StatusOK {
		return answerAccepted
	}
	return answerRefused
}

// sequenceID is one generated record's identity. Eight digits keep every run
// well inside the shortest payment card the masking rules look for, so nothing
// the generator writes can be rewritten on its way to the store.
func sequenceID(prefix string, n int) string {
	return fmt.Sprintf("%s-%08d", prefix, n)
}

// ---------- OTLP/JSON, only the fields these payloads set ----------

type anyValue struct {
	StringValue string `json:"stringValue"`
}

type keyValue struct {
	Key   string   `json:"key"`
	Value anyValue `json:"value"`
}

type resource struct {
	Attributes []keyValue `json:"attributes"`
}

type scope struct {
	Name string `json:"name"`
}

const scopeName = "nexovue.chaos"

func serviceResource(service string) resource {
	return resource{Attributes: []keyValue{
		{Key: attrServiceName, Value: anyValue{StringValue: service}},
	}}
}

func sequenceAttrs(id string) []keyValue {
	return []keyValue{{Key: attrSequence, Value: anyValue{StringValue: id}}}
}

// unixNano renders a timestamp the way OTLP/JSON asks for a 64-bit integer.
func unixNano(at time.Time) string {
	return strconv.FormatInt(at.UnixNano(), 10)
}

// ---------- logs ----------

// logRecordsPerExport is how many records one export carries. The batch is
// small enough that the whole run stays well under the receiver's body limit
// and its 1,000 req/s rate limit.
const logRecordsPerExport = 25

// severityInfo is OTLP's SEVERITY_NUMBER_INFO.
const severityInfo = 9

type logRecord struct {
	TimeUnixNano   string     `json:"timeUnixNano"`
	SeverityNumber int        `json:"severityNumber"`
	SeverityText   string     `json:"severityText"`
	Body           anyValue   `json:"body"`
	Attributes     []keyValue `json:"attributes"`
}

type scopeLogs struct {
	Scope      scope       `json:"scope"`
	LogRecords []logRecord `json:"logRecords"`
}

type resourceLogs struct {
	Resource  resource    `json:"resource"`
	ScopeLogs []scopeLogs `json:"scopeLogs"`
}

type logsRequest struct {
	ResourceLogs []resourceLogs `json:"resourceLogs"`
}

// logsPayload renders one log export. The identity is the record body, which
// the store keeps verbatim in a plain column — so counting distinct bodies asks
// the store directly how many of our records it holds.
func logsPayload(service string, firstID int) []byte {
	now := unixNano(time.Now())
	records := make([]logRecord, 0, logRecordsPerExport)
	for offset := range logRecordsPerExport {
		id := sequenceID("log", firstID+offset)
		records = append(records, logRecord{
			TimeUnixNano:   now,
			SeverityNumber: severityInfo,
			SeverityText:   "INFO",
			Body:           anyValue{StringValue: "nexovue chaos log record " + id},
			Attributes:     sequenceAttrs(id),
		})
	}
	return mustEncode(logsRequest{ResourceLogs: []resourceLogs{{
		Resource:  serviceResource(service),
		ScopeLogs: []scopeLogs{{Scope: scope{Name: scopeName}, LogRecords: records}},
	}}})
}

// ---------- metrics ----------

// pointsPerShapePerExport is how many points of each shape one export carries.
// Five shapes travel together, so one export is five times this many points and
// fans out to all five tables from a single Kafka message.
const pointsPerShapePerExport = 5

// deltaTemporality is the only temporality these payloads declare. The writer
// stores it verbatim and converts nothing, so the choice is arbitrary — but it
// has to be a legal enum name or the export would be refused as malformed.
const deltaTemporality = "AGGREGATION_TEMPORALITY_DELTA"

// metricShape is one of the five OTLP point shapes, the metric that carries it
// and the table the writer sends it to. One export carries every shape, which
// is what makes each message a five-table, non-atomic write.
type metricShape struct {
	metric    string
	table     string
	seqPrefix string
}

var metricShapes = []metricShape{
	{"nexovue.chaos.gauge", "metrics_gauge", "gauge"},
	{"nexovue.chaos.sum", "metrics_sum", "sum"},
	{"nexovue.chaos.histogram", "metrics_histogram", "hist"},
	{"nexovue.chaos.exphistogram", "metrics_exp_histogram", "exph"},
	{"nexovue.chaos.summary", "metrics_summary", "summ"},
}

// shapeAt names the five shapes in the order metricsPayload builds them.
const (
	shapeGauge = iota
	shapeSum
	shapeHistogram
	shapeExpHistogram
	shapeSummary
)

type numberPoint struct {
	Attributes        []keyValue `json:"attributes"`
	StartTimeUnixNano string     `json:"startTimeUnixNano"`
	TimeUnixNano      string     `json:"timeUnixNano"`
	AsDouble          float64    `json:"asDouble"`
}

type gaugeShape struct {
	DataPoints []numberPoint `json:"dataPoints"`
}

type sumShape struct {
	DataPoints             []numberPoint `json:"dataPoints"`
	AggregationTemporality string        `json:"aggregationTemporality"`
	IsMonotonic            bool          `json:"isMonotonic"`
}

type histogramPoint struct {
	Attributes        []keyValue `json:"attributes"`
	StartTimeUnixNano string     `json:"startTimeUnixNano"`
	TimeUnixNano      string     `json:"timeUnixNano"`
	Count             string     `json:"count"`
	Sum               float64    `json:"sum"`
	BucketCounts      []string   `json:"bucketCounts"`
	ExplicitBounds    []float64  `json:"explicitBounds"`
	Min               float64    `json:"min"`
	Max               float64    `json:"max"`
}

type histogramShape struct {
	DataPoints             []histogramPoint `json:"dataPoints"`
	AggregationTemporality string           `json:"aggregationTemporality"`
}

type bucketRun struct {
	Offset       int      `json:"offset"`
	BucketCounts []string `json:"bucketCounts"`
}

type expHistogramPoint struct {
	Attributes        []keyValue `json:"attributes"`
	StartTimeUnixNano string     `json:"startTimeUnixNano"`
	TimeUnixNano      string     `json:"timeUnixNano"`
	Count             string     `json:"count"`
	Sum               float64    `json:"sum"`
	Scale             int        `json:"scale"`
	ZeroCount         string     `json:"zeroCount"`
	Positive          bucketRun  `json:"positive"`
	Negative          bucketRun  `json:"negative"`
	Min               float64    `json:"min"`
	Max               float64    `json:"max"`
}

type expHistogramShape struct {
	DataPoints             []expHistogramPoint `json:"dataPoints"`
	AggregationTemporality string              `json:"aggregationTemporality"`
}

type quantileValue struct {
	Quantile float64 `json:"quantile"`
	Value    float64 `json:"value"`
}

type summaryPoint struct {
	Attributes        []keyValue      `json:"attributes"`
	StartTimeUnixNano string          `json:"startTimeUnixNano"`
	TimeUnixNano      string          `json:"timeUnixNano"`
	Count             string          `json:"count"`
	Sum               float64         `json:"sum"`
	QuantileValues    []quantileValue `json:"quantileValues"`
}

type summaryShape struct {
	DataPoints []summaryPoint `json:"dataPoints"`
}

// metric carries exactly one shape; the others stay absent, which is how OTLP
// says which kind of metric this is.
type metric struct {
	Name                 string             `json:"name"`
	Unit                 string             `json:"unit"`
	Gauge                *gaugeShape        `json:"gauge,omitempty"`
	Sum                  *sumShape          `json:"sum,omitempty"`
	Histogram            *histogramShape    `json:"histogram,omitempty"`
	ExponentialHistogram *expHistogramShape `json:"exponentialHistogram,omitempty"`
	Summary              *summaryShape      `json:"summary,omitempty"`
}

type scopeMetrics struct {
	Scope   scope    `json:"scope"`
	Metrics []metric `json:"metrics"`
}

type resourceMetrics struct {
	Resource     resource       `json:"resource"`
	ScopeMetrics []scopeMetrics `json:"scopeMetrics"`
}

type metricsRequest struct {
	ResourceMetrics []resourceMetrics `json:"resourceMetrics"`
}

// metricsPayload renders one metric export carrying all five point shapes.
//
// Every point's identity is its single attribute, which the store keeps in the
// `attrs` JSON column — so counting distinct attribute documents per table asks
// each table how many of our points it holds, one table at a time. That is what
// the five-table, non-atomic write needs: a shortfall has to be attributable to
// the table it happened in.
func metricsPayload(service string, firstID int) []byte {
	now := unixNano(time.Now())
	ids := func(shape int) []string {
		numbered := make([]string, 0, pointsPerShapePerExport)
		for offset := range pointsPerShapePerExport {
			numbered = append(numbered, sequenceID(metricShapes[shape].seqPrefix, firstID+offset))
		}
		return numbered
	}

	gaugePoints := make([]numberPoint, 0, pointsPerShapePerExport)
	for _, id := range ids(shapeGauge) {
		gaugePoints = append(gaugePoints, numberPoint{
			Attributes: sequenceAttrs(id), StartTimeUnixNano: now, TimeUnixNano: now, AsDouble: 1,
		})
	}
	sumPoints := make([]numberPoint, 0, pointsPerShapePerExport)
	for _, id := range ids(shapeSum) {
		sumPoints = append(sumPoints, numberPoint{
			Attributes: sequenceAttrs(id), StartTimeUnixNano: now, TimeUnixNano: now, AsDouble: 1,
		})
	}
	histogramPoints := make([]histogramPoint, 0, pointsPerShapePerExport)
	for _, id := range ids(shapeHistogram) {
		histogramPoints = append(histogramPoints, histogramPoint{
			Attributes: sequenceAttrs(id), StartTimeUnixNano: now, TimeUnixNano: now,
			Count: "1", Sum: 1, BucketCounts: []string{"0", "1"},
			ExplicitBounds: []float64{1}, Min: 1, Max: 1,
		})
	}
	expHistogramPoints := make([]expHistogramPoint, 0, pointsPerShapePerExport)
	for _, id := range ids(shapeExpHistogram) {
		expHistogramPoints = append(expHistogramPoints, expHistogramPoint{
			Attributes: sequenceAttrs(id), StartTimeUnixNano: now, TimeUnixNano: now,
			Count: "1", Sum: 1, Scale: 0, ZeroCount: "0",
			Positive: bucketRun{Offset: 0, BucketCounts: []string{"1"}},
			Negative: bucketRun{Offset: 0, BucketCounts: []string{}},
			Min:      1, Max: 1,
		})
	}
	summaryPoints := make([]summaryPoint, 0, pointsPerShapePerExport)
	for _, id := range ids(shapeSummary) {
		summaryPoints = append(summaryPoints, summaryPoint{
			Attributes: sequenceAttrs(id), StartTimeUnixNano: now, TimeUnixNano: now,
			Count: "1", Sum: 1, QuantileValues: []quantileValue{{Quantile: 0.5, Value: 1}},
		})
	}

	metrics := []metric{
		{
			Name: metricShapes[shapeGauge].metric, Unit: "1",
			Gauge: &gaugeShape{DataPoints: gaugePoints},
		},
		{
			Name: metricShapes[shapeSum].metric, Unit: "1",
			Sum: &sumShape{
				DataPoints:             sumPoints,
				AggregationTemporality: deltaTemporality,
				IsMonotonic:            true,
			},
		},
		{
			Name: metricShapes[shapeHistogram].metric, Unit: "1",
			Histogram: &histogramShape{
				DataPoints:             histogramPoints,
				AggregationTemporality: deltaTemporality,
			},
		},
		{
			Name: metricShapes[shapeExpHistogram].metric, Unit: "1",
			ExponentialHistogram: &expHistogramShape{
				DataPoints:             expHistogramPoints,
				AggregationTemporality: deltaTemporality,
			},
		},
		{
			Name: metricShapes[shapeSummary].metric, Unit: "1",
			Summary: &summaryShape{DataPoints: summaryPoints},
		},
	}

	return mustEncode(metricsRequest{ResourceMetrics: []resourceMetrics{{
		Resource:     serviceResource(service),
		ScopeMetrics: []scopeMetrics{{Scope: scope{Name: scopeName}, Metrics: metrics}},
	}}})
}

// mustEncode renders a payload. These are plain structs of strings and numbers,
// so a failure would mean the shapes above are wrong rather than the data —
// panicking says so at the first export instead of sending an empty body for
// the whole run.
func mustEncode(payload any) []byte {
	encoded, err := json.Marshal(payload)
	if err != nil {
		panic("chaos: cannot render an OTLP/JSON payload: " + err.Error())
	}
	return encoded
}
