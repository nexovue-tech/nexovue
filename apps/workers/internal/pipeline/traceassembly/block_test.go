package traceassembly

import (
	"strings"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// spansColumns is every column of the `spans` table, in declaration order.
// Migration 000046 rebuilt the table and is now the source; a column added
// there must be added here.
var spansColumns = []string{
	"timestamp", "received_at", "retention_days", "derived_retention_days",
	"tenant_id", "project_id", "trace_id", "span_id", "parent_span_id",
	"service_name", "span_name", "span_kind", "duration_ms", "status_code",
	"status_message", "http_method", "http_route", "http_status",
	"deployment_version", "resource_attrs", "span_attrs", "events",
}

func TestBlockCoversEveryColumnOfTheTable(t *testing.T) {
	block := newSpanBlock()
	if len(block.columns) != len(spansColumns) {
		t.Fatalf("block has %d columns, the table has %d", len(block.columns), len(spansColumns))
	}
	for i, column := range block.columns {
		if column.Name != spansColumns[i] {
			t.Errorf("column %d is %q, want %q", i, column.Name, spansColumns[i])
		}
	}

	statement := block.statement()
	if !strings.HasPrefix(statement, `INSERT INTO "spans"`) {
		t.Errorf("statement = %q, want it to insert into spans", statement)
	}
	for _, column := range spansColumns {
		if !strings.Contains(statement, `"`+column+`"`) {
			t.Errorf("statement does not name %q: %s", column, statement)
		}
	}
}

func TestAppendAdvancesEveryColumnTogether(t *testing.T) {
	block := newSpanBlock()
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
	block := newSpanBlock()
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

func testRow() spanRow {
	return spanRow{
		Timestamp:     time.Unix(0, 1_700_000_000_000_000_000).UTC(),
		TenantID:      "tenant-a",
		ProjectID:     "project-a",
		TraceID:       otlpvalue.HexID(testTraceID, otlpvalue.TraceIDHexLen),
		SpanID:        otlpvalue.HexID(testSpanID, otlpvalue.SpanIDHexLen),
		ParentSpanID:  otlpvalue.HexID(nil, otlpvalue.SpanIDHexLen),
		ServiceName:   "checkout",
		SpanName:      "GET /orders/:id",
		SpanKind:      "SERVER",
		DurationMS:    1.5,
		StatusCode:    "OK",
		HTTPMethod:    "GET",
		HTTPRoute:     "/orders/:id",
		HTTPStatus:    200,
		ResourceAttrs: otlpvalue.EmptyObject,
		SpanAttrs:     otlpvalue.EmptyObject,
		Events:        "[]",
	}
}
