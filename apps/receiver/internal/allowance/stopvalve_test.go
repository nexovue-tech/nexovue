package allowance

import (
	"testing"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// insideTheAllowance is a free organization that has spent almost nothing. It
// is the whole point of the stop valve: the allowance has no reason to refuse
// this tenant, and the operator needs one anyway.
func insideTheAllowance() Usage {
	return Usage{
		IncludedBytes: freeIncluded,
		Enforced:      true,
		IngestedBytes: 1,
		Found:         true,
	}
}

// The valve is operational and not billing, so it has to work on a tenant the
// allowance would admit. A valve that only fired on a spent month would be a
// second spelling of the cap, and would stop nobody the cap does not.
func TestASuspendedOrganizationIsRefusedWhileInsideItsAllowance(t *testing.T) {
	suspended := insideTheAllowance()
	suspended.IngestSuspended = true

	verdict := checkOnce(t, &fakeUsage{usage: suspended})

	if !verdict.Refused() {
		t.Fatalf("verdict = %+v, want refused: the stop valve is closed on this organization",
			verdict)
	}
	if verdict.Cause != IngestSuspended {
		t.Errorf("cause = %v, want IngestSuspended: a suspension is not a spent allowance, "+
			"and telling the customer their month ran out would be a false answer", verdict.Cause)
	}
}

// A tenant stopped for what it is doing to the platform is stopped at every
// entrance. Refusing at one door would leave the runaway customer a door to
// keep running through.
func TestASuspendedOrganizationIsRefusedAtEveryDoor(t *testing.T) {
	suspended := insideTheAllowance()
	suspended.IngestSuspended = true

	for _, door := range []nexovuev1.Door{serverDoor, browserDoor} {
		verdict := checkAt(t, &fakeUsage{usage: suspended}, door)
		if verdict.Cause != IngestSuspended {
			t.Errorf("%v: cause = %v, want IngestSuspended", door, verdict.Cause)
		}
	}
}

// The unset column is the ordinary state, and the ordinary state has to admit.
// A default that read the other way would stop every tenant on the day the
// column shipped.
func TestAnOrganizationWithTheValveOpenIsAdmitted(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: insideTheAllowance()})

	if verdict.Refused() {
		t.Errorf("verdict = %+v, want admitted: an unset valve must not refuse anybody",
			verdict)
	}
}

// The suspension does not swallow the refusals that outrank it. A deleted
// organization is gone and never comes back, which is a different message and a
// different tally from one somebody paused.
func TestADeletedOrganizationIsStillReportedAsGoneWhenAlsoSuspended(t *testing.T) {
	both := Usage{Found: true, Deleted: true, IngestSuspended: true}

	verdict := checkOnce(t, &fakeUsage{usage: both})

	if verdict.Cause != OrganizationGone {
		t.Errorf("cause = %v, want OrganizationGone: deletion is final and a suspension is not",
			verdict.Cause)
	}
}

// And a spent allowance is still a spent allowance when nobody has touched the
// valve — the new branch must not have captured the old verdict.
func TestASpentAllowanceIsStillReportedAsSpentWithTheValveOpen(t *testing.T) {
	spent := Usage{
		IncludedBytes: freeIncluded,
		Enforced:      true,
		IngestedBytes: freeIncluded + 1,
		Found:         true,
	}

	verdict := checkOnce(t, &fakeUsage{usage: spent})

	if verdict.Cause != AllowanceSpent {
		t.Errorf("cause = %v, want AllowanceSpent", verdict.Cause)
	}
	if verdict.ResetsOn.IsZero() {
		t.Error("a spent allowance must still name the day it comes back")
	}
}

// The valve rides the allowance read rather than adding one of its own, which
// is what makes it free to leave on. One read per organization, cached, however
// many exports arrive.
func TestTheValveCostsNoExtraLookup(t *testing.T) {
	suspended := insideTheAllowance()
	suspended.IngestSuspended = true
	reader := &fakeUsage{usage: suspended}
	checker := newChecker(reader)

	for range 5 {
		if _, err := checker.Check(t.Context(), orgID, serverDoor); err != nil {
			t.Fatalf("Check() = %v, want no error", err)
		}
	}

	if reader.reads != 1 {
		t.Errorf("read the control plane %d times, want 1: the valve must not add a query",
			reader.reads)
	}
}
