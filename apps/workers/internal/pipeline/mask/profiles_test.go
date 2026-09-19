package mask_test

import (
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
)

// AC6: a sample's attribute matching a rule is masked; function and file names
// are stored as sent.
func TestProfileAttributesAreMaskedAndFramesAreNot(t *testing.T) {
	secret := "super-secret-value"
	frame := &nexovuev1.Frame{Function: "main.(*Auth).CheckToken", File: "auth.go", Line: 42}
	batch := &nexovuev1.ProfileBatch{
		ResourceAttributes: []*commonpb.KeyValue{stringAttr("api_key", secret)},
		Samples: []*nexovuev1.Sample{{
			Frames:     []*nexovuev1.Frame{frame},
			Value:      1,
			Attributes: []*commonpb.KeyValue{stringAttr("api_key", secret)},
		}},
	}
	payload := &nexovuev1.ProfileSamples{Batches: []*nexovuev1.ProfileBatch{batch}}

	masker := mask.New(mask.StaticRules(mask.Defaults()))
	if masked := masker.MaskProfileSamples("project", payload); masked != 2 {
		t.Fatalf("masked = %d, want 2 (the resource's and the sample's)", masked)
	}
	if attrValue(batch.GetResourceAttributes(), "api_key") == secret {
		t.Error("the secret survived masking in the resource")
	}
	if attrValue(batch.GetSamples()[0].GetAttributes(), "api_key") == secret {
		t.Error("the secret survived masking in the sample")
	}
	if frame.GetFunction() != "main.(*Auth).CheckToken" || frame.GetFile() != "auth.go" {
		t.Errorf("frame = %v, want the function and file untouched", frame)
	}
}
