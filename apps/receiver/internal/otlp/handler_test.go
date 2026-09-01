package otlp_test

import (
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ingest"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ratelimit"
)

const (
	validKey = "nvk_live_devlocaldevlocaldevloc"

	// One letter apart from validKey on purpose: the two doors must be told
	// apart by the table they read, never by how the value looks.
	validSiteKey = "nvp_live_devlocaldevlocaldevloc"

	orgID     = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
	projectID = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"

	spansTopic   = "spans-raw"
	logsTopic    = "logs-raw"
	metricsTopic = "metrics-raw"

	contentTypeProtobuf = "application/x-protobuf"
	contentTypeJSON     = "application/json"
)

// testTopics gives each signal a distinct topic, so a batch published to the
// wrong one shows up as the wrong name rather than as no name at all.
var testTopics = ingest.Topics{Spans: spansTopic, Logs: logsTopic, Metrics: metricsTopic}

// --- doubles -----------------------------------------------------------------

type staticKeys struct{}

func (staticKeys) CredentialFor(_ context.Context, prefix string) (auth.Credential, error) {
	if prefix != validKey[:auth.LookupPrefixLength] {
		return auth.Credential{Found: false}, nil
	}
	return auth.Credential{
		Identity: auth.Identity{OrgID: orgID, ProjectID: projectID},
		KeyHash:  auth.HashKey(validKey),
		Found:    true,
	}, nil
}

type unreachableKeys struct{}

func (unreachableKeys) CredentialFor(context.Context, string) (auth.Credential, error) {
	return auth.Credential{}, errors.New("dial tcp: connection refused")
}

// staticSiteKeys stands in for site_keys. There is no hash to compare: the whole
// public key is the lookup, which is the browser door's entire verification.
type staticSiteKeys struct {
	origins []string
}

func (s staticSiteKeys) SiteCredentialFor(
	_ context.Context, publicKey string,
) (auth.SiteCredential, error) {
	if publicKey != validSiteKey {
		return auth.SiteCredential{Found: false}, nil
	}
	return auth.SiteCredential{
		Identity: auth.Identity{OrgID: orgID, ProjectID: projectID, AllowedOrigins: s.origins},
		Found:    true,
	}, nil
}

// countingProducer records what reached the broker, and whether the body had
// been read by the time it did.
type countingProducer struct {
	records []*kgo.Record
	err     error
}

func (p *countingProducer) ProduceSync(_ context.Context, records ...*kgo.Record) kgo.ProduceResults {
	if p.err != nil {
		return kgo.ProduceResults{{Err: p.err}}
	}
	p.records = append(p.records, records...)
	results := make(kgo.ProduceResults, 0, len(records))
	for _, record := range records {
		results = append(results, kgo.ProduceResult{Record: record})
	}
	return results
}

// unresponsiveProducer never answers, which is what a producer whose buffer is
// full does: franz-go blocks the produce and only the context breaks the wait.
type unresponsiveProducer struct{}

func (unresponsiveProducer) ProduceSync(ctx context.Context, records ...*kgo.Record) kgo.ProduceResults {
	<-ctx.Done()
	results := make(kgo.ProduceResults, 0, len(records))
	for _, record := range records {
		results = append(results, kgo.ProduceResult{Record: record, Err: ctx.Err()})
	}
	return results
}

// publishingTo picks the broker double a test asked for.
func publishingTo(counting *countingProducer, opts doorOptions) ingest.Producer {
	if opts.producer != nil {
		return opts.producer
	}
	return counting
}

// countingCounter returns an ever-increasing count, so a test seeds `seen` to
// place a request either inside or past its window's budget.
type countingCounter struct {
	seen int64
}

func (c *countingCounter) Increment(context.Context, string, time.Duration) (int64, error) {
	c.seen++
	return c.seen, nil
}

type brokenCounter struct{}

func (brokenCounter) Increment(context.Context, string, time.Duration) (int64, error) {
	return 0, errors.New("dial tcp: connection refused")
}

// --- fixtures ----------------------------------------------------------------

type door struct {
	handler  http.Handler
	producer *countingProducer
}

type doorOptions struct {
	keys auth.KeyReader

	// siteKeys and siteOrigins are the browser door's half. Every door fixture
	// mounts both entrances, because a test that a secret key is refused from a
	// page is only worth anything against the door a page actually reaches.
	siteKeys    auth.SiteKeyReader
	siteOrigins []string

	counter        ratelimit.Counter
	requestsPerSec int
	produceErr     error
	maxBodyBytes   int64

	// producer replaces the default when a test needs the broker to behave in
	// a way a plain error cannot express, such as never answering.
	producer      ingest.Producer
	produceBudget time.Duration

	// log replaces the discarding default when a test reads what was written.
	log *slog.Logger

	// traffic replaces the default when a test reads the summary the door wrote.
	traffic *otlp.Traffic
}

// testProduceBudget is long enough that a working broker never trips it, short
// enough that a test proving the bound does not stall the suite.
const testProduceBudget = 200 * time.Millisecond

// applyDefaults fills in whatever a test did not care to state.
func (o *doorOptions) applyDefaults() {
	if o.keys == nil {
		o.keys = staticKeys{}
	}
	if o.siteOrigins == nil {
		o.siteOrigins = []string{pageOrigin}
	}
	if o.siteKeys == nil {
		o.siteKeys = staticSiteKeys{origins: o.siteOrigins}
	}
	if o.counter == nil {
		o.counter = &countingCounter{}
	}
	if o.requestsPerSec == 0 {
		o.requestsPerSec = 1000
	}
	if o.maxBodyBytes == 0 {
		o.maxBodyBytes = 1 << 20
	}
	if o.produceBudget == 0 {
		o.produceBudget = testProduceBudget
	}
	if o.log == nil {
		o.log = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	if o.traffic == nil {
		o.traffic = otlp.NewTraffic()
	}
}

func newDoor(t *testing.T, opts doorOptions) door {
	t.Helper()
	opts.applyDefaults()

	producer := &countingProducer{err: opts.produceErr}
	return door{
		producer: producer,
		handler: otlp.Handler(otlp.Doors{
			Server:  serverDoorOptions(producer, opts),
			Browser: browserDoorOptions(producer, opts),
		}),
	}
}

// testCredentialCache is long enough that no test races the TTL and small
// enough that nothing about eviction is being exercised by accident.
var testCredentialCache = auth.Options{CacheTTL: time.Minute, CacheMaxEntries: 100}

// The two doors differ by their gatekeeper and by nothing else, which is what
// the wiring under test claims. Building them from one shared value here is how
// a drift between the two would show up as a compile error rather than a gap.
func serverDoorOptions(producer *countingProducer, opts doorOptions) otlp.Options {
	shared := sharedDoorOptions(producer, opts)
	shared.Gatekeeper = auth.New(opts.keys, testCredentialCache)
	return shared
}

func browserDoorOptions(producer *countingProducer, opts doorOptions) otlp.Options {
	shared := sharedDoorOptions(producer, opts)
	shared.Gatekeeper = auth.NewSite(opts.siteKeys, testCredentialCache)
	return shared
}

func sharedDoorOptions(producer *countingProducer, opts doorOptions) otlp.Options {
	return otlp.Options{
		Limiter:             ratelimit.New(opts.counter, opts.requestsPerSec),
		Publisher:           ingest.NewPublisher(publishingTo(producer, opts), testTopics, opts.produceBudget),
		Logger:              opts.log,
		Traffic:             opts.traffic,
		MaxBodyBytes:        opts.maxBodyBytes,
		MaxDecodedBodyBytes: opts.maxBodyBytes * 4,
	}
}

func traceID(marker byte) []byte {
	id := make([]byte, 16)
	for i := range id {
		id[i] = marker
	}
	return id
}

func exportRequest(traceIDs ...[]byte) *collectorpb.ExportTraceServiceRequest {
	spans := make([]*tracepb.Span, 0, len(traceIDs))
	for _, id := range traceIDs {
		spans = append(spans, &tracepb.Span{Name: "handler", TraceId: id, SpanId: make([]byte, 8)})
	}
	return &collectorpb.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{{
			SchemaUrl:  "https://opentelemetry.io/schemas/1.43.0",
			ScopeSpans: []*tracepb.ScopeSpans{{Spans: spans}},
		}},
	}
}

func encodedRequest(t *testing.T, request *collectorpb.ExportTraceServiceRequest) []byte {
	t.Helper()
	body, err := proto.Marshal(request)
	if err != nil {
		t.Fatalf("encoding request: %v", err)
	}
	return body
}

// post sends an export to the traces door, which is the one most tests exercise.
func post(t *testing.T, d door, body []byte, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	return postTo(t, d, otlp.TracesPath, body, headers)
}

// postTo sends an export to one named door, with the dev key and protobuf
// encoding unless a header says otherwise. A header set to "" is removed.
func postTo(
	t *testing.T,
	d door,
	path string,
	body []byte,
	headers map[string]string,
) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(body))
	request.Header.Set("Content-Type", contentTypeProtobuf)
	request.Header.Set(auth.HeaderName, validKey)
	for name, value := range headers {
		if value == "" {
			request.Header.Del(name)
			continue
		}
		request.Header.Set(name, value)
	}

	recorder := httptest.NewRecorder()
	d.handler.ServeHTTP(recorder, request)
	return recorder
}

// --- the happy path ----------------------------------------------------------

func TestAcceptedBatchIsSplitOneEnvelopePerTrace(t *testing.T) {
	d := newDoor(t, doorOptions{})
	first, second := traceID(0x11), traceID(0x22)

	response := post(t, d, encodedRequest(t, exportRequest(first, second, first)), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if len(d.producer.records) != 2 {
		t.Fatalf("produced %d records, want one per trace id", len(d.producer.records))
	}

	keys := map[string]bool{}
	for _, record := range d.producer.records {
		keys[string(record.Key)] = true
		if record.Topic != spansTopic {
			t.Errorf("topic = %q, want %q", record.Topic, spansTopic)
		}
		var envelope nexovuev1.Envelope
		if err := proto.Unmarshal(record.Value, &envelope); err != nil {
			t.Fatalf("record is not an Envelope: %v", err)
		}
		if envelope.GetTenantId() != orgID || envelope.GetProjectId() != projectID {
			t.Errorf("envelope tenancy = %s/%s, want %s/%s",
				envelope.GetTenantId(), envelope.GetProjectId(), orgID, projectID)
		}
	}
	if len(keys) != 2 {
		t.Errorf("keys = %v, want two distinct trace ids", keys)
	}
}

func TestSuccessAnswersWithAnUnsetPartialSuccess(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if got := response.Header().Get("Content-Type"); got != contentTypeProtobuf {
		t.Errorf("Content-Type = %q, want the request's %q", got, contentTypeProtobuf)
	}
	var body collectorpb.ExportTraceServiceResponse
	if err := proto.Unmarshal(response.Body.Bytes(), &body); err != nil {
		t.Fatalf("success body is not an ExportTraceServiceResponse: %v", err)
	}
	if body.GetPartialSuccess() != nil {
		t.Errorf("partial_success = %v, want unset: everything was accepted", body.GetPartialSuccess())
	}
}

func TestJSONRequestIsAcceptedAndAnsweredInJSON(t *testing.T) {
	d := newDoor(t, doorOptions{})
	body, err := protojson.Marshal(exportRequest(traceID(0x33)))
	if err != nil {
		t.Fatalf("encoding JSON request: %v", err)
	}

	response := post(t, d, body, map[string]string{"Content-Type": contentTypeJSON})

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if got := response.Header().Get("Content-Type"); got != contentTypeJSON {
		t.Errorf("Content-Type = %q, want the request's %q", got, contentTypeJSON)
	}
	var accepted collectorpb.ExportTraceServiceResponse
	if err := protojson.Unmarshal(response.Body.Bytes(), &accepted); err != nil {
		t.Fatalf("success body is not JSON OTLP: %v", err)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("produced %d records, want 1", len(d.producer.records))
	}
}

func TestGzippedRequestIsAccepted(t *testing.T) {
	d := newDoor(t, doorOptions{})

	var compressed bytes.Buffer
	writer := gzip.NewWriter(&compressed)
	if _, err := writer.Write(encodedRequest(t, exportRequest(traceID(0x44)))); err != nil {
		t.Fatalf("compressing: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("closing gzip writer: %v", err)
	}

	response := post(t, d, compressed.Bytes(), map[string]string{"Content-Encoding": "gzip"})

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("produced %d records, want 1: the SDKs gzip by default", len(d.producer.records))
	}
}

func TestBatchWithNoSpansIsAcceptedAndPublishesNothing(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := post(t, d, encodedRequest(t, &collectorpb.ExportTraceServiceRequest{}), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if len(d.producer.records) != 0 {
		t.Errorf("produced %d records for a batch with no spans", len(d.producer.records))
	}
}

// --- failures ----------------------------------------------------------------

// rpcStatus decodes the body every OTLP failure must carry.
func rpcStatus(t *testing.T, response *httptest.ResponseRecorder) *status.Status {
	t.Helper()
	var decoded status.Status
	if err := proto.Unmarshal(response.Body.Bytes(), &decoded); err != nil {
		t.Fatalf("failure body is not a google.rpc.Status: %v (%q)", err, response.Body.String())
	}
	return &decoded
}

func TestUnusableKeyIsRefusedWithoutReachingTheBroker(t *testing.T) {
	cases := map[string]string{
		"absent":    "",
		"malformed": "not-a-nexovue-key",
		"unknown":   "nvk_live_absentabsentabsentabse",
	}
	for name, key := range cases {
		t.Run(name, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))),
				map[string]string{auth.HeaderName: key})

			if response.Code != http.StatusUnauthorized {
				t.Fatalf("status = %d, want %d", response.Code, http.StatusUnauthorized)
			}
			if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_UNAUTHENTICATED) {
				t.Errorf("status code = %d, want UNAUTHENTICATED", got)
			}
			if len(d.producer.records) != 0 {
				t.Error("an unauthenticated request reached the broker")
			}
		})
	}
}

// An unreachable control plane is our failure: 503 is retryable where 401 is not.
func TestUnreachableControlPlaneIsRetryable(t *testing.T) {
	d := newDoor(t, doorOptions{keys: unreachableKeys{}})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusServiceUnavailable)
	}
	if response.Header().Get("Retry-After") == "" {
		t.Error("a 503 must tell the client how long to wait")
	}
}

func TestOverLimitIsRefusedBeforeTheBodyIsRead(t *testing.T) {
	d := newDoor(t, doorOptions{counter: &countingCounter{seen: 5}, requestsPerSec: 1})

	// A body far larger than the cap: if it were read, the answer would be 413.
	response := post(t, d, bytes.Repeat([]byte{0}, 4<<20), nil)

	if response.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want %d: the limiter must answer before decoding",
			response.Code, http.StatusTooManyRequests)
	}
	if got := response.Header().Get("Retry-After"); got != "1" {
		t.Errorf("Retry-After = %q, want %q", got, "1")
	}
	if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_RESOURCE_EXHAUSTED) {
		t.Errorf("status code = %d, want RESOURCE_EXHAUSTED", got)
	}
}

// A Valkey outage must not become an ingest outage.
func TestUnavailableLimiterFailsOpen(t *testing.T) {
	d := newDoor(t, doorOptions{counter: brokenCounter{}})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d: telemetry must survive a limiter blip",
			response.Code, http.StatusOK)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("produced %d records, want 1", len(d.producer.records))
	}
}

func TestMalformedPayloadIsNotRetryable(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := post(t, d, []byte("this is not protobuf at all"), nil)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusBadRequest)
	}
	if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_INVALID_ARGUMENT) {
		t.Errorf("status code = %d, want INVALID_ARGUMENT", got)
	}
	if len(d.producer.records) != 0 {
		t.Error("a malformed payload reached the broker")
	}
}

func TestSpanWithAnUnroutableTraceIDIsRefused(t *testing.T) {
	d := newDoor(t, doorOptions{})
	request := exportRequest(make([]byte, 8)) // not 16 bytes

	response := post(t, d, encodedRequest(t, request), nil)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusBadRequest)
	}
	if len(d.producer.records) != 0 {
		t.Error("a span that cannot be partitioned reached the broker")
	}
}

func TestOversizedBodyIsRefused(t *testing.T) {
	d := newDoor(t, doorOptions{maxBodyBytes: 512})

	response := post(t, d, bytes.Repeat([]byte{0}, 4096), nil)

	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusRequestEntityTooLarge)
	}
	if len(d.producer.records) != 0 {
		t.Error("an oversized body reached the broker")
	}
}

func TestUnsupportedEncodingsAreRefused(t *testing.T) {
	cases := map[string]map[string]string{
		"content type":     {"Content-Type": "application/xml"},
		"content encoding": {"Content-Encoding": "br"},
	}
	for name, headers := range cases {
		t.Run(name, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), headers)

			if response.Code != http.StatusUnsupportedMediaType {
				t.Fatalf("status = %d, want %d", response.Code, http.StatusUnsupportedMediaType)
			}
			if rpcStatus(t, response).GetMessage() == "" {
				t.Error("a failure must say why")
			}
		})
	}
}

// An absent Content-Type is read as protobuf, OTLP/HTTP's binary default.
func TestAbsentContentTypeIsTreatedAsProtobuf(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{"Content-Type": ""})

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
}

func TestBrokerRefusalIsReportedAsRetryable(t *testing.T) {
	d := newDoor(t, doorOptions{produceErr: errors.New("kafka: not leader for partition")})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d: the request must not be acked", response.Code,
			http.StatusServiceUnavailable)
	}
	if response.Header().Get("Retry-After") == "" {
		t.Error("a 503 must tell the client how long to wait")
	}
	if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_UNAVAILABLE) {
		t.Errorf("status code = %d, want UNAVAILABLE", got)
	}
}

// A producer that never answers is the shape of a broker outage: the buffer
// fills, franz-go blocks the produce, and without a bound the handler goroutine
// waits there holding the request's memory until the process dies. The batch
// must come back refused-and-retryable instead, because a refusal the client
// resends is the only ending that does not lose it.
func TestAStuckProducerIsRefusedAsRetryableRatherThanBlocking(t *testing.T) {
	d := newDoor(t, doorOptions{producer: unresponsiveProducer{}})

	answered := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		answered <- post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)
	}()

	var response *httptest.ResponseRecorder
	select {
	case response = <-answered:
	case <-time.After(testProduceBudget * 10):
		t.Fatal("the handler never answered: a stuck broker must not pin a request goroutine")
	}

	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d: the batch must be refused, never silently dropped",
			response.Code, http.StatusServiceUnavailable)
	}
	if response.Header().Get("Retry-After") == "" {
		t.Error("a 503 must tell the client how long to wait, or the batch is lost at the sender")
	}
	if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_UNAVAILABLE) {
		t.Errorf("status code = %d, want UNAVAILABLE", got)
	}
	if len(d.producer.records) != 0 {
		t.Error("nothing reached the broker, yet the door counted a publish")
	}
}

// A JSON caller gets a JSON failure: OTLP requires the response to use the same
// Content-Type as the request, failures included.
func TestJSONRequestGetsAJSONFailure(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := post(t, d, []byte("{"), map[string]string{"Content-Type": contentTypeJSON})

	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusBadRequest)
	}
	if got := response.Header().Get("Content-Type"); got != contentTypeJSON {
		t.Errorf("Content-Type = %q, want %q", got, contentTypeJSON)
	}
	var decoded status.Status
	if err := protojson.Unmarshal(response.Body.Bytes(), &decoded); err != nil {
		t.Fatalf("failure body is not a JSON google.rpc.Status: %v (%q)", err, response.Body)
	}
}

// A credential must never reach a log or an error body.
func TestFailureBodiesNeverEchoTheKey(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := post(t, d, []byte("nonsense"), map[string]string{auth.HeaderName: validKey})

	if strings.Contains(response.Body.String(), validKey) {
		t.Error("the failure body echoed the ingest key")
	}
}

func TestOnlyPostIsRouted(t *testing.T) {
	d := newDoor(t, doorOptions{})
	request := httptest.NewRequest(http.MethodGet, otlp.TracesPath, nil)
	request.Header.Set(auth.HeaderName, validKey)

	recorder := httptest.NewRecorder()
	d.handler.ServeHTTP(recorder, request)

	if recorder.Code == http.StatusOK {
		t.Error("GET /v1/traces was accepted; OTLP/HTTP is POST only")
	}
}

// A log line that misreports a status is how an incident gets diagnosed
// wrongly: the reader believes a 400 was answered and goes looking for a
// malformed batch when the door in fact refused the media type. These are the
// three failures OTLP/HTTP spells more precisely than the rpc code does, so
// they are the three that could disagree.
func TestARefusedStatusIsLoggedAsItWasAnswered(t *testing.T) {
	oversized := bytes.Repeat([]byte{0}, 4096)

	cases := map[string]struct {
		headers map[string]string
		body    []byte
		want    int
	}{
		"unsupported content type": {
			headers: map[string]string{"Content-Type": "application/xml"},
			want:    http.StatusUnsupportedMediaType,
		},
		"unsupported content encoding": {
			headers: map[string]string{"Content-Encoding": "br"},
			want:    http.StatusUnsupportedMediaType,
		},
		"oversized body": {
			body: oversized,
			want: http.StatusRequestEntityTooLarge,
		},
	}
	for name, testCase := range cases {
		t.Run(name, func(t *testing.T) {
			written := &bytes.Buffer{}
			d := newDoor(t, doorOptions{
				maxBodyBytes: 512,
				log:          slog.New(slog.NewJSONHandler(written, &slog.HandlerOptions{Level: slog.LevelDebug})),
			})

			body := testCase.body
			if body == nil {
				body = encodedRequest(t, exportRequest(traceID(0x11)))
			}
			response := post(t, d, body, testCase.headers)

			if response.Code != testCase.want {
				t.Fatalf("status = %d, want %d (%s)", response.Code, testCase.want, response.Body)
			}
			refusal := refusalLine(t, written)
			if refusal["status"] != float64(testCase.want) {
				t.Errorf("logged status = %v, want %d — the log and the wire disagree",
					refusal["status"], testCase.want)
			}
		})
	}
}

// refusalLine returns the one refusal a door logged.
func refusalLine(t *testing.T, written *bytes.Buffer) map[string]any {
	t.Helper()
	for _, line := range recordedLines(t, written) {
		if line["msg"] == "export refused" {
			return line
		}
	}
	t.Fatalf("no refusal was logged (%s)", written)
	return nil
}
