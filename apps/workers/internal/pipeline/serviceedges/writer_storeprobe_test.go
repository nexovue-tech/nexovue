//go:build storeprobe

package serviceedges

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

// Against a real server, after 000040: the door and the peer attribute reach
// their columns, and a browser row and a server row for one pair and minute stay
// two rows after a forced merge, since the door is in the sorting key. Run with
// PROBE_CH_ADDR set (`PROBE_CH_ADDR=127.0.0.1:19000 PROBE_CH_USER=nexovue
// PROBE_CH_PASSWORD=nexovue_dev`).
func TestADoorSurvivesAMergeOnTheServer(t *testing.T) {
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
	tenant := fmt.Sprintf("__edge_door_probe__%d", time.Now().UnixNano())
	t.Cleanup(func() {
		_ = pool.Do(ctx, ch.Query{Body: fmt.Sprintf("DELETE FROM service_edges_1m WHERE tenant_id = '%s'", tenant)})
	})

	minute := time.Now().UTC().Truncate(time.Minute)
	edge := func(door, attr string) Edge {
		return Edge{
			Minute: minute, TenantID: tenant, ProjectID: "probe", RetentionDays: 1,
			ClientService: "checkout", ServerService: "(payments)", ServerPeerKind: "",
			ServerPeerAttr: attr, Door: door, Kind: EdgeCall, CallCount: 3, LatencyMS: []float64{5},
		}
	}
	writer := NewWriter(pool)
	if err := writer.Insert(ctx, []Edge{edge(DoorServer, AttrPeerService)}); err != nil {
		t.Fatalf("insert server row: %v", err)
	}
	if err := writer.Insert(ctx, []Edge{edge(DoorBrowser, AttrPeerService)}); err != nil {
		t.Fatalf("insert browser row: %v", err)
	}
	if err := pool.Do(ctx, ch.Query{Body: "OPTIMIZE TABLE service_edges_1m FINAL"}); err != nil {
		t.Fatalf("merge: %v", err)
	}

	var doors, attrs proto.ColStr
	var calls proto.ColUInt64
	if err := pool.Do(ctx, ch.Query{
		Body: fmt.Sprintf("SELECT CAST(door AS String) AS door, CAST(server_peer_attr AS String) AS server_peer_attr, "+
			"CAST(call_count AS UInt64) AS call_count FROM service_edges_1m "+
			"WHERE tenant_id = '%s' ORDER BY door", tenant),
		Result: proto.Results{
			{Name: "door", Data: &doors},
			{Name: "server_peer_attr", Data: &attrs},
			{Name: "call_count", Data: &calls},
		},
	}); err != nil {
		t.Fatalf("read back: %v", err)
	}
	if doors.Rows() != 2 || doors.Row(0) != DoorBrowser || doors.Row(1) != DoorServer {
		t.Fatalf("doors = %v, want one browser and one server row", doors)
	}
	for row := 0; row < 2; row++ {
		if attrs.Row(row) != AttrPeerService || calls.Row(row) != 3 {
			t.Errorf("row %d = (%q, %d), want (%q, 3)", row, attrs.Row(row), calls.Row(row), AttrPeerService)
		}
	}
}
