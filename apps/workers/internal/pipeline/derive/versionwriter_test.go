package derive

import "testing"

func versionRowsFor(versions ...string) []VersionStat {
	rows := make([]VersionStat, 0, len(versions))
	for _, version := range versions {
		row := VersionStat{ServiceStat: serviceRows()[0], Version: version}
		rows = append(rows, row)
	}
	return rows
}

// The same rows must carry the same token, or a retry after a lost
// acknowledgement is summed in twice.
func TestTheSameVersionRowsCarryTheSameToken(t *testing.T) {
	first, again := versionRowsFor("v1", "v2"), versionRowsFor("v1", "v2")
	if versionDeduplicationToken(first) != versionDeduplicationToken(again) {
		t.Error("the same rows produced two tokens; a retry would be summed in twice")
	}
}

// Rows that differ only by version must not share a token: a minute reopened by
// a late span on another version would otherwise be dropped as a duplicate.
func TestVersionRowsThatDifferOnlyByVersionCarryDifferentTokens(t *testing.T) {
	if versionDeduplicationToken(versionRowsFor("v1")) == versionDeduplicationToken(versionRowsFor("v2")) {
		t.Error("two versions' rows shared a token; the second would be dropped as a repeat")
	}
	// And the version is its own field: moving text between the service and the
	// version must change the token.
	moved := versionRowsFor("2")
	moved[0].ServiceName += "v"
	if versionDeduplicationToken(moved) == versionDeduplicationToken(versionRowsFor("v2")) {
		t.Error("text moved from the version to the service name kept the token")
	}
}
