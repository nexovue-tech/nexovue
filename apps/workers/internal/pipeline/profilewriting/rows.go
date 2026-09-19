package profilewriting

import (
	"encoding/binary"
	"fmt"
	"hash/fnv"
	"time"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// The resource attributes behind the promoted columns. The environment has two
// names in OpenTelemetry's conventions; the newer wins when both are set.
const (
	attrServiceName       = "service.name"
	attrServiceVersion    = "service.version"
	attrHostName          = "host.name"
	attrEnvironment       = "deployment.environment.name"
	attrLegacyEnvironment = "deployment.environment"
	attrExecutableName    = "process.executable.name"
	// What an OpenTelemetry SDK calls a service it was not told the name of.
	unknownService = "unknown_service"
)

// serviceName is the resource's service.name, or — for a process no SDK named, which a
// whole-host profiler reports — `unknown_service:<executable>`, as an SDK names it.
func serviceName(resource []*commonpb.KeyValue) string {
	if name := otlpvalue.StringOrEmpty(resource, attrServiceName); name != "" {
		return name
	}
	if executable := otlpvalue.StringOrEmpty(resource, attrExecutableName); executable != "" {
		return unknownService + ":" + executable
	}
	return unknownService
}

// row is one sample as `profile_samples` stores it.
type row struct {
	Timestamp      time.Time
	ReceivedAt     time.Time
	RetentionDays  uint16
	TenantID       string
	ProjectID      string
	ServiceName    string
	HostName       string
	ServiceVersion string
	Environment    string
	SampleType     string
	SampleUnit     string
	Value          int64
	StackHash      uint64
	Functions      []string
	Files          []string
	Lines          []uint32
	Binaries       []string
	Addresses      []uint64
	// TraceID and SpanID are hex, padded to the FixedString widths, as `spans`
	// stores them — a span's profile is an equality on the two.
	TraceID       []byte
	SpanID        []byte
	ResourceAttrs string
	Attrs         string
}

// stamps are what every row of one message shares, from the envelope and the
// project's configuration — never from the payload.
type stamps struct {
	receivedAt    time.Time
	retentionDays uint16
}

// decoded is one Kafka message unpacked: the envelope and the ProfileSamples it
// carries, still in protobuf form so masking can run on it before any row exists.
type decoded struct {
	envelope *nexovuev1.Envelope
	samples  *nexovuev1.ProfileSamples
}

// decodeEnvelope unpacks one message. Every failure is a *consume.Rejection naming
// the tenant, so the caller parks it without asking.
func decodeEnvelope(message []byte) (decoded, error) {
	envelope, err := consume.DecodeEnvelope(message, nexovuev1.Signal_SIGNAL_PROFILES)
	if err != nil {
		return decoded{}, err
	}
	var samples nexovuev1.ProfileSamples
	if err := proto.Unmarshal(envelope.GetPayload(), &samples); err != nil {
		return decoded{}, consume.RejectedBy(envelope, fmt.Errorf("decode ProfileSamples payload: %w", err))
	}
	return decoded{envelope: envelope, samples: &samples}, nil
}

// flatten turns one decoded message into rows, one per sample.
func (d decoded) flatten(marks stamps) []row {
	var out []row
	for _, batch := range d.samples.GetBatches() {
		resource := batch.GetResourceAttributes()
		environment := otlpvalue.StringOrEmpty(resource, attrEnvironment)
		if environment == "" {
			environment = otlpvalue.StringOrEmpty(resource, attrLegacyEnvironment)
		}
		shared := row{
			ReceivedAt:     marks.receivedAt,
			RetentionDays:  marks.retentionDays,
			TenantID:       d.envelope.GetTenantId(),
			ProjectID:      d.envelope.GetProjectId(),
			ServiceName:    serviceName(resource),
			HostName:       otlpvalue.StringOrEmpty(resource, attrHostName),
			ServiceVersion: otlpvalue.StringOrEmpty(resource, attrServiceVersion),
			Environment:    environment,
			SampleType:     batch.GetSampleType(),
			SampleUnit:     batch.GetSampleUnit(),
			ResourceAttrs:  otlpvalue.AttributesJSON(resource),
		}
		for _, sample := range batch.GetSamples() {
			out = append(out, sampleRow(shared, sample))
		}
	}
	return out
}

func sampleRow(shared row, sample *nexovuev1.Sample) row {
	frames := sample.GetFrames()
	r := shared
	r.Timestamp = time.Unix(0, int64(sample.GetTimeUnixNano())).UTC() //nolint:gosec // a nanosecond clock fits int64 until 2262
	r.Value = sample.GetValue()
	r.Functions = make([]string, len(frames))
	r.Files = make([]string, len(frames))
	r.Lines = make([]uint32, len(frames))
	r.Binaries = make([]string, len(frames))
	r.Addresses = make([]uint64, len(frames))
	for i, frame := range frames {
		r.Functions[i] = frame.GetFunction()
		r.Files[i] = frame.GetFile()
		r.Lines[i] = frame.GetLine()
		r.Binaries[i] = frame.GetBinary()
		r.Addresses[i] = frame.GetAddress()
	}
	r.StackHash = stackHash(frames)
	r.TraceID = otlpvalue.HexID(sample.GetTraceId(), otlpvalue.TraceIDHexLen)
	r.SpanID = otlpvalue.HexID(sample.GetSpanId(), otlpvalue.SpanIDHexLen)
	r.Attrs = otlpvalue.AttributesJSON(sample.GetAttributes())
	return r
}

// stackHash identifies a stack by everything that distinguishes one frame from
// another. Two samples of the same stack sort side by side within the hour
// (`profile_samples`' ORDER BY), which is what lets their arrays compress, and the
// read side groups by it. FNV-1a: stable, fast, and in the standard library.
func stackHash(frames []*nexovuev1.Frame) uint64 {
	hash := fnv.New64a()
	var number [8]byte
	for _, frame := range frames {
		_, _ = hash.Write([]byte(frame.GetFunction()))
		_, _ = hash.Write([]byte{0})
		_, _ = hash.Write([]byte(frame.GetFile()))
		_, _ = hash.Write([]byte{0})
		binary.LittleEndian.PutUint64(number[:], uint64(frame.GetLine()))
		_, _ = hash.Write(number[:])
		_, _ = hash.Write([]byte(frame.GetBinary()))
		_, _ = hash.Write([]byte{0})
		binary.LittleEndian.PutUint64(number[:], frame.GetAddress())
		_, _ = hash.Write(number[:])
	}
	return hash.Sum64()
}
