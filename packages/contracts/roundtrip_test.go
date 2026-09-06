// Round-trip proof for the frozen envelope. Every golden fixture must decode to
// the field values stated here, re-encode to the exact bytes on disk, and carry a
// payload that still parses as the OTLP request its signal names.
//
// The Python suite in tests/ asserts the same table independently. Two readers
// agreeing is what makes "frozen contract" a fact rather than an assertion.
package contracts_test

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	logspb "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metricspb "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	tracepb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

const (
	fixturesDir    = "fixtures"
	tenantID       = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
	projectID      = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"
	semconvVersion = "1.44.0"

	nanosPerMillisecond = 1_000_000
	baseUnixNano        = 1787649300 * int64(1_000_000_000)
)

type fixtureExpectation struct {
	filename             string
	door                 nexovuev1.Door
	signal               nexovuev1.Signal
	ingestID             string
	receivedOffsetMillis int64
	minPayloadEntryCount int
}

// One row per shape a door sends, the list in docs/architecture/ingestion-envelope.md.
// Door 3 has two: one page view produces both log records and a span, and an
// envelope carries one signal.
var expectations = []fixtureExpectation{
	{"door1-traces.bin", nexovuev1.Door_DOOR_OTLP, nexovuev1.Signal_SIGNAL_SPANS, "01996d5e-9a40-7c01-a000-000000000001", 430, 1},
	{"door1-logs.bin", nexovuev1.Door_DOOR_OTLP, nexovuev1.Signal_SIGNAL_LOGS, "01996d5e-9a40-7c01-a000-000000000002", 1_260, 1},
	{"door1-metrics.bin", nexovuev1.Door_DOOR_OTLP, nexovuev1.Signal_SIGNAL_METRICS, "01996d5e-9a40-7c01-a000-000000000003", 60_080, 1},
	{"door2-synthetic-http.bin", nexovuev1.Door_DOOR_SYNTHETIC, nexovuev1.Signal_SIGNAL_METRICS, "01996d5e-9a40-7c01-a000-000000000004", 310, 1},
	{"door2-synthetic-browser.bin", nexovuev1.Door_DOOR_SYNTHETIC, nexovuev1.Signal_SIGNAL_LOGS, "01996d5e-9a40-7c01-a000-000000000005", 32_190, 1},
	{"door3-rum-logs.bin", nexovuev1.Door_DOOR_RUM, nexovuev1.Signal_SIGNAL_LOGS, "01996d5e-9a40-7c01-a000-000000000009", 8_712, 1},
	{"door3-rum-traces.bin", nexovuev1.Door_DOOR_RUM, nexovuev1.Signal_SIGNAL_SPANS, "01996d5e-9a40-7c01-a000-000000000006", 8_719, 1},
	{"door4b-cloudwatch.bin", nexovuev1.Door_DOOR_POLLER, nexovuev1.Signal_SIGNAL_METRICS, "01996d5e-9a40-7c01-a000-000000000007", 60_140, 1},
	{"door4b-dbstats.bin", nexovuev1.Door_DOOR_POLLER, nexovuev1.Signal_SIGNAL_METRICS, "01996d5e-9a40-7c01-a000-000000000008", 60_210, 1},
}

func TestEveryDoorHasAFixture(t *testing.T) {
	entries, err := filepath.Glob(filepath.Join(fixturesDir, "*.bin"))
	if err != nil {
		t.Fatalf("globbing fixtures: %v", err)
	}
	if len(entries) != len(expectations) {
		t.Fatalf("fixture count = %d, want %d — a door gained or lost a fixture", len(entries), len(expectations))
	}
}

func TestFixturesDecodeToExpectedFields(t *testing.T) {
	for _, want := range expectations {
		t.Run(want.filename, func(t *testing.T) {
			encoded := readFixture(t, want.filename)

			var envelope nexovuev1.Envelope
			if err := proto.Unmarshal(encoded, &envelope); err != nil {
				t.Fatalf("decoding envelope: %v", err)
			}

			if envelope.GetTenantId() != tenantID {
				t.Errorf("tenant_id = %q, want %q", envelope.GetTenantId(), tenantID)
			}
			if envelope.GetProjectId() != projectID {
				t.Errorf("project_id = %q, want %q", envelope.GetProjectId(), projectID)
			}
			if envelope.GetDoor() != want.door {
				t.Errorf("door = %v, want %v", envelope.GetDoor(), want.door)
			}
			if envelope.GetSignal() != want.signal {
				t.Errorf("signal = %v, want %v", envelope.GetSignal(), want.signal)
			}
			if envelope.GetIngestId() != want.ingestID {
				t.Errorf("ingest_id = %q, want %q", envelope.GetIngestId(), want.ingestID)
			}
			if envelope.GetSemconvVersion() != semconvVersion {
				t.Errorf("semconv_version = %q, want %q", envelope.GetSemconvVersion(), semconvVersion)
			}
			wantReceivedAt := baseUnixNano + want.receivedOffsetMillis*nanosPerMillisecond
			if envelope.GetReceivedAtUnixNano() != wantReceivedAt {
				t.Errorf("received_at_unix_nano = %d, want %d", envelope.GetReceivedAtUnixNano(), wantReceivedAt)
			}
			if len(envelope.GetPayload()) == 0 {
				t.Fatal("payload is empty — the fixture is a stub, not a recorded payload")
			}
		})
	}
}

// The point of the whole contract: bytes in, identical bytes out.
func TestFixturesReEncodeByteIdentically(t *testing.T) {
	marshal := proto.MarshalOptions{Deterministic: true}

	for _, want := range expectations {
		t.Run(want.filename, func(t *testing.T) {
			encoded := readFixture(t, want.filename)

			var envelope nexovuev1.Envelope
			if err := proto.Unmarshal(encoded, &envelope); err != nil {
				t.Fatalf("decoding envelope: %v", err)
			}
			reEncoded, err := marshal.Marshal(&envelope)
			if err != nil {
				t.Fatalf("re-encoding envelope: %v", err)
			}
			if string(reEncoded) != string(encoded) {
				t.Errorf("re-encoded %d bytes, fixture is %d bytes — the round trip is not lossless",
					len(reEncoded), len(encoded))
			}
		})
	}
}

// The payload must still be OTLP the signal's own decoder accepts. A payload the
// envelope carried but corrupted would pass the byte check and fail here.
func TestPayloadsParseAsTheirSignalsOtlpRequest(t *testing.T) {
	for _, want := range expectations {
		t.Run(want.filename, func(t *testing.T) {
			var envelope nexovuev1.Envelope
			if err := proto.Unmarshal(readFixture(t, want.filename), &envelope); err != nil {
				t.Fatalf("decoding envelope: %v", err)
			}

			entryCount, err := otlpEntryCount(envelope.GetSignal(), envelope.GetPayload())
			if err != nil {
				t.Fatalf("parsing payload as %v: %v", envelope.GetSignal(), err)
			}
			if entryCount < want.minPayloadEntryCount {
				t.Errorf("payload holds %d resource entries, want at least %d",
					entryCount, want.minPayloadEntryCount)
			}
		})
	}
}

// Number of resource_* entries in the OTLP request the signal names.
func otlpEntryCount(signal nexovuev1.Signal, payload []byte) (int, error) {
	switch signal {
	case nexovuev1.Signal_SIGNAL_SPANS:
		var request tracepb.ExportTraceServiceRequest
		if err := proto.Unmarshal(payload, &request); err != nil {
			return 0, err
		}
		return len(request.GetResourceSpans()), nil
	case nexovuev1.Signal_SIGNAL_LOGS:
		var request logspb.ExportLogsServiceRequest
		if err := proto.Unmarshal(payload, &request); err != nil {
			return 0, err
		}
		return len(request.GetResourceLogs()), nil
	case nexovuev1.Signal_SIGNAL_METRICS:
		var request metricspb.ExportMetricsServiceRequest
		if err := proto.Unmarshal(payload, &request); err != nil {
			return 0, err
		}
		return len(request.GetResourceMetrics()), nil
	default:
		return 0, errUnroutableSignal
	}
}

var errUnroutableSignal = errors.New("signal is unspecified, so no OTLP request type routes it")

func readFixture(t *testing.T, filename string) []byte {
	t.Helper()
	encoded, err := os.ReadFile(filepath.Join(fixturesDir, filename))
	if err != nil {
		t.Fatalf("reading fixture: %v", err)
	}
	return encoded
}
