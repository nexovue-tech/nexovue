// Package profiles converts OTLP profiles into nexovue's own ProfileSamples.
//
// It is the only code in nexovue that reads OTLP profiles (ADR-015). The format is
// still Development — `opentelemetry.proto.profiles.v1development` — and has broken
// compatibility between releases, so it stops here: what the receiver publishes, and
// everything behind it stores and reads, is `nexovuev1.ProfileSamples`. Supporting a
// new OTLP profiles version is a new converter beside this one, nothing else.
//
// An OTLP profile is a dictionary of tables — strings, functions, locations, mappings,
// stacks, links, attributes — that samples point into by index. Converting resolves
// every index once, so no consumer ever handles an index, and refuses a request whose
// indices point outside their table: a malformed profile is refused at the door, with
// a reason the sender's Collector logs, not accepted and then lost in a worker.
//
// Indices are read as they stand. OTLP says entry zero of every table is the empty
// value, so that index zero means "none"; the contrib Collector's pprof receiver does
// not keep that rule and puts real entries there. Reading literally serves both: a
// conforming table's zero entry is empty, so "none" falls out of it unasked.
package profiles

import (
	"errors"
	"fmt"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/profiles/v1development"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	profilespb "go.opentelemetry.io/proto/otlp/profiles/v1development"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// ErrMalformed is what every refusal wraps, so the door can answer 400 with the
// reason while telling it apart from anything else that went wrong.
var ErrMalformed = errors.New("malformed OTLP profiles request")

// ErrTooLarge is a request whose stacks, written out, pass MaxFrames. Every sample
// carries its whole stack after conversion, so a small request naming one deep stack
// many times can expand without bound; it is refused before it does.
var ErrTooLarge = errors.New("OTLP profiles request expands past the frames one request may hold")

// MaxFrames bounds the frames one request converts into, across all its samples. A
// real eBPF export writes out to about ten thousand; this leaves two orders of
// magnitude for large fleets batched by a Collector, and bounds the worst request
// near fifty megabytes rather than gigabytes.
const MaxFrames = 1_000_000

// Convert turns one OTLP profiles export request into ProfileSamples: one batch per
// profile (a resource and a profile type), each sample with its stack as frames, leaf
// first. A request that carries samples must carry the dictionary they point into.
func Convert(request *collectorpb.ExportProfilesServiceRequest) (*nexovuev1.ProfileSamples, error) {
	tables := &dictionary{
		source:   request.GetDictionary(),
		stacks:   map[int32][]*nexovuev1.Frame{},
		resolved: map[int32]*commonpb.KeyValue{},
	}

	converted := &nexovuev1.ProfileSamples{}
	for _, resource := range request.GetResourceProfiles() {
		resourceAttributes, err := tables.keyValues(resource.GetResource().GetAttributes())
		if err != nil {
			return nil, fmt.Errorf("%w: resource attribute", err)
		}
		for _, scope := range resource.GetScopeProfiles() {
			for _, profile := range scope.GetProfiles() {
				batch, err := tables.batch(profile, resourceAttributes)
				if err != nil {
					return nil, err
				}
				if len(batch.GetSamples()) > 0 {
					converted.Batches = append(converted.Batches, batch)
				}
			}
		}
	}
	return converted, nil
}

// SampleCount is how many samples a request carries, before conversion: the unit
// the door counts what it accepted and refused in.
func SampleCount(request *collectorpb.ExportProfilesServiceRequest) int {
	count := 0
	for _, resource := range request.GetResourceProfiles() {
		for _, scope := range resource.GetScopeProfiles() {
			for _, profile := range scope.GetProfiles() {
				count += len(profile.GetSamples())
			}
		}
	}
	return count
}

// SampleCountOf is how many samples a conversion kept — what the door's sample
// budget is spent in.
func SampleCountOf(converted *nexovuev1.ProfileSamples) int {
	count := 0
	for _, batch := range converted.GetBatches() {
		count += len(batch.GetSamples())
	}
	return count
}

// dictionary resolves indices into the request's shared tables, and remembers each
// stack and attribute it has resolved: an eBPF profile repeats a few thousand stacks
// many times, and a table entry resolved once per referring sample is a copy per
// sample — measured, a 496 KB request became a gigabyte that way.
type dictionary struct {
	source   *profilespb.ProfilesDictionary
	stacks   map[int32][]*nexovuev1.Frame
	resolved map[int32]*commonpb.KeyValue
	// frames counts every frame written out so far, against MaxFrames.
	frames int
}

func (d *dictionary) batch(
	profile *profilespb.Profile,
	resourceAttributes []*commonpb.KeyValue,
) (*nexovuev1.ProfileBatch, error) {
	sampleType, err := d.valueType(profile.GetSampleType(), "sample_type")
	if err != nil {
		return nil, err
	}
	periodType, err := d.valueType(profile.GetPeriodType(), "period_type")
	if err != nil {
		return nil, err
	}
	profileAttributes, err := d.attributes(profile.GetAttributeIndices(), "profile")
	if err != nil {
		return nil, err
	}

	batch := &nexovuev1.ProfileBatch{
		ResourceAttributes: resourceAttributes,
		SampleType:         sampleType[0],
		SampleUnit:         sampleType[1],
		Period:             profile.GetPeriod(),
		PeriodType:         periodType[0],
		PeriodUnit:         periodType[1],
	}
	for position, sample := range profile.GetSamples() {
		converted, err := d.sample(profile, sample, profileAttributes)
		if err != nil {
			return nil, fmt.Errorf("%w (sample %d)", err, position)
		}
		if converted != nil {
			batch.Samples = append(batch.Samples, converted)
		}
	}
	return batch, nil
}

// sample converts one sample, or returns nil for one with nothing to show: an empty
// stack or no value at all.
func (d *dictionary) sample(
	profile *profilespb.Profile,
	sample *profilespb.Sample,
	profileAttributes []*commonpb.KeyValue,
) (*nexovuev1.Sample, error) {
	frames, err := d.stack(sample.GetStackIndex())
	if err != nil {
		return nil, err
	}
	value, err := sampleValue(sample)
	if err != nil {
		return nil, err
	}
	if len(frames) == 0 || value == 0 {
		return nil, nil
	}
	d.frames += len(frames)
	if d.frames > MaxFrames {
		return nil, fmt.Errorf("%w: more than %d frames; send smaller batches", ErrTooLarge, MaxFrames)
	}
	own, err := d.attributes(sample.GetAttributeIndices(), "sample")
	if err != nil {
		return nil, err
	}

	converted := &nexovuev1.Sample{
		Frames:       frames,
		Value:        value,
		TimeUnixNano: profile.GetTimeUnixNano(),
		Attributes:   append(append([]*commonpb.KeyValue{}, profileAttributes...), own...),
	}
	if timestamps := sample.GetTimestampsUnixNano(); len(timestamps) > 0 {
		converted.TimeUnixNano = timestamps[0]
	}
	if index := sample.GetLinkIndex(); !absent(index, len(d.source.GetLinkTable())) {
		link, err := d.link(index)
		if err != nil {
			return nil, err
		}
		converted.TraceId = link.GetTraceId()
		converted.SpanId = link.GetSpanId()
	}
	return converted, nil
}

// sampleValue sums a sample's values. A sample may instead carry only timestamps —
// one per occurrence — and is then worth one per occurrence, in the sample type's
// unit. The period is how often the profiler looked, not part of the value.
func sampleValue(sample *profilespb.Sample) (int64, error) {
	values := sample.GetValues()
	if len(values) == 0 {
		return int64(len(sample.GetTimestampsUnixNano())), nil
	}
	var total int64
	for _, value := range values {
		sum := total + value
		// Overflow flips the sign against both operands.
		if (value > 0 && sum < total) || (value < 0 && sum > total) {
			return 0, fmt.Errorf("%w: a sample's values overflow", ErrMalformed)
		}
		total = sum
	}
	return total, nil
}

// stack resolves a stack into frames, leaf first. A location with inlined functions
// becomes one frame per function, the inlined callee first, as OTLP orders them.
func (d *dictionary) stack(index int32) ([]*nexovuev1.Frame, error) {
	if frames, seen := d.stacks[index]; seen {
		return frames, nil
	}
	table := d.source.GetStackTable()
	if absent(index, len(table)) {
		return nil, nil
	}
	if index < 0 || int(index) >= len(table) {
		return nil, outOfRange("stack_table", index, len(table))
	}

	var frames []*nexovuev1.Frame
	for _, locationIndex := range table[index].GetLocationIndices() {
		located, err := d.location(locationIndex)
		if err != nil {
			return nil, err
		}
		frames = append(frames, located...)
	}
	d.stacks[index] = frames
	return frames, nil
}

func (d *dictionary) location(index int32) ([]*nexovuev1.Frame, error) {
	table := d.source.GetLocationTable()
	if absent(index, len(table)) {
		return nil, nil
	}
	if index < 0 || int(index) >= len(table) {
		return nil, outOfRange("location_table", index, len(table))
	}
	location := table[index]

	var frames []*nexovuev1.Frame
	for _, line := range location.GetLines() {
		frame, err := d.function(line)
		if err != nil {
			return nil, err
		}
		if frame != nil {
			frames = append(frames, frame)
		}
	}
	if len(frames) > 0 {
		return frames, nil
	}
	// Nothing named it — native code the profiler had no symbols for. Kept as the
	// binary and the address, so it shows as unsymbolized rather than vanishing.
	binary, err := d.mappingFile(location.GetMappingIndex())
	if err != nil {
		return nil, err
	}
	return []*nexovuev1.Frame{{Binary: binary, Address: location.GetAddress()}}, nil
}

// function is one line of a location, or nil when its function has no name.
func (d *dictionary) function(line *profilespb.Line) (*nexovuev1.Frame, error) {
	table := d.source.GetFunctionTable()
	index := line.GetFunctionIndex()
	if absent(index, len(table)) {
		return nil, nil
	}
	if index < 0 || int(index) >= len(table) {
		return nil, outOfRange("function_table", index, len(table))
	}
	function := table[index]
	name, err := d.text(function.GetNameStrindex())
	if err != nil {
		return nil, err
	}
	if name == "" {
		if name, err = d.text(function.GetSystemNameStrindex()); err != nil {
			return nil, err
		}
	}
	if name == "" {
		return nil, nil
	}
	file, err := d.text(function.GetFilenameStrindex())
	if err != nil {
		return nil, err
	}
	return &nexovuev1.Frame{Function: name, File: file, Line: clampLine(line.GetLine())}, nil
}

func (d *dictionary) mappingFile(index int32) (string, error) {
	table := d.source.GetMappingTable()
	if absent(index, len(table)) {
		return "", nil
	}
	if index < 0 || int(index) >= len(table) {
		return "", outOfRange("mapping_table", index, len(table))
	}
	return d.text(table[index].GetFilenameStrindex())
}

func (d *dictionary) link(index int32) (*profilespb.Link, error) {
	table := d.source.GetLinkTable()
	if index < 0 || int(index) >= len(table) {
		return nil, outOfRange("link_table", index, len(table))
	}
	return table[index], nil
}

// attributes resolves attribute indices into OTLP key-values — the type the masker
// already knows, so masking needs nothing new.
func (d *dictionary) attributes(indices []int32, owner string) ([]*commonpb.KeyValue, error) {
	if len(indices) == 0 {
		return nil, nil
	}
	table := d.source.GetAttributeTable()
	resolved := make([]*commonpb.KeyValue, 0, len(indices))
	for _, index := range indices {
		if absent(index, len(table)) {
			continue
		}
		if index < 0 || int(index) >= len(table) {
			return nil, fmt.Errorf("%w: %s attribute", outOfRange("attribute_table", index, len(table)), owner)
		}
		pair, err := d.attribute(index)
		if err != nil {
			return nil, err
		}
		if pair != nil {
			resolved = append(resolved, pair)
		}
	}
	return resolved, nil
}

// attribute resolves one attribute-table entry once, and shares it with every
// sample that names it: the output is only ever marshalled, never modified. Nil is
// a conforming table's zero entry, which is no attribute at all.
func (d *dictionary) attribute(index int32) (*commonpb.KeyValue, error) {
	if pair, seen := d.resolved[index]; seen {
		return pair, nil
	}
	entry := d.source.GetAttributeTable()[index]
	key, err := d.text(entry.GetKeyStrindex())
	if err != nil {
		return nil, err
	}
	var pair *commonpb.KeyValue
	if key != "" {
		value, err := d.anyValue(entry.GetValue())
		if err != nil {
			return nil, err
		}
		pair = &commonpb.KeyValue{Key: key, Value: value}
	}
	d.resolved[index] = pair
	return pair, nil
}

// keyValues resolves key-values whose key or string values may be indices into the
// string table, as the profiles signal allows anywhere it carries attributes.
func (d *dictionary) keyValues(pairs []*commonpb.KeyValue) ([]*commonpb.KeyValue, error) {
	if len(pairs) == 0 {
		return nil, nil
	}
	resolved := make([]*commonpb.KeyValue, 0, len(pairs))
	for _, pair := range pairs {
		key := pair.GetKey()
		if key == "" {
			text, err := d.text(pair.GetKeyStrindex())
			if err != nil {
				return nil, err
			}
			key = text
		}
		value, err := d.anyValue(pair.GetValue())
		if err != nil {
			return nil, err
		}
		resolved = append(resolved, &commonpb.KeyValue{Key: key, Value: value})
	}
	return resolved, nil
}

// anyValue replaces a string-table reference with the string, inside arrays and
// maps too, so nothing downstream ever meets an index.
func (d *dictionary) anyValue(value *commonpb.AnyValue) (*commonpb.AnyValue, error) {
	switch kind := value.GetValue().(type) {
	case *commonpb.AnyValue_StringValueStrindex:
		text, err := d.text(kind.StringValueStrindex)
		if err != nil {
			return nil, err
		}
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: text}}, nil
	case *commonpb.AnyValue_ArrayValue:
		items := make([]*commonpb.AnyValue, 0, len(kind.ArrayValue.GetValues()))
		for _, item := range kind.ArrayValue.GetValues() {
			resolved, err := d.anyValue(item)
			if err != nil {
				return nil, err
			}
			items = append(items, resolved)
		}
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_ArrayValue{ArrayValue: &commonpb.ArrayValue{Values: items}}}, nil
	case *commonpb.AnyValue_KvlistValue:
		pairs, err := d.keyValues(kind.KvlistValue.GetValues())
		if err != nil {
			return nil, err
		}
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_KvlistValue{KvlistValue: &commonpb.KeyValueList{Values: pairs}}}, nil
	default:
		return value, nil
	}
}

func (d *dictionary) valueType(value *profilespb.ValueType, field string) ([2]string, error) {
	if value == nil {
		return [2]string{}, nil
	}
	kind, err := d.text(value.GetTypeStrindex())
	if err != nil {
		return [2]string{}, fmt.Errorf("%s: %w", field, err)
	}
	unit, err := d.text(value.GetUnitStrindex())
	if err != nil {
		return [2]string{}, fmt.Errorf("%s: %w", field, err)
	}
	return [2]string{kind, unit}, nil
}

func (d *dictionary) text(index int32) (string, error) {
	table := d.source.GetStringTable()
	if absent(index, len(table)) {
		return "", nil
	}
	if index < 0 || int(index) >= len(table) {
		return "", outOfRange("string_table", index, len(table))
	}
	return table[index], nil
}

// absent is index zero into a table the request did not send: nothing referenced.
func absent(index int32, size int) bool {
	return index == 0 && size == 0
}

func outOfRange(table string, index int32, size int) error {
	return fmt.Errorf("%w: %s index %d is outside the table's %d entries", ErrMalformed, table, index, size)
}

// clampLine keeps a line number in the frame's field: OTLP sends int64, a source line
// never needs more than uint32, and a negative one is no line at all.
func clampLine(line int64) uint32 {
	switch {
	case line <= 0:
		return 0
	case line > 1<<32-1:
		return 1<<32 - 1
	default:
		return uint32(line)
	}
}
