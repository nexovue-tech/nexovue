package profiles

import (
	"errors"
	"strings"
	"testing"

	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// A request's converted form can be far larger than the request itself — every
// sample carries its whole stack — and Kafka refuses a record past its limit, which
// the sender's Collector would retry for ever. Split keeps every part under it.
func TestALargeConversionIsSplitIntoPartsThatEachFitOneRecord(t *testing.T) {
	converted, err := Convert(readCapture(t, "pprof-receiver-0.161.0-heap.binpb"))
	if err != nil {
		t.Fatal(err)
	}
	limit := proto.Size(converted) / 5

	parts, err := Split(converted, limit)
	if err != nil {
		t.Fatalf("Split: %v", err)
	}

	if len(parts) < 5 {
		t.Fatalf("parts = %d, want at least 5 for a fifth of the size", len(parts))
	}
	samples, types := 0, map[string]bool{}
	for index, part := range parts {
		if size := proto.Size(part); size > limit {
			t.Errorf("part %d is %d bytes, over the %d limit", index, size, limit)
		}
		for _, batch := range part.GetBatches() {
			// A piece of a batch keeps what every sample in it is read by.
			if batch.GetSampleType() == "" || batch.GetSampleUnit() == "" {
				t.Errorf("part %d lost its batch's type: %v", index, batch.GetSampleType())
			}
			types[batch.GetSampleType()] = true
			samples += len(batch.GetSamples())
		}
	}
	if want := SampleCountOf(converted); samples != want {
		t.Errorf("samples across parts = %d, want the %d converted", samples, want)
	}
	if len(types) != 4 {
		t.Errorf("types across parts = %v, want all four", types)
	}
}

func TestAConversionThatFitsIsOnePart(t *testing.T) {
	converted, _ := Convert(request())
	parts, err := Split(converted, proto.Size(converted))
	if err != nil || len(parts) != 1 || parts[0] != converted {
		t.Fatalf("parts = %d, err = %v; want the conversion itself", len(parts), err)
	}
}

func TestOneSampleLargerThanARecordIsRefusedWithTheReason(t *testing.T) {
	frames := make([]*nexovuev1.Frame, 2000)
	for index := range frames {
		frames[index] = &nexovuev1.Frame{Function: strings.Repeat("f", 100)}
	}
	huge := &nexovuev1.ProfileSamples{Batches: []*nexovuev1.ProfileBatch{{
		SampleType: "cpu", SampleUnit: "nanoseconds",
		Samples: []*nexovuev1.Sample{{Frames: frames, Value: 1}},
	}}}

	_, err := Split(huge, 64<<10)

	if !errors.Is(err, ErrSampleTooLarge) {
		t.Fatalf("err = %v, want ErrSampleTooLarge", err)
	}
}

// A piece's length prefix is a varint that grows as the piece does; every limit,
// including those where it gains a byte, holds exactly.
func TestNoPartIsOverItsLimitWhereTheLengthPrefixGrows(t *testing.T) {
	converted, err := Convert(request())
	if err != nil {
		t.Fatal(err)
	}
	for _, batch := range converted.GetBatches() {
		for range 60 {
			batch.Samples = append(batch.Samples, batch.GetSamples()[0])
		}
	}
	for limit := 100; limit <= 400; limit++ {
		parts, err := Split(converted, limit)
		if err != nil {
			continue // a limit below one sample is refused, which is its own test
		}
		for _, part := range parts {
			if size := proto.Size(part); size > limit {
				t.Fatalf("limit %d: a part is %d bytes", limit, size)
			}
		}
	}
}
