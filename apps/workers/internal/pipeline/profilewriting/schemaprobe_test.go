//go:build schemaprobe

package profilewriting

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/proto"
)

// probeTenant marks the rows this probe writes so they can be removed again.
const probeTenant = "__f17_profiles_probe__"

// TestTheBlockRoundTripsThroughTheServer writes the payload's three samples through
// the real block, reads back what a header check cannot prove — the frame arrays,
// the hex ids, the labels — and deletes what it wrote.
//
// Behind a build tag: it needs a running ClickHouse with migration 000032. Run it with
//
//	PROBE_CH_ADDR=127.0.0.1:19000 go test -tags schemaprobe ./internal/pipeline/profilewriting/
func TestTheBlockRoundTripsThroughTheServer(t *testing.T) {
	ctx := context.Background()
	client, err := ch.Dial(ctx, ch.Options{
		Address: os.Getenv("PROBE_CH_ADDR"), Database: "nexovue", User: "nexovue", Password: "nexovue_dev",
	})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { _ = client.Close() })
	t.Cleanup(func() {
		_ = client.Do(ctx, ch.Query{
			Body:     "ALTER TABLE profile_samples DELETE WHERE tenant_id = '" + probeTenant + "'",
			Settings: []ch.Setting{{Key: "mutations_sync", Value: "2"}},
		})
	})

	p := payload()
	p.envelope.TenantId = probeTenant
	b := newBlock()
	for _, r := range p.flatten(stamps{receivedAt: time.Now(), retentionDays: 1}) {
		b.append(r)
	}
	if err := client.Do(ctx, ch.Query{
		Body:     b.columns.Into(table),
		Input:    b.columns,
		Settings: []ch.Setting{{Key: jsonAsStringSetting, Value: "1"}},
	}); err != nil {
		t.Fatalf("%s rejected the block: %v", table, err)
	}

	var (
		leaf       proto.ColStr
		secondLine proto.ColUInt32
		traceID    proto.ColStr
		thread     proto.ColStr
	)
	if err := client.Do(ctx, ch.Query{
		Body: "SELECT frame_functions[1] AS leaf, frame_lines[2] AS second_line, " +
			"toString(trace_id) AS trace, toString(attrs.`thread.name`) AS thread " +
			"FROM profile_samples WHERE tenant_id = '" + probeTenant + "' AND value = 30 LIMIT 1",
		Result: proto.Results{
			{Name: "leaf", Data: &leaf},
			{Name: "second_line", Data: &secondLine}, {Name: "trace", Data: &traceID},
			{Name: "thread", Data: &thread},
		},
	}); err != nil {
		t.Fatalf("reading back: %v", err)
	}
	if leaf.Row(0) != "crypto/sha256.block" || secondLine.Row(0) != 112 {
		t.Errorf("frames = %q line %d, want the leaf and the caller's line", leaf.Row(0), secondLine.Row(0))
	}
	if traceID.Row(0)[:4] != "abcd" {
		t.Errorf("trace_id = %q, want hex", traceID.Row(0))
	}
	if thread.Row(0) != "worker-3" {
		t.Errorf("attrs thread.name = %q, want worker-3", thread.Row(0))
	}
}
