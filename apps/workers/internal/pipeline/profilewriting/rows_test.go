package profilewriting

import (
	"testing"
	"time"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

func text(key, value string) *commonpb.KeyValue {
	return &commonpb.KeyValue{Key: key, Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: value}}}
}

func payload() decoded {
	named := []*nexovuev1.Frame{
		{Function: "crypto/sha256.block", File: "sha256block.go", Line: 41},
		{Function: "main.(*Cart).Checkout", File: "cart.go", Line: 112},
	}
	return decoded{
		envelope: &nexovuev1.Envelope{TenantId: "org", ProjectId: "project"},
		samples: &nexovuev1.ProfileSamples{Batches: []*nexovuev1.ProfileBatch{{
			ResourceAttributes: []*commonpb.KeyValue{
				text("service.name", "checkout"), text("host.name", "web-1"),
				text("service.version", "1.4.2"), text("deployment.environment", "production"),
			},
			SampleType: "cpu", SampleUnit: "nanoseconds",
			Samples: []*nexovuev1.Sample{
				{Frames: named, Value: 30, TimeUnixNano: 1_500,
					TraceId: []byte{0xab, 0xcd}, SpanId: []byte{0x01},
					Attributes: []*commonpb.KeyValue{text("thread.name", "worker-3")}},
				{Frames: named, Value: 10, TimeUnixNano: 1_600},
				{Frames: []*nexovuev1.Frame{{Binary: "libc.so.6", Address: 0x9A4F0}}, Value: 10},
			},
		}}},
	}
}

func TestEverySampleBecomesARowWithItsResourceColumns(t *testing.T) {
	rows := payload().flatten(stamps{receivedAt: time.Unix(10, 0), retentionDays: 14})
	if len(rows) != 3 {
		t.Fatalf("rows = %d, want 3", len(rows))
	}
	first := rows[0]
	if first.ServiceName != "checkout" || first.HostName != "web-1" || first.ServiceVersion != "1.4.2" ||
		first.Environment != "production" {
		t.Errorf("resource columns = %q %q %q %q", first.ServiceName, first.HostName, first.ServiceVersion, first.Environment)
	}
	if first.SampleType != "cpu" || first.SampleUnit != "nanoseconds" || first.Value != 30 {
		t.Errorf("type, unit, value = %s %s %d", first.SampleType, first.SampleUnit, first.Value)
	}
	if first.Functions[0] != "crypto/sha256.block" || first.Lines[1] != 112 {
		t.Errorf("frames = %v %v, want leaf first", first.Functions, first.Lines)
	}
	if string(first.TraceID[:4]) != "abcd" || len(first.TraceID) != 32 || len(first.SpanID) != 16 {
		t.Errorf("ids = %q %q, want hex padded to 32 and 16", first.TraceID, first.SpanID)
	}
	if first.Attrs == "" || first.Attrs == "{}" {
		t.Errorf("attrs = %q, want the sample's labels", first.Attrs)
	}
}

func TestTheNewerEnvironmentNameWins(t *testing.T) {
	p := payload()
	batch := p.samples.Batches[0]
	batch.ResourceAttributes = append(batch.ResourceAttributes, text("deployment.environment.name", "staging"))
	if env := p.flatten(stamps{})[0].Environment; env != "staging" {
		t.Errorf("environment = %q, want the newer attribute's", env)
	}
}

// A whole-host profiler sees processes no SDK named: dockerd, a kernel thread.
func TestAProcessWithNoServiceNameIsNamedAsOpenTelemetrySDKsNameIt(t *testing.T) {
	cases := []struct {
		resource []*commonpb.KeyValue
		want     string
	}{
		{[]*commonpb.KeyValue{text("process.executable.name", "dockerd")}, "unknown_service:dockerd"},
		{nil, "unknown_service"},
	}
	for _, c := range cases {
		p := payload()
		p.samples.Batches[0].ResourceAttributes = c.resource
		if got := p.flatten(stamps{})[0].ServiceName; got != c.want {
			t.Errorf("service = %q, want %q", got, c.want)
		}
	}
}

func TestTheSameStackHashesTheSameAndAnotherDoesNot(t *testing.T) {
	rows := payload().flatten(stamps{})
	if rows[0].StackHash != rows[1].StackHash {
		t.Error("two samples of one stack hashed differently")
	}
	if rows[0].StackHash == rows[2].StackHash {
		t.Error("two different stacks hashed alike")
	}
}

func TestAnUnnamedFrameKeepsItsBinaryAndAddress(t *testing.T) {
	unnamed := payload().flatten(stamps{})[2]
	if unnamed.Functions[0] != "" || unnamed.Binaries[0] != "libc.so.6" || unnamed.Addresses[0] != 0x9A4F0 {
		t.Errorf("frame = %q %q %x", unnamed.Functions[0], unnamed.Binaries[0], unnamed.Addresses[0])
	}
}

// D2: the project's raw retention, never more than 14 days.
func TestRetentionIsCappedAtFourteenDays(t *testing.T) {
	for raw, want := range map[uint16]uint16{7: 7, 14: 14, 30: 14, 0: 14} {
		if got := profileRetention(raw); got != want {
			t.Errorf("profileRetention(%d) = %d, want %d", raw, got, want)
		}
	}
}
