//go:build storeprobe

package traceassembly

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// Against a real server, because the first version passed every unit test and
// wrote no summary at all: ch-go infers a DateTime64's precision from the
// server's column type, and cannot read it through `SimpleAggregateFunction(min,
// DateTime64(9))`. Run with PROBE_CH_ADDR set (`PROBE_CH_ADDR=127.0.0.1:19000
// PROBE_CH_USER=nexovue PROBE_CH_PASSWORD=nexovue_dev`).
func TestASummaryRoundTripsThroughTheServer(t *testing.T) {
	ctx := context.Background()
	pool, err := chpool.Dial(ctx, chpool.Options{ClientOptions: ch.Options{
		Address:  os.Getenv("PROBE_CH_ADDR"),
		User:     os.Getenv("PROBE_CH_USER"),
		Password: os.Getenv("PROBE_CH_PASSWORD"),
		Database: "nexovue",
	}})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer pool.Close()
	tenant := fmt.Sprintf("__summary_probe__%d", time.Now().UnixNano())
	t.Cleanup(func() {
		_ = pool.Do(ctx, ch.Query{Body: fmt.Sprintf("DELETE FROM trace_summaries WHERE tenant_id = '%s'", tenant)})
	})

	keyStart := time.Now().UTC().Truncate(time.Microsecond)
	traceStart := keyStart.Add(-250 * time.Microsecond)
	row := summaryRow{
		TenantID: tenant, ProjectID: "probe", StartTime: keyStart, TraceStart: traceStart,
		TraceID: []byte("5f1c0de55f1c0de55f1c0de55f1c0de5"), ReceivedAt: keyStart, RetentionDays: 1,
		SpanCount: 1, RootService: "probe", RootName: "probe",
	}
	if err := newSummaryWriter(pool).insert(ctx, []summaryRow{row}); err != nil {
		t.Fatalf("insert: %v", err)
	}

	var stored proto.ColInt64
	if err := pool.Do(ctx, ch.Query{
		Body:   fmt.Sprintf("SELECT toUnixTimestamp64Nano(min(trace_start)) FROM trace_summaries WHERE tenant_id = '%s'", tenant),
		Result: proto.Results{{Name: "toUnixTimestamp64Nano(min(trace_start))", Data: &stored}},
	}); err != nil {
		t.Fatalf("read back: %v", err)
	}
	if stored.Rows() != 1 || stored.Row(0) != traceStart.UnixNano() {
		t.Errorf("trace_start = %v, want %d", stored, traceStart.UnixNano())
	}
}
