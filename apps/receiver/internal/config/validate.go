package config

import (
	"fmt"
	"net"
	"net/url"
	"slices"
	"strings"
	"time"
)

// InvalidValueError names the environment variable a bad value came from, so a
// boot failure points at what to edit rather than at a Go struct field.
type InvalidValueError struct {
	Variable string
	Reason   string
}

func (e *InvalidValueError) Error() string {
	return fmt.Sprintf("%s: %s", e.Variable, e.Reason)
}

func invalid(variable, format string, args ...any) error {
	return &InvalidValueError{Variable: variable, Reason: fmt.Sprintf(format, args...)}
}

func oneOf(variable, value string, allowed ...string) error {
	if slices.Contains(allowed, value) {
		return nil
	}
	return invalid(variable, "got %q, want one of %s", value, strings.Join(allowed, ", "))
}

// listeningAddress accepts host:port and :port, the two forms net.Listen takes.
func listeningAddress(variable, value string) error {
	if _, _, err := net.SplitHostPort(value); err != nil {
		return invalid(variable, "got %q, want a [host]:port listening address", value)
	}
	return nil
}

func noBlankEntries(variable string, values []string) error {
	if len(values) == 0 {
		return invalid(variable, "must list at least one entry")
	}
	for _, value := range values {
		if strings.TrimSpace(value) == "" {
			return invalid(variable, "contains a blank entry")
		}
	}
	return nil
}

func positiveDuration(variable string, value time.Duration) error {
	if value <= 0 {
		return invalid(variable, "got %s, want a positive duration", value)
	}
	return nil
}

// connectionURL accepts a DSN whose scheme is one we know how to dial. Catching
// a mistyped scheme at boot beats catching it on the first query.
func connectionURL(variable, value string, schemes ...string) error {
	parsed, err := url.Parse(value)
	if err != nil {
		return invalid(variable, "is not a URL: %v", err)
	}
	if !slices.Contains(schemes, parsed.Scheme) {
		return invalid(variable, "got scheme %q, want one of %s",
			parsed.Scheme, strings.Join(schemes, ", "))
	}
	if parsed.Host == "" {
		return invalid(variable, "has no host")
	}
	return nil
}

func notBlank(variable, value string) error {
	if strings.TrimSpace(value) == "" {
		return invalid(variable, "must not be blank")
	}
	return nil
}

func positiveCount(variable string, value int) error {
	if value <= 0 {
		return invalid(variable, "got %d, want a positive count", value)
	}
	return nil
}

func positiveSize(variable string, value int64) error {
	if value <= 0 {
		return invalid(variable, "got %d, want a positive number of bytes", value)
	}
	return nil
}

// atLeastSize keeps a pair of byte limits in a usable order: a decompressed cap
// below the wire cap would reject bodies the wire cap just allowed.
func atLeastSize(variable string, value int64, floorVariable string, floor int64) error {
	if value < floor {
		return invalid(variable, "got %d, want at least %s (%d)", value, floorVariable, floor)
	}
	return nil
}

// atMostSize caps a byte limit that something downstream cannot exceed.
func atMostSize(variable string, value int64, ceilingReason string, ceiling int64) error {
	if value > ceiling {
		return invalid(variable, "got %d, want at most %s (%d)", value, ceilingReason, ceiling)
	}
	return nil
}

func atMostDuration(variable string, value, ceiling time.Duration) error {
	if value > ceiling {
		return invalid(variable, "got %s, want at most %s", value, ceiling)
	}
	return nil
}

// atMostCount keeps a pair of pool bounds in a usable order.
func atMostCount(variable string, value int, ceilingVariable string, ceiling int) error {
	if value > ceiling {
		return invalid(variable, "got %d, want at most %s (%d)", value, ceilingVariable, ceiling)
	}
	return nil
}
