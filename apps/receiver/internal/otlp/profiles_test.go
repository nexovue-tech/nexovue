package otlp_test

import (
	"context"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	profilescollectorpb "go.opentelemetry.io/proto/otlp/collector/profiles/v1development"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	profilespb "go.opentelemetry.io/proto/otlp/profiles/v1development"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// profilesRequest is one CPU profile of the checkout service: one two-frame stack
// sampled once, with its dictionary.
func profilesRequest() *profilescollectorpb.ExportProfilesServiceRequest {
	return &profilescollectorpb.ExportProfilesServiceRequest{
		Dictionary: &profilespb.ProfilesDictionary{
			StringTable:   []string{"", "cpu", "nanoseconds", "main.handle", "main.go", "main.main"},
			MappingTable:  []*profilespb.Mapping{{}},
			FunctionTable: []*profilespb.Function{{}, {NameStrindex: 3, FilenameStrindex: 4}, {NameStrindex: 5, FilenameStrindex: 4}},
			LocationTable: []*profilespb.Location{
				{},
				{Lines: []*profilespb.Line{{FunctionIndex: 1, Line: 20}}},
				{Lines: []*profilespb.Line{{FunctionIndex: 2, Line: 9}}},
			},
			StackTable:     []*profilespb.Stack{{}, {LocationIndices: []int32{1, 2}}},
			LinkTable:      []*profilespb.Link{{}},
			AttributeTable: []*profilespb.KeyValueAndUnit{{}},
		},
		ResourceProfiles: []*profilespb.ResourceProfiles{{
			Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{{
				Key:   "service.name",
				Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: "checkout"}},
			}}},
			ScopeProfiles: []*profilespb.ScopeProfiles{{Profiles: []*profilespb.Profile{{
				SampleType:   &profilespb.ValueType{TypeStrindex: 1, UnitStrindex: 2},
				TimeUnixNano: uint64(time.Now().UnixNano()),
				Samples:      []*profilespb.Sample{{StackIndex: 1, Values: []int64{10_000_000}}},
			}}}},
		}},
	}
}

// publishedProfiles decodes the one envelope the door published, and fails the test
// unless it went to the profiles topic carrying nexovue's own ProfileSamples.
func publishedProfiles(t *testing.T, producer *countingProducer) *nexovuev1.ProfileSamples {
	t.Helper()
	if len(producer.records) != 1 {
		t.Fatalf("published %d records, want 1", len(producer.records))
	}
	record := producer.records[0]
	if record.Topic != profilesTopic {
		t.Errorf("topic = %q, want %q", record.Topic, profilesTopic)
	}
	var envelope nexovuev1.Envelope
	if err := proto.Unmarshal(record.Value, &envelope); err != nil {
		t.Fatalf("decoding envelope: %v", err)
	}
	if envelope.GetSignal() != nexovuev1.Signal_SIGNAL_PROFILES {
		t.Errorf("signal = %v, want SIGNAL_PROFILES", envelope.GetSignal())
	}
	var samples nexovuev1.ProfileSamples
	if err := proto.Unmarshal(envelope.GetPayload(), &samples); err != nil {
		t.Fatalf("payload is not ProfileSamples (ADR-015): %v", err)
	}
	return &samples
}

func TestAProfileIsConvertedAndPublishedOverHTTP(t *testing.T) {
	d := newDoor(t, doorOptions{})
	body, err := proto.Marshal(profilesRequest())
	if err != nil {
		t.Fatal(err)
	}

	response := postTo(t, d, otlp.ProfilesPath, body, nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", response.Code, response.Body.String())
	}
	frames := publishedProfiles(t, d.producer).GetBatches()[0].GetSamples()[0].GetFrames()
	if len(frames) != 2 || frames[0].GetFunction() != "main.handle" {
		t.Errorf("frames = %v, want main.handle leaf first", frames)
	}
}

func TestAProfileIsAcceptedAsJSON(t *testing.T) {
	d := newDoor(t, doorOptions{})
	body, err := protojson.Marshal(profilesRequest())
	if err != nil {
		t.Fatal(err)
	}

	response := postTo(t, d, otlp.ProfilesPath, body, map[string]string{"Content-Type": contentTypeJSON})

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", response.Code, response.Body.String())
	}
	publishedProfiles(t, d.producer)
}

func TestAProfileIsAcceptedOverGRPC(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{})
	client := profilescollectorpb.NewProfilesServiceClient(d.conn)

	if _, err := client.Export(callContext(t, validKey), profilesRequest()); err != nil {
		t.Fatalf("Export: %v", err)
	}
	publishedProfiles(t, d.producer)
}

// AC4: a Collector speaking another OTLP profiles version is told which one to send,
// and nothing it sent is stored.
func TestAnotherProfilesVersionIsRefusedNamingTheSupportedPath(t *testing.T) {
	d := newDoor(t, doorOptions{})
	body, _ := proto.Marshal(profilesRequest())

	for _, path := range []string{"/v1/profiles", "/v1experimental/profiles"} {
		response := postTo(t, d, path, body, nil)
		if response.Code != http.StatusNotFound {
			t.Errorf("%s: status = %d, want 404", path, response.Code)
		}
		if message := rpcStatus(t, response).GetMessage(); !strings.Contains(message, otlp.ProfilesPath) {
			t.Errorf("%s: message = %q, want it to name %s", path, message, otlp.ProfilesPath)
		}
	}
	if len(d.producer.records) != 0 {
		t.Errorf("published %d records, want none", len(d.producer.records))
	}
}

func TestAMalformedProfileIsRefusedWithItsReason(t *testing.T) {
	d := newDoor(t, doorOptions{})
	request := profilesRequest()
	request.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples[0].StackIndex = 42
	body, _ := proto.Marshal(request)

	response := postTo(t, d, otlp.ProfilesPath, body, nil)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", response.Code)
	}
	if message := rpcStatus(t, response).GetMessage(); !strings.Contains(message, "stack_table index 42") {
		t.Errorf("message = %q, want it to name the bad index", message)
	}
	if len(d.producer.records) != 0 {
		t.Errorf("published %d records, want none", len(d.producer.records))
	}
}

// A public site key is printed in a web page; it has no profiles to send, and the
// browser entrance does not take them.
func TestTheBrowserEntranceDoesNotTakeProfiles(t *testing.T) {
	d := newDoor(t, doorOptions{})
	body, _ := proto.Marshal(profilesRequest())

	response := postTo(t, d, otlp.BrowserPathPrefix+otlp.ProfilesPath, body,
		map[string]string{"Origin": pageOrigin})

	if response.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404", response.Code)
	}
	if len(d.producer.records) != 0 {
		t.Errorf("published %d records, want none", len(d.producer.records))
	}
}

// keyedCounter counts per rate-limit key, which is what tells a profiles bucket from
// the key's own.
type keyedCounter struct {
	mu   sync.Mutex
	seen map[string]int64
}

func (c *keyedCounter) Increment(_ context.Context, key string, by int64, _ time.Duration) (int64, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.seen == nil {
		c.seen = map[string]int64{}
	}
	c.seen[key] += by
	return c.seen[key], nil
}

// AC15, the receiver's half: a profiling flood spends its own budget, never the one
// the key's traces need.
func TestProfilesSpendTheirOwnRateBudget(t *testing.T) {
	d := newDoor(t, doorOptions{counter: &keyedCounter{}, requestsPerSec: 1})
	body, _ := proto.Marshal(profilesRequest())

	if first := postTo(t, d, otlp.ProfilesPath, body, nil); first.Code != http.StatusOK {
		t.Fatalf("first profiles export: status = %d, want 200", first.Code)
	}
	if second := postTo(t, d, otlp.ProfilesPath, body, nil); second.Code != http.StatusTooManyRequests {
		t.Fatalf("second profiles export: status = %d, want 429 (the bucket is spent)", second.Code)
	}

	spans := post(t, d, encodedRequest(t, exportRequest(traceID(0x21))), nil)
	if spans.Code != http.StatusOK {
		t.Errorf("traces after a spent profiles bucket: status = %d, want 200", spans.Code)
	}
}

// Profiles spend no allowance in preview (D1), so the door bounds the samples one key
// sends a second: past it, a retryable refusal that says samples, and the key's
// spans untouched.
func TestProfileSamplesPastTheKeysBudgetAreRefusedAndSpansAreNot(t *testing.T) {
	d := newDoor(t, doorOptions{counter: &keyedCounter{}, requestsPerSec: 100, samplesPerSec: 3})
	request := profilesRequest()
	profile := request.GetResourceProfiles()[0].GetScopeProfiles()[0].GetProfiles()[0]
	profile.Samples = append(profile.Samples, profile.GetSamples()[0], profile.GetSamples()[0])
	body, _ := proto.Marshal(request) // three samples: the whole budget

	if first := postTo(t, d, otlp.ProfilesPath, body, nil); first.Code != http.StatusOK {
		t.Fatalf("first export: status = %d, want 200", first.Code)
	}
	second := postTo(t, d, otlp.ProfilesPath, body, nil)
	if second.Code != http.StatusTooManyRequests {
		t.Fatalf("second export: status = %d, want 429 (the samples are spent)", second.Code)
	}
	if second.Header().Get("Retry-After") == "" {
		t.Error("a refusal a sender should retry must say when")
	}
	if !strings.Contains(second.Body.String(), "profile samples") {
		t.Errorf("body = %q, want it to name the samples limit", second.Body.String())
	}
	if len(d.producer.records) != 1 {
		t.Errorf("published %d records, want only the first export's", len(d.producer.records))
	}

	spans := post(t, d, encodedRequest(t, exportRequest(traceID(0x22))), nil)
	if spans.Code != http.StatusOK {
		t.Errorf("traces after a spent sample budget: status = %d, want 200", spans.Code)
	}
}

// A request that expands past the frames one request may hold is refused as too
// large, with the reason — not converted into gigabytes first.
func TestAProfileThatExpandsPastTheFrameBoundIsTooLarge(t *testing.T) {
	d := newDoor(t, doorOptions{maxBodyBytes: 4 << 20})
	request := profilesRequest()
	dictionary := request.GetDictionary()
	deep := make([]int32, 20_000)
	for index := range deep {
		deep[index] = 1
	}
	dictionary.StackTable = append(dictionary.StackTable, &profilespb.Stack{LocationIndices: deep})
	profile := request.GetResourceProfiles()[0].GetScopeProfiles()[0].GetProfiles()[0]
	profile.Samples = nil
	for range 51 {
		profile.Samples = append(profile.Samples, &profilespb.Sample{
			StackIndex: int32(len(dictionary.GetStackTable()) - 1), Values: []int64{1},
		})
	}
	body, _ := proto.Marshal(request)

	response := postTo(t, d, otlp.ProfilesPath, body, nil)

	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d, want 413: %s", response.Code, response.Body.String())
	}
	if len(d.producer.records) != 0 {
		t.Errorf("published %d records, want none", len(d.producer.records))
	}
}

// A request whose stacks, written out, outgrow the record limit is published as
// several records, each under it — never refused by the broker and retried for ever.
func TestAProfileLargerWrittenOutThanARecordIsPublishedInParts(t *testing.T) {
	const bodyLimit = 64 << 10 // the fixture's decoded limit is four times this
	d := newDoor(t, doorOptions{maxBodyBytes: bodyLimit})
	request := profilesRequest()
	dictionary := request.GetDictionary()
	var deep []int32
	for index := range 200 {
		dictionary.StringTable = append(dictionary.StringTable, strings.Repeat("f", 90)+string(rune('a'+index%26)))
		dictionary.FunctionTable = append(dictionary.FunctionTable,
			&profilespb.Function{NameStrindex: int32(len(dictionary.GetStringTable()) - 1)})
		dictionary.LocationTable = append(dictionary.LocationTable, &profilespb.Location{
			Lines: []*profilespb.Line{{FunctionIndex: int32(len(dictionary.GetFunctionTable()) - 1)}},
		})
		deep = append(deep, int32(len(dictionary.GetLocationTable())-1))
	}
	dictionary.StackTable = append(dictionary.StackTable, &profilespb.Stack{LocationIndices: deep})
	profile := request.GetResourceProfiles()[0].GetScopeProfiles()[0].GetProfiles()[0]
	profile.Samples = nil
	for range 200 {
		profile.Samples = append(profile.Samples, &profilespb.Sample{
			StackIndex: int32(len(dictionary.GetStackTable()) - 1), Values: []int64{1},
		})
	}
	body, err := proto.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}

	response := postTo(t, d, otlp.ProfilesPath, body, nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", response.Code, response.Body.String())
	}
	if len(d.producer.records) < 2 {
		t.Fatalf("published %d records, want the conversion split into several", len(d.producer.records))
	}
	samples := 0
	for _, record := range d.producer.records {
		var envelope nexovuev1.Envelope
		if err := proto.Unmarshal(record.Value, &envelope); err != nil {
			t.Fatal(err)
		}
		if size := len(envelope.GetPayload()); size > 4*bodyLimit {
			t.Errorf("a part's payload is %d bytes, over the %d limit", size, 4*bodyLimit)
		}
		var part nexovuev1.ProfileSamples
		if err := proto.Unmarshal(envelope.GetPayload(), &part); err != nil {
			t.Fatal(err)
		}
		for _, batch := range part.GetBatches() {
			samples += len(batch.GetSamples())
		}
	}
	if samples != 200 {
		t.Errorf("samples across records = %d, want all 200", samples)
	}
}
