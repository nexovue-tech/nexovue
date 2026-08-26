// Package buildinfo reports which build of which service is running, so an
// operator reading a health response or a log line knows what answered.
package buildinfo

import (
	"runtime"
	"runtime/debug"
)

// Unknown is reported for a build fact the toolchain could not supply.
const Unknown = "unknown"

// Info identifies a running binary.
type Info struct {
	Service   string `json:"service"`
	Revision  string `json:"revision"`
	BuildTime string `json:"build_time"`
	GoVersion string `json:"go_version"`
}

// Overridable at link time for container builds, where the .git directory is
// absent and the toolchain cannot stamp the VCS facts itself.
var (
	revision  string
	buildTime string
)

const (
	revisionSetting = "vcs.revision"
	timeSetting     = "vcs.time"
	modifiedSetting = "vcs.modified"
	dirtySuffix     = "+dirty"
)

// For returns the build facts for the named service.
func For(service string) Info {
	rev, built := revision, buildTime
	if rev == "" || built == "" {
		stampedRev, stampedTime := fromBuildStamps()
		if rev == "" {
			rev = stampedRev
		}
		if built == "" {
			built = stampedTime
		}
	}
	return Info{
		Service:   service,
		Revision:  orUnknown(rev),
		BuildTime: orUnknown(built),
		GoVersion: runtime.Version(),
	}
}

// fromBuildStamps reads the VCS facts the Go toolchain embeds when it builds
// from a git checkout.
func fromBuildStamps() (rev, built string) {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "", ""
	}
	dirty := false
	for _, setting := range info.Settings {
		switch setting.Key {
		case revisionSetting:
			rev = setting.Value
		case timeSetting:
			built = setting.Value
		case modifiedSetting:
			dirty = setting.Value == "true"
		}
	}
	if dirty && rev != "" {
		rev += dirtySuffix
	}
	return rev, built
}

func orUnknown(value string) string {
	if value == "" {
		return Unknown
	}
	return value
}
