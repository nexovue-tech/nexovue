package logwriting

import (
	"strings"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// logsColumns is every column of the `logs` table, in declaration order:
// migration 000002 created it, 000015 appended the exception columns and 000017
// appended the timestamp source. A column added there must be added here.
var logsColumns = []string{
	"timestamp", "received_at", "retention_days", "tenant_id", "project_id",
	"trace_id", "span_id", "service_name", "severity", "severity_number",
	"body", "body_structured", "resource_attrs", "log_attrs",
	"exception_type", "exception_message", "timestamp_source",
}

func TestBlockCoversEveryColumnOfTheTable(t *testing.T) {
	block := newLogBlock()
	if len(block.columns) != len(logsColumns) {
		t.Fatalf("block has %d columns, the table has %d", len(block.columns), len(logsColumns))
	}
	for i, column := range block.columns {
		if column.Name != logsColumns[i] {
			t.Errorf("column %d is %q, want %q", i, column.Name, logsColumns[i])
		}
	}

	statement := block.statement()
	if !strings.HasPrefix(statement, `INSERT INTO "logs"`) {
		t.Errorf("statement = %q, want it to insert into logs", statement)
	}
	for _, column := range logsColumns {
		if !strings.Contains(statement, `"`+column+`"`) {
			t.Errorf("statement does not name %q: %s", column, statement)
		}
	}
}

func TestAppendAdvancesEveryColumnTogether(t *testing.T) {
	block := newLogBlock()
	for i := range 3 {
		block.append(testRow())
		if block.rows() != i+1 {
			t.Fatalf("rows() = %d after %d appends", block.rows(), i+1)
		}
		for _, column := range block.columns {
			if got := column.Data.Rows(); got != i+1 {
				t.Fatalf("column %q holds %d rows, want %d", column.Name, got, i+1)
			}
		}
	}
}

// A block is reused between flushes, so a stale row must never ride along on
// the next insert.
func TestResetEmptiesEveryColumn(t *testing.T) {
	block := newLogBlock()
	block.append(testRow())
	block.reset()

	if block.rows() != 0 {
		t.Errorf("rows() = %d after reset, want 0", block.rows())
	}
	for _, column := range block.columns {
		if got := column.Data.Rows(); got != 0 {
			t.Errorf("column %q holds %d rows after reset, want 0", column.Name, got)
		}
	}
}

func testRow() logRow {
	moment := time.Unix(0, 1_700_000_000_000_000_000).UTC()
	return logRow{
		Timestamp:       moment,
		ReceivedAt:      moment,
		RetentionDays:   30,
		TenantID:        "tenant-a",
		ProjectID:       "project-a",
		TraceID:         otlpvalue.HexID(testTraceID, otlpvalue.TraceIDHexLen),
		SpanID:          otlpvalue.HexID(testSpanID, otlpvalue.SpanIDHexLen),
		ServiceName:     "checkout",
		Severity:        "ERROR",
		SeverityNumber:  17,
		Body:            "payment declined",
		BodyStructured:  otlpvalue.EmptyObject,
		ResourceAttrs:   otlpvalue.EmptyObject,
		LogAttrs:        otlpvalue.EmptyObject,
		TimestampSource: timestampSourceEvent,
	}
}
