// Package config reads the demo app's settings from the environment.
//
// The OTel exporter is deliberately NOT configured here: it reads the standard
// OTEL_* variables straight from the environment, which is exactly what a
// customer does. If that path is awkward for us, it is awkward for them.
package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Role decides which of the shop's services this process is. One binary, three
// roles, so a trace crosses real process and network boundaries rather than
// pretending to.
type Role string

// The shop's three services.
const (
	RoleStorefront Role = "storefront"
	RoleCheckout   Role = "checkout"
	RolePayments   Role = "payments"
)

// Roles is every valid DEMO_ROLE value.
var Roles = []Role{RoleStorefront, RoleCheckout, RolePayments}

// RequestTimeout bounds every outbound call, so a wedged upstream becomes a
// failed span rather than a hung request.
const RequestTimeout = 5 * time.Second

// Config is the demo app's complete settings surface.
type Config struct {
	Role Role
	Addr string
	// CheckoutURL and PaymentsURL are this role's upstreams, where it has any.
	CheckoutURL string
	PaymentsURL string
	// TrafficRate is requests per second the storefront drives against itself.
	// Zero turns the generator off and leaves the app purely request-driven.
	TrafficRate float64
}

// Load reads the environment, failing rather than starting half-configured.
func Load() (Config, error) {
	role := Role(env("DEMO_ROLE", string(RoleStorefront)))
	if !validRole(role) {
		return Config{}, fmt.Errorf("DEMO_ROLE: got %q, want one of %v", role, Roles)
	}

	raw := env("DEMO_TRAFFIC_RATE", "2")
	rate, err := strconv.ParseFloat(raw, 64)
	if err != nil || rate < 0 {
		return Config{}, fmt.Errorf("DEMO_TRAFFIC_RATE: got %q, want a non-negative number", raw)
	}

	return Config{
		Role:        role,
		Addr:        env("DEMO_ADDR", ":8090"),
		CheckoutURL: env("DEMO_CHECKOUT_URL", "http://demo-checkout:8090"),
		PaymentsURL: env("DEMO_PAYMENTS_URL", "http://demo-payments:8090"),
		TrafficRate: rate,
	}, nil
}

func validRole(r Role) bool {
	for _, known := range Roles {
		if r == known {
			return true
		}
	}
	return false
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
