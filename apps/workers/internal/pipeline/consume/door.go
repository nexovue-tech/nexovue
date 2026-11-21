package consume

import nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

// The doors a stored row can say it came through, as `spans.door` and `logs.door`
// hold them (F28 D38, migrations 000041 and 000042), in the words the edges rollup
// already writes (`serviceedges.DoorServer`, `DoorBrowser`). Two values, not the
// envelope's door: a reader asks only whether a row came through the door whose
// key is printed in a page, where anyone can name any service and version.
const (
	StoredDoorServer  = "server"
	StoredDoorBrowser = "browser"
)

// StoredDoor is the door a row from this envelope is stored under.
func StoredDoor(door nexovuev1.Door) string {
	if door == nexovuev1.Door_DOOR_RUM {
		return StoredDoorBrowser
	}
	return StoredDoorServer
}
