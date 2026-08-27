package config

import (
	"fmt"
	"net"
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

func positiveCount(variable string, value int) error {
	if value <= 0 {
		return invalid(variable, "got %d, want a positive count", value)
	}
	return nil
}

func notBlank(variable, value string) error {
	if strings.TrimSpace(value) == "" {
		return invalid(variable, "must not be blank")
	}
	return nil
}
