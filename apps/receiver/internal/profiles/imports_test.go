package profiles

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The two OTLP profiles modules: the messages, and the collector service.
var otlpProfilesModules = []string{
	"go.opentelemetry.io/proto/otlp/profiles/v1development",
	"go.opentelemetry.io/proto/otlp/collector/profiles/v1development",
}

// Only the receiver may depend on OTLP profiles (ADR-015). It is a Development
// format that breaks between releases; the receiver converts it, and everything
// behind the receiver reads nexovue's own ProfileSamples. A second module importing
// it would be a second place a format change has to be followed.
func TestNoOtherModuleDependsOnOtlpProfiles(t *testing.T) {
	root, err := filepath.Abs(filepath.Join("..", "..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	receiver := filepath.Join(root, "apps", "receiver", "go.mod")

	var modules []string
	for _, pattern := range []string{"apps/*/go.mod", "packages/*/go.mod", "tools/*/go.mod"} {
		found, err := filepath.Glob(filepath.Join(root, pattern))
		if err != nil {
			t.Fatal(err)
		}
		modules = append(modules, found...)
	}
	if len(modules) < 3 {
		t.Fatalf("found %d go.mod files under %s; the walk is looking in the wrong place", len(modules), root)
	}

	for _, module := range modules {
		if module == receiver {
			continue
		}
		contents, err := os.ReadFile(module)
		if err != nil {
			t.Fatal(err)
		}
		for _, profiles := range otlpProfilesModules {
			if strings.Contains(string(contents), profiles) {
				rel, _ := filepath.Rel(root, module)
				t.Errorf("%s depends on %s; only the receiver may (ADR-015)", rel, profiles)
			}
		}
	}
}
