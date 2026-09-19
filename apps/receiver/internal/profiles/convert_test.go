package profiles

import (
	"errors"
	"math"
	"os"
	"strings"
	"testing"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/profiles/v1development"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	profilespb "go.opentelemetry.io/proto/otlp/profiles/v1development"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	"google.golang.org/protobuf/proto"
)

// The strings the dictionary below indexes, by position.
var dictionaryStrings = []string{
	"",                       // 0
	"cpu",                    // 1
	"nanoseconds",            // 2
	"main.(*Cart).Checkout",  // 3
	"cart.go",                // 4
	"crypto/sha256.block",    // 5
	"sha256block.go",         // 6
	"libc.so.6",              // 7
	"thread.name",            // 8
	"_ZN4core3fmt5write17h1", // 9  a system (mangled) name only
}

// request builds a profiles export with one CPU profile: a named two-frame stack
// sampled twice and linked to a span, an unnamed native frame, and a location whose
// only name is its system name.
func request() *collectorpb.ExportProfilesServiceRequest {
	dictionary := &profilespb.ProfilesDictionary{
		StringTable: dictionaryStrings,
		MappingTable: []*profilespb.Mapping{
			{},
			{FilenameStrindex: 7},
		},
		FunctionTable: []*profilespb.Function{
			{},
			{NameStrindex: 3, FilenameStrindex: 4},
			{NameStrindex: 5, FilenameStrindex: 6},
			{SystemNameStrindex: 9},
		},
		LocationTable: []*profilespb.Location{
			{},
			{Lines: []*profilespb.Line{{FunctionIndex: 1, Line: 112}}},
			{Lines: []*profilespb.Line{{FunctionIndex: 2, Line: 41}}},
			{MappingIndex: 1, Address: 0x9A4F0},
			{Lines: []*profilespb.Line{{FunctionIndex: 3}}},
		},
		StackTable: []*profilespb.Stack{
			{},
			{LocationIndices: []int32{2, 1}},
			{LocationIndices: []int32{3, 1}},
			{LocationIndices: []int32{4}},
		},
		LinkTable: []*profilespb.Link{
			{},
			{TraceId: []byte("0123456789abcdef"), SpanId: []byte("span0001")},
		},
		AttributeTable: []*profilespb.KeyValueAndUnit{
			{},
			{KeyStrindex: 8, Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: "worker-3"}}},
		},
	}
	profile := &profilespb.Profile{
		SampleType:   &profilespb.ValueType{TypeStrindex: 1, UnitStrindex: 2},
		PeriodType:   &profilespb.ValueType{TypeStrindex: 1, UnitStrindex: 2},
		Period:       10_000_000,
		TimeUnixNano: 1_000,
		Samples: []*profilespb.Sample{
			{StackIndex: 1, Values: []int64{20_000_000, 10_000_000}, LinkIndex: 1, AttributeIndices: []int32{1}, TimestampsUnixNano: []uint64{1_500, 1_600}},
			{StackIndex: 2, Values: []int64{10_000_000}},
			{StackIndex: 3, TimestampsUnixNano: []uint64{1_700, 1_800, 1_900}},
		},
	}
	return &collectorpb.ExportProfilesServiceRequest{
		Dictionary: dictionary,
		ResourceProfiles: []*profilespb.ResourceProfiles{{
			Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{
				{Key: "service.name", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: "checkout"}}},
			}},
			ScopeProfiles: []*profilespb.ScopeProfiles{{Profiles: []*profilespb.Profile{profile}}},
		}},
	}
}

func TestAProfileBecomesOneBatchOfItsSamples(t *testing.T) {
	converted, err := Convert(request())
	if err != nil {
		t.Fatalf("Convert: %v", err)
	}
	if len(converted.GetBatches()) != 1 {
		t.Fatalf("batches = %d, want 1", len(converted.GetBatches()))
	}
	batch := converted.GetBatches()[0]
	if batch.GetSampleType() != "cpu" || batch.GetSampleUnit() != "nanoseconds" || batch.GetPeriod() != 10_000_000 {
		t.Errorf("type = %s/%s period %d, want cpu/nanoseconds 10000000",
			batch.GetSampleType(), batch.GetSampleUnit(), batch.GetPeriod())
	}
	if got := batch.GetResourceAttributes()[0].GetValue().GetStringValue(); got != "checkout" {
		t.Errorf("resource service.name = %q, want checkout", got)
	}
	if len(batch.GetSamples()) != 3 {
		t.Fatalf("samples = %d, want 3", len(batch.GetSamples()))
	}
}

func TestAStackIsResolvedLeafFirstWithFilesAndLines(t *testing.T) {
	converted, _ := Convert(request())
	frames := converted.GetBatches()[0].GetSamples()[0].GetFrames()
	if len(frames) != 2 {
		t.Fatalf("frames = %d, want 2", len(frames))
	}
	leaf, caller := frames[0], frames[1]
	if leaf.GetFunction() != "crypto/sha256.block" || leaf.GetFile() != "sha256block.go" || leaf.GetLine() != 41 {
		t.Errorf("leaf = %v, want crypto/sha256.block sha256block.go:41", leaf)
	}
	if caller.GetFunction() != "main.(*Cart).Checkout" || caller.GetLine() != 112 {
		t.Errorf("caller = %v, want main.(*Cart).Checkout :112", caller)
	}
}

func TestValuesAreSummedAndTheSpanLinkAndAttributesKept(t *testing.T) {
	converted, _ := Convert(request())
	sample := converted.GetBatches()[0].GetSamples()[0]
	if sample.GetValue() != 30_000_000 {
		t.Errorf("value = %d, want 30000000 (the two values summed)", sample.GetValue())
	}
	if sample.GetTimeUnixNano() != 1_500 {
		t.Errorf("time = %d, want the first timestamp 1500", sample.GetTimeUnixNano())
	}
	if string(sample.GetTraceId()) != "0123456789abcdef" || string(sample.GetSpanId()) != "span0001" {
		t.Errorf("link = %x/%x, want the linked span", sample.GetTraceId(), sample.GetSpanId())
	}
	if len(sample.GetAttributes()) != 1 || sample.GetAttributes()[0].GetKey() != "thread.name" {
		t.Errorf("attributes = %v, want thread.name", sample.GetAttributes())
	}
}

func TestAFrameWithNoNameKeepsItsBinaryAndAddress(t *testing.T) {
	converted, _ := Convert(request())
	leaf := converted.GetBatches()[0].GetSamples()[1].GetFrames()[0]
	if leaf.GetFunction() != "" || leaf.GetBinary() != "libc.so.6" || leaf.GetAddress() != 0x9A4F0 {
		t.Errorf("unnamed leaf = %v, want libc.so.6 at 0x9a4f0 and no function", leaf)
	}
}

func TestASystemNameStandsInWhenThereIsNoName(t *testing.T) {
	converted, _ := Convert(request())
	frame := converted.GetBatches()[0].GetSamples()[2].GetFrames()[0]
	if frame.GetFunction() != "_ZN4core3fmt5write17h1" {
		t.Errorf("function = %q, want the system name", frame.GetFunction())
	}
}

func TestTimestampsOnlyCountOneEachInTheSampleTypesUnit(t *testing.T) {
	converted, _ := Convert(request())
	sample := converted.GetBatches()[0].GetSamples()[2]
	// The value is in the sample type's unit; the period only says how often the
	// profiler looked, and multiplying by it would change the unit under the label.
	if sample.GetValue() != 3 {
		t.Errorf("value = %d, want 3 occurrences", sample.GetValue())
	}
	if sample.GetTimeUnixNano() != 1_700 {
		t.Errorf("time = %d, want the first timestamp 1700", sample.GetTimeUnixNano())
	}
}

func TestADictionaryEncodedResourceIsResolvedToPlainStrings(t *testing.T) {
	r := request()
	r.ResourceProfiles[0].Resource.Attributes = []*commonpb.KeyValue{
		{KeyStrindex: 8, Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValueStrindex{StringValueStrindex: 3}}},
	}
	converted, err := Convert(r)
	if err != nil {
		t.Fatalf("Convert: %v", err)
	}
	got := converted.GetBatches()[0].GetResourceAttributes()
	if len(got) != 1 || got[0].GetKey() != "thread.name" || got[0].GetValue().GetStringValue() != "main.(*Cart).Checkout" {
		t.Errorf("resource attributes = %v, want thread.name = main.(*Cart).Checkout as plain strings", got)
	}
}

// The first export of the OpenTelemetry eBPF profiler 0.161.0 against the
// OpenTelemetry Demo, captured on the development stack: every key and string
// value in its resources is an index into the string table, and every sample is
// timestamps only.
func TestTheEBPFProfilersExportConvertsWithItsServicesAndCounts(t *testing.T) {
	raw, err := os.ReadFile("testdata/ebpf-profiler-0.161.0.binpb")
	if err != nil {
		t.Fatal(err)
	}
	var captured collectorpb.ExportProfilesServiceRequest
	if err := proto.Unmarshal(raw, &captured); err != nil {
		t.Fatal(err)
	}
	occurrences := int64(0)
	for _, resource := range captured.GetResourceProfiles() {
		for _, scope := range resource.GetScopeProfiles() {
			for _, profile := range scope.GetProfiles() {
				for _, sample := range profile.GetSamples() {
					occurrences += int64(len(sample.GetTimestampsUnixNano()))
				}
			}
		}
	}

	converted, err := Convert(&captured)
	if err != nil {
		t.Fatalf("Convert: %v", err)
	}
	services := map[string]bool{}
	total := int64(0)
	for _, batch := range converted.GetBatches() {
		if batch.GetSampleType() != "samples" || batch.GetSampleUnit() != "count" {
			t.Errorf("type = %s/%s, want samples/count", batch.GetSampleType(), batch.GetSampleUnit())
		}
		for _, attribute := range batch.GetResourceAttributes() {
			if attribute.GetKey() == "" {
				t.Fatalf("a resource attribute lost its key: %v", attribute)
			}
			if attribute.GetKey() == "service.name" {
				services[attribute.GetValue().GetStringValue()] = true
			}
		}
		for _, sample := range batch.GetSamples() {
			total += sample.GetValue()
		}
	}
	for _, want := range []string{"payment", "recommendation", "frontend-proxy"} {
		if !services[want] {
			t.Errorf("service %q missing; services = %v", want, services)
		}
	}
	if total != occurrences {
		t.Errorf("summed value = %d, want the %d occurrences the profiler saw", total, occurrences)
	}
}

func readCapture(t *testing.T, name string) *collectorpb.ExportProfilesServiceRequest {
	t.Helper()
	raw, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatal(err)
	}
	var captured collectorpb.ExportProfilesServiceRequest
	if err := proto.Unmarshal(raw, &captured); err != nil {
		t.Fatal(err)
	}
	return &captured
}

// The contrib Collector's pprof receiver 0.161.0, scraping a Go service's
// /debug/pprof/heap. It does not keep OTLP's "entry zero is empty" rule: string,
// stack, location, function, mapping and attribute index 0 are real entries. The
// reference Collector sending it is the reason indices are read as they stand.
func TestThePprofReceiversHeapExportConvertsIntoOneBatchPerType(t *testing.T) {
	converted, err := Convert(readCapture(t, "pprof-receiver-0.161.0-heap.binpb"))
	if err != nil {
		t.Fatalf("Convert: %v", err)
	}
	types := map[string]string{}
	named := 0
	for _, batch := range converted.GetBatches() {
		types[batch.GetSampleType()] = batch.GetSampleUnit()
		for _, sample := range batch.GetSamples() {
			if sample.GetValue() <= 0 || len(sample.GetFrames()) == 0 {
				t.Fatalf("sample %v kept with no value or no frames", sample)
			}
			if strings.HasPrefix(sample.GetFrames()[0].GetFunction(), "runtime.") ||
				strings.Contains(sample.GetFrames()[0].GetFunction(), "nexovue") {
				named++
			}
		}
	}
	want := map[string]string{
		"inuse_space": "bytes", "alloc_space": "bytes", "inuse_objects": "count", "alloc_objects": "count",
	}
	for kind, unit := range want {
		if types[kind] != unit {
			t.Errorf("type %s = %q, want %q; types = %v", kind, types[kind], unit, types)
		}
	}
	if named == 0 {
		t.Error("no leaf frame resolved to a Go function name")
	}
}

func TestSamplesAreCountedBeforeConversion(t *testing.T) {
	if got := SampleCount(request()); got != 3 {
		t.Errorf("SampleCount = %d, want 3", got)
	}
}

func TestMalformedRequestsAreRefusedWithTheirReason(t *testing.T) {
	cases := []struct {
		name    string
		malform func(*collectorpb.ExportProfilesServiceRequest)
		reason  string
	}{
		{"a stack index past the table", func(r *collectorpb.ExportProfilesServiceRequest) {
			r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples[0].StackIndex = 99
		}, "stack_table index 99"},
		{"a location index past the table", func(r *collectorpb.ExportProfilesServiceRequest) {
			r.Dictionary.StackTable[1].LocationIndices = []int32{42}
		}, "location_table index 42"},
		{"a function index past the table", func(r *collectorpb.ExportProfilesServiceRequest) {
			r.Dictionary.LocationTable[1].Lines[0].FunctionIndex = 7
		}, "function_table index 7"},
		{"a string index past the table", func(r *collectorpb.ExportProfilesServiceRequest) {
			r.Dictionary.FunctionTable[1].NameStrindex = 500
		}, "string_table index 500"},
		{"a link index past the table", func(r *collectorpb.ExportProfilesServiceRequest) {
			r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples[0].LinkIndex = 3
		}, "link_table index 3"},
		{"an attribute index past the table", func(r *collectorpb.ExportProfilesServiceRequest) {
			r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples[0].AttributeIndices = []int32{9}
		}, "attribute_table index 9"},
		{"a mapping index past the table", func(r *collectorpb.ExportProfilesServiceRequest) {
			r.Dictionary.LocationTable[3].MappingIndex = 4
		}, "mapping_table index 4"},
		{"a resource key index past the string table", func(r *collectorpb.ExportProfilesServiceRequest) {
			r.ResourceProfiles[0].Resource.Attributes[0] = &commonpb.KeyValue{KeyStrindex: 77}
		}, "string_table index 77"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			broken := request()
			c.malform(broken)
			_, err := Convert(broken)
			if !errors.Is(err, ErrMalformed) {
				t.Fatalf("err = %v, want ErrMalformed", err)
			}
			if !strings.Contains(err.Error(), c.reason) {
				t.Errorf("err = %q, want it to name %q", err, c.reason)
			}
		})
	}
}

func TestAnEmptyStackOrZeroValueIsDroppedNotRefused(t *testing.T) {
	r := request()
	samples := r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples
	samples[1].StackIndex = 0
	samples[2].TimestampsUnixNano = nil
	converted, err := Convert(r)
	if err != nil {
		t.Fatalf("Convert: %v", err)
	}
	if got := len(converted.GetBatches()[0].GetSamples()); got != 1 {
		t.Errorf("samples = %d, want 1: an empty stack and a valueless sample show nothing", got)
	}
}

// The receiver's conversion cost on a real export, recorded in the feature's status.
func BenchmarkConvertTheEBPFProfilersExport(b *testing.B) {
	raw, err := os.ReadFile("testdata/ebpf-profiler-0.161.0.binpb")
	if err != nil {
		b.Fatal(err)
	}
	var captured collectorpb.ExportProfilesServiceRequest
	if err := proto.Unmarshal(raw, &captured); err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	b.SetBytes(int64(len(raw)))
	for b.Loop() {
		if _, err := Convert(&captured); err != nil {
			b.Fatal(err)
		}
	}
}

// One deep stack named by many samples is small on the wire and enormous written
// out; past MaxFrames it is refused before it is written (measured: 223 KB became
// 3.2 GB without the bound).
func TestAStackWrittenOutPastTheFrameBoundIsRefused(t *testing.T) {
	r := request()
	dictionary := r.GetDictionary()
	deep := make([]int32, 20_000)
	for index := range deep {
		deep[index] = 1
	}
	dictionary.StackTable = append(dictionary.StackTable, &profilespb.Stack{LocationIndices: deep})
	profile := r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0]
	profile.Samples = nil
	for range MaxFrames/len(deep) + 1 {
		profile.Samples = append(profile.Samples, &profilespb.Sample{
			StackIndex: int32(len(dictionary.GetStackTable()) - 1), Values: []int64{1},
		})
	}

	_, err := Convert(r)

	if !errors.Is(err, ErrTooLarge) {
		t.Fatalf("err = %v, want ErrTooLarge", err)
	}
}

// An attribute-table entry named by every sample is resolved once and shared, not
// copied per sample (measured: a 496 KB request became a gigabyte that way).
func TestAnAttributeNamedByManySamplesIsResolvedOnce(t *testing.T) {
	r := request()
	samples := r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples
	samples[1].AttributeIndices = []int32{1}

	converted, err := Convert(r)
	if err != nil {
		t.Fatal(err)
	}

	got := converted.GetBatches()[0].GetSamples()
	if got[0].GetAttributes()[0] != got[1].GetAttributes()[0] {
		t.Error("the same table entry was resolved twice")
	}
}

func TestValuesThatOverflowAreRefused(t *testing.T) {
	r := request()
	r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples[0].Values = []int64{math.MaxInt64, 1}

	_, err := Convert(r)

	if !errors.Is(err, ErrMalformed) || !strings.Contains(err.Error(), "overflow") {
		t.Fatalf("err = %v, want an overflow refusal", err)
	}
}

// Index zero into a table the request did not send is "none" everywhere, not a
// refusal in some tables and an absence in others.
func TestIndexZeroIntoAnAbsentTableIsNoneInEveryTable(t *testing.T) {
	r := request()
	r.Dictionary.FunctionTable = nil
	r.Dictionary.AttributeTable = nil
	r.Dictionary.LocationTable[1].Lines = []*profilespb.Line{{FunctionIndex: 0}}
	r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples[0].AttributeIndices = []int32{0}
	// The remaining samples point at functions that are now gone.
	r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples =
		r.ResourceProfiles[0].ScopeProfiles[0].Profiles[0].Samples[:1]
	r.Dictionary.StackTable[1].LocationIndices = []int32{1}

	converted, err := Convert(r)

	if err != nil {
		t.Fatalf("Convert: %v", err)
	}
	frame := converted.GetBatches()[0].GetSamples()[0].GetFrames()[0]
	if frame.GetFunction() != "" {
		t.Errorf("frame = %v, want an unnamed frame shown by its binary", frame)
	}
}
