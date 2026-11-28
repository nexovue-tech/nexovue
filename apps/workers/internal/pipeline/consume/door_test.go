// An external test package: serviceedges imports consume, through storefailure.
package consume_test

import (
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/serviceedges"
)

// One vocabulary for the door across the raw tables and the edges rollup, so a
// reader's `door != 'browser'` means the same on each.
func TestStoredDoorsAreTheEdgesRollupsWords(t *testing.T) {
	if consume.StoredDoorBrowser != serviceedges.DoorBrowser ||
		consume.StoredDoorServer != serviceedges.DoorServer {
		t.Errorf("stored doors %q/%q differ from the edges rollup's %q/%q",
			consume.StoredDoorBrowser, consume.StoredDoorServer,
			serviceedges.DoorBrowser, serviceedges.DoorServer)
	}
}
