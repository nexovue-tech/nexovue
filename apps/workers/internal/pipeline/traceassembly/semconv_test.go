package traceassembly

import "testing"

func TestParseSemconvVersion(t *testing.T) {
	cases := map[string]struct {
		raw       string
		want      semconvVersion
		wantValid bool
	}{
		"major minor patch":  {"1.27.0", semconvVersion{1, 27}, true},
		"major minor only":   {"1.27", semconvVersion{1, 27}, true},
		"v prefixed":         {"v1.23.1", semconvVersion{1, 23}, true},
		"surrounding space":  {" 1.23.0 ", semconvVersion{1, 23}, true},
		"empty":              {"", semconvVersion{}, false},
		"major only":         {"1", semconvVersion{}, false},
		"not a number":       {"latest", semconvVersion{}, false},
		"minor not a number": {"1.x.0", semconvVersion{}, false},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got, ok := parseSemconvVersion(tc.raw)
			if ok != tc.wantValid {
				t.Fatalf("parseSemconvVersion(%q) valid = %v, want %v", tc.raw, ok, tc.wantValid)
			}
			if ok && got != tc.want {
				t.Errorf("parseSemconvVersion(%q) = %+v, want %+v", tc.raw, got, tc.want)
			}
		})
	}
}

// The version chooses which spelling wins, never whether a spelling is looked
// at: both generations are always tried, so a mislabelled envelope still fills
// its promoted columns.
func TestHTTPNamesForAlwaysOffersBothGenerations(t *testing.T) {
	cases := map[string]struct {
		semconv string
		wantTop httpAttributeNames
	}{
		"stabilization release": {"1.23.0", stableHTTPNames},
		"after stabilization":   {"1.27.0", stableHTTPNames},
		"before stabilization":  {"1.22.0", legacyHTTPNames},
		"much older major":      {"0.9.0", legacyHTTPNames},
		"absent":                {"", stableHTTPNames},
		"unparseable":           {"unknown", stableHTTPNames},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got := httpNamesFor(tc.semconv)
			if got[0] != tc.wantTop {
				t.Errorf("httpNamesFor(%q) preferred %+v, want %+v", tc.semconv, got[0], tc.wantTop)
			}
			if got[1] == got[0] {
				t.Error("both entries are the same generation; the fallback is lost")
			}
		})
	}
}
