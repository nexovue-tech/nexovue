package browserdoor

import (
	"testing"
	"time"
)

// The offered rate is across every visitor, not per visitor. Getting this
// backwards multiplies the load by the visitor count, which reads as the door
// being slow rather than as the generator sending twelve times too much.
func TestTheOfferedRateIsSharedAcrossVisitors(t *testing.T) {
	interval := perVisitorInterval(Options{Visitors: 10, PageLoadsPerSecond: 20})

	// 20 page loads/sec across 10 visitors is 2 each, so one every 500ms.
	if interval != 500*time.Millisecond {
		t.Errorf("interval = %v, want 500ms", interval)
	}
}

func TestAnUnthrottledRunHasNoWaitAtAll(t *testing.T) {
	cases := map[string]Options{
		"no rate given": {Visitors: 4, PageLoadsPerSecond: 0},
		"no visitors":   {Visitors: 0, PageLoadsPerSecond: 20},
		"negative rate": {Visitors: 4, PageLoadsPerSecond: -1},
	}
	for name, opts := range cases {
		t.Run(name, func(t *testing.T) {
			if interval := perVisitorInterval(opts); interval != 0 {
				t.Errorf("interval = %v, want 0", interval)
			}
		})
	}
}

// A run with nothing to authenticate with must say so rather than measure the
// door refusing every export.
func TestDrivingWithoutASiteKeyIsRefusedUpFront(t *testing.T) {
	if _, err := Drive(t.Context(), Options{Origin: "http://localhost:15173"}, nil); err == nil {
		t.Error("a run with no site key was allowed to start")
	}
	if _, err := Drive(t.Context(), Options{SiteKey: "nvp_live_x"}, nil); err == nil {
		t.Error("a run with no origin was allowed to start")
	}
}
