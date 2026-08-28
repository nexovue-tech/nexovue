package ingeststate_test

import (
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

const spansTopic = "spans-raw"

func partitionOf(id int32) ingeststate.Partition {
	return ingeststate.Partition{Topic: spansTopic, ID: id}
}

// The range a batch claims is what the state machine classifies, so it has to
// bound each partition on its own: one partition's replay says nothing about
// another's, and merging them would count or skip a whole partition.
func TestRangesOfBoundEachPartitionSeparately(t *testing.T) {
	positions := []consume.Position{
		{Topic: spansTopic, Partition: 0, Offset: 5},
		{Topic: spansTopic, Partition: 1, Offset: 90},
		{Topic: spansTopic, Partition: 0, Offset: 3},
		{Topic: spansTopic, Partition: 0, Offset: 9},
	}

	ranges := ingeststate.RangesOf(positions)

	cases := map[int32]ingeststate.Range{
		0: {Min: 3, Max: 9},
		1: {Min: 90, Max: 90},
	}
	for id, want := range cases {
		if got := ranges[partitionOf(id)]; got != want {
			t.Errorf("partition %d = %+v, want %+v", id, got, want)
		}
	}
	if len(ranges) != len(cases) {
		t.Errorf("RangesOf covered %d partitions, want %d", len(ranges), len(cases))
	}
}

func TestRangesOfNothingClaimsNothing(t *testing.T) {
	if ranges := ingeststate.RangesOf(nil); len(ranges) != 0 {
		t.Errorf("RangesOf(nil) = %+v, want nothing claimed", ranges)
	}
}
