package logwriting

import (
	"strings"

	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
)

// severityUnspecified is what a record with no severity at all stores. It sorts
// ahead of every real level, which is where an unknown belongs: "warn and
// above" excludes it by the same comparison that excludes DEBUG.
const severityUnspecified int32 = 0

// severityLevelSpan is how many numbers OTLP gives each named level — INFO is
// 9..12, WARN 13..16, and so on. A trailing digit on the text ("INFO2") selects
// within that span.
const severityLevelSpan = 4

// severityByText maps the spellings SDKs actually emit onto OTLP's normalised
// scale. The variety is the reason the numeric column exists: WARN, WARNING, W
// and warn are one level, and no text comparison expresses "warn and above".
//
// Ranked names outside OTLP's vocabulary are placed by meaning rather than
// dropped: syslog's notice sits between INFO and WARN, and its alert and
// emergency above FATAL.
var severityByText = map[string]int32{
	"trace": 1, "trc": 1, "t": 1, "finest": 1, "finer": 2, "fine": 3, "verbose": 4,
	"debug": 5, "dbg": 5, "d": 5,
	"info": 9, "inf": 9, "i": 9, "information": 9, "informational": 9, "notice": 10,
	"warn": 13, "warning": 13, "wrn": 13, "w": 13,
	"error": 17, "err": 17, "e": 17, "severe": 17,
	"fatal": 21, "critical": 21, "crit": 21, "panic": 21, "f": 21,
	"alert": 22, "emergency": 23, "emerg": 23,
}

// severityOf resolves a record's numeric severity and the text to keep beside
// it.
//
// The record's own SeverityNumber wins when it is set: it is OTLP's normalised
// value, already on the scale the sort key orders by. Only when it is absent is
// the free text mapped, which is the case that matters — most SDK bridges fill
// the text and leave the number unset.
func severityOf(record *logspb.LogRecord) (text string, number int32) {
	text = record.GetSeverityText()
	if declared := int32(record.GetSeverityNumber()); declared > 0 {
		return text, declared
	}
	return text, severityNumberFromText(text)
}

// severityNumberFromText maps a level name to OTLP's scale, honouring a
// trailing 1-4 that names a step inside the level ("WARN3").
func severityNumberFromText(text string) int32 {
	name := strings.ToLower(strings.TrimSpace(text))
	if name == "" {
		return severityUnspecified
	}
	if number, ok := severityByText[name]; ok {
		return number
	}

	base, step, ok := splitLevelStep(name)
	if !ok {
		return severityUnspecified
	}
	number, ok := severityByText[base]
	if !ok {
		return severityUnspecified
	}
	// Only a level that starts its own span of four can be stepped within it;
	// stepping from NOTICE (10) would run past WARN.
	if (number-1)%severityLevelSpan != 0 {
		return severityUnspecified
	}
	return number + step - 1
}

// splitLevelStep separates "warn3" into its level name and the step 3. The step
// is 1-4 because that is the width of an OTLP level.
func splitLevelStep(name string) (base string, step int32, ok bool) {
	if len(name) < 2 {
		return "", 0, false
	}
	digit := name[len(name)-1]
	if digit < '1' || digit > '0'+severityLevelSpan {
		return "", 0, false
	}
	return strings.TrimSpace(name[:len(name)-1]), int32(digit - '0'), true
}
