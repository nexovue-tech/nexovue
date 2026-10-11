package config

import "testing"

// The -health self-check runs before the settings are loaded, so it reads the
// address itself; it must probe the port the listener was told to bind.
func TestSelfCheckAddrFollowsTheConfiguredListener(t *testing.T) {
	t.Setenv("WORKERS_HEALTH_ADDR", ":9090")
	if got := SelfCheckAddr(); got != ":9090" {
		t.Fatalf("SelfCheckAddr() = %q, want the configured :9090", got)
	}
}

func TestSelfCheckAddrDefaultsToTheListenersDefault(t *testing.T) {
	t.Setenv("WORKERS_HEALTH_ADDR", "")
	if got := SelfCheckAddr(); got != DefaultHealthAddr {
		t.Fatalf("SelfCheckAddr() = %q, want %q", got, DefaultHealthAddr)
	}
}
