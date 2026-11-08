package mask

import (
	"encoding/json"
	"os"
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
)

// metadataCase is one case of packages/contracts/fixtures/metadata-masking.json.
type metadataCase struct {
	Name   string `json:"name"`
	Key    string `json:"key"`
	Value  string `json:"value"`
	Masked string `json:"masked"`
}

// TestDefaultsMaskTheSharedMetadataCases holds the worker to the cases the deploy
// webhook's Python port reads (F28 FR9, AC53), so the two cannot drift silently.
func TestDefaultsMaskTheSharedMetadataCases(t *testing.T) {
	raw, err := os.ReadFile("../../../../../packages/contracts/fixtures/metadata-masking.json")
	if err != nil {
		t.Fatalf("read the shared fixture: %v", err)
	}
	var fixture struct {
		Cases []metadataCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("parse the shared fixture: %v", err)
	}
	if len(fixture.Cases) == 0 {
		t.Fatal("the shared fixture holds no cases")
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			attribute := &commonpb.KeyValue{
				Key:   tc.Key,
				Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: tc.Value}},
			}
			maskAttributes(Defaults(), []*commonpb.KeyValue{attribute})
			if got := attribute.GetValue().GetStringValue(); got != tc.Masked {
				t.Fatalf("%s=%q masked to %q, the fixture says %q", tc.Key, tc.Value, got, tc.Masked)
			}
		})
	}
}
