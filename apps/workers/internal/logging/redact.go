package logging

import (
	"log/slog"
	"regexp"
	"strings"
)

// Placeholder replaces any value withheld from the log.
const Placeholder = "[redacted]"

// secretKeyMarkers are substrings that mark an attribute as carrying a
// credential. Matching on the key rather than the value keeps the rule cheap
// and predictable; a value-sniffing redactor mangles legitimate data.
var secretKeyMarkers = []string{
	"password",
	"passwd",
	"secret",
	"token",
	"authorization",
	"api_key",
	"apikey",
	"private_key",
	"credential",
	"session_id",
}

// credentialsInURL matches the password of a scheme://user:password@host
// connection string, wherever it appears — including inside the free text of a
// driver error, which is how DSNs most often escape into logs.
var credentialsInURL = regexp.MustCompile(`([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+):[^\s/@]*@`)

// IsSecretKey reports whether an attribute under this key must never be logged.
func IsSecretKey(key string) bool {
	lowered := strings.ToLower(key)
	for _, marker := range secretKeyMarkers {
		if strings.Contains(lowered, marker) {
			return true
		}
	}
	return false
}

// Redact returns the placeholder for any non-empty value.
func Redact(value string) string {
	if value == "" {
		return ""
	}
	return Placeholder
}

// RedactCredentials removes connection-string passwords from text while keeping
// the scheme, user and host, which is what an operator actually needs to see.
func RedactCredentials(text string) string {
	return credentialsInURL.ReplaceAllString(text, "$1:"+Placeholder+"@")
}

// RedactAttr is the slog hook that keeps credentials out of every handler.
func RedactAttr(_ []string, attr slog.Attr) slog.Attr {
	switch {
	case IsSecretKey(attr.Key):
		attr.Value = slog.StringValue(Placeholder)
	case attr.Value.Kind() == slog.KindString:
		attr.Value = slog.StringValue(RedactCredentials(attr.Value.String()))
	}
	return attr
}
