package dedupe_test

import (
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dedupe"
)

// The whole point: a replayed batch must not be counted twice.
func TestARedeliveredRecordIsNotAFirstDelivery(t *testing.T) {
	offsets := dedupe.NewOffsets()

	for _, offset := range []int64{10, 11, 12} {
		if !offsets.FirstDelivery("spans-raw", 3, offset) {
			t.Fatalf("offset %d was refused on its first delivery", offset)
		}
	}
	for _, offset := range []int64{10, 11, 12} {
		if offsets.FirstDelivery("spans-raw", 3, offset) {
			t.Errorf("offset %d was accepted twice; a replay would double-count it", offset)
		}
	}
	if !offsets.FirstDelivery("spans-raw", 3, 13) {
		t.Error("the next offset after a replay was refused")
	}
}

// The direction that makes this type dangerous rather than useful. When a
// partition leaves, the stages drop whatever they were holding for it — so its
// records are unprocessed again. A watermark left behind would recognise them
// as replays and skip them, and nothing would ever write those spans.
func TestAForgottenPartitionIsDeliveredAgain(t *testing.T) {
	offsets := dedupe.NewOffsets()
	offsets.FirstDelivery("spans-raw", 3, 100)
	offsets.FirstDelivery("spans-raw", 4, 100)

	offsets.Forget("spans-raw", []int32{3})

	if !offsets.FirstDelivery("spans-raw", 3, 100) {
		t.Error("a forgotten partition's record was still treated as a replay; " +
			"its spans were dropped, not stored, so skipping them is a hole")
	}
	if offsets.FirstDelivery("spans-raw", 4, 100) {
		t.Error("a partition this worker still owns forgot its watermark")
	}
}

func TestPartitionsAndTopicsAreTrackedApart(t *testing.T) {
	offsets := dedupe.NewOffsets()
	offsets.FirstDelivery("spans-raw", 0, 100)

	cases := map[string]struct {
		topic     string
		partition int32
		offset    int64
	}{
		"same offset on another partition": {"spans-raw", 1, 100},
		"same offset on another topic":     {"logs-raw", 0, 100},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if !offsets.FirstDelivery(tc.topic, tc.partition, tc.offset) {
				t.Error("a different partition's offset was treated as a replay")
			}
		})
	}
	if offsets.Partitions() != 3 {
		t.Errorf("Partitions() = %d, want one entry per topic and partition", offsets.Partitions())
	}
}
