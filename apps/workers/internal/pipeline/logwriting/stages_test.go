package logwriting

import (
	"log/slog"
	"strings"
	"testing"
	"time"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
)

const maskedText = "[redacted]"

// blankingMasker stands in for the real mask stage. It rewrites the payload in
// place, which is what lets this test prove the ordering: if masking ran after
// flattening, the original value would already be in a row.
type blankingMasker struct {
	projectID string
	calls     int
}

func (m *blankingMasker) MaskResourceLogs(projectID string, batches []*logspb.ResourceLogs) int {
	m.projectID = projectID
	m.calls++

	replaced := 0
	for _, batch := range batches {
		for _, scope := range batch.GetScopeLogs() {
			for _, record := range scope.GetLogRecords() {
				for _, attribute := range record.GetAttributes() {
					if _, isString := attribute.GetValue().GetValue().(*commonpb.AnyValue_StringValue); !isString {
						continue
					}
					attribute.Value = stringValue(maskedText)
					replaced++
				}
			}
		}
	}
	return replaced
}

// fixedRetention is the project-config cache's answer, stubbed.
type fixedRetention struct{ raw uint16 }

func (f fixedRetention) Retention(string) (uint16, uint16) { return f.raw, 0 }

func testPipeline(masker Masker, retention RetentionSource) *Pipeline {
	return &Pipeline{
		block:     newLogBlock(),
		masker:    masker,
		retention: retention,
		log:       slog.New(slog.DiscardHandler),
	}
}

func TestMaskingRunsBeforeRowsAreBuilt(t *testing.T) {
	masker := &blankingMasker{}
	pipeline := testPipeline(masker, fixedRetention{raw: 7})

	rows, masked, err := pipeline.absorbMessage(receivedAt, logMessage(t))
	if err != nil {
		t.Fatalf("absorbMessage: %v", err)
	}
	if masker.calls != 1 {
		t.Fatalf("masker called %d times, want once per message", masker.calls)
	}
	if masker.projectID != "project-a" {
		t.Errorf("masker asked about project %q, want project-a", masker.projectID)
	}
	if masked != 2 {
		t.Errorf("masked = %d, want the two record attributes", masked)
	}

	if len(rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(rows))
	}
	row := rows[0]
	if strings.Contains(row.LogAttrs, "bad card") {
		t.Errorf("log_attrs = %q, want the masked value", row.LogAttrs)
	}
	// The promoted columns are read from the same attributes, so they carry the
	// masked value too rather than a copy taken before the stage ran.
	if row.ExceptionMessage != maskedText || row.ExceptionType != maskedText {
		t.Errorf("exception = (%q, %q), want both masked",
			row.ExceptionType, row.ExceptionMessage)
	}
	if row.RetentionDays != 7 {
		t.Errorf("retention_days = %d, want the project's 7", row.RetentionDays)
	}
}

func TestRetentionFallsBackToTheDeploymentDefault(t *testing.T) {
	pipeline := testPipeline(&blankingMasker{}, staticRetention{raw: 30})

	rows, _, err := pipeline.absorbMessage(receivedAt, logMessage(t))
	if err != nil {
		t.Fatalf("absorbMessage: %v", err)
	}
	if rows[0].RetentionDays != 30 {
		t.Errorf("retention_days = %d, want 30", rows[0].RetentionDays)
	}
}

// The envelope's receive time is our own clock, and the row partitions on it.
func TestReceivedAtComesFromTheEnvelope(t *testing.T) {
	pipeline := testPipeline(&blankingMasker{}, staticRetention{raw: 30})

	rows, _, err := pipeline.absorbMessage(time.Unix(0, 1).UTC(), logMessage(t))
	if err != nil {
		t.Fatalf("absorbMessage: %v", err)
	}
	if !rows[0].ReceivedAt.Equal(receivedAt) {
		t.Errorf("received_at = %s, want the envelope's %s", rows[0].ReceivedAt, receivedAt)
	}
}

func TestAMaskerIsRequired(t *testing.T) {
	if _, err := New(testConfig(), nil, slog.New(slog.DiscardHandler), Options{}); err == nil {
		t.Fatal("New built a pipeline with no masker; it would write unmasked rows")
	}
}

func testConfig() config.Config {
	return config.Config{
		KafkaBrokers: []string{"localhost:9092"},
		Logs: config.LogIngest{
			Topic:         "logs-raw",
			ConsumerGroup: "nexovue-workers",
			DLQTopic:      "logs-dlq",
			MaxRows:       5000,
			MaxDelay:      2 * time.Second,
		},
		Retention: config.Retention{RawDays: 30},
	}
}

func logMessage(t *testing.T) []byte {
	t.Helper()

	payload, err := proto.Marshal(requestWithOneRecord(errorRecord()))
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	message, err := proto.Marshal(&nexovuev1.Envelope{
		Signal:             nexovuev1.Signal_SIGNAL_LOGS,
		TenantId:           "tenant-a",
		ProjectId:          "project-a",
		ReceivedAtUnixNano: receivedAt.UnixNano(),
		Payload:            payload,
	})
	if err != nil {
		t.Fatalf("marshal envelope: %v", err)
	}
	return message
}
