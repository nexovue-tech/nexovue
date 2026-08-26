// Package logging builds the one structured logger every nexovue service uses.
// The field names are fixed and shared with the Python services, so a single
// query works across the whole system.
//
// Levels mean the same thing in every service:
//
//	ERROR  a human must look at this
//	WARN   degraded, but handled — no action required right now
//	INFO   a state change worth a permanent record
//	DEBUG  development detail; off outside development
package logging

import (
	"context"
	"io"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/lmittmann/tint"
)

// Output formats. JSON is what production ships; console is for a human reading
// a terminal during development.
const (
	FormatJSON    = "json"
	FormatConsole = "console"
)

// Level names accepted in configuration.
const (
	LevelDebug = "debug"
	LevelInfo  = "info"
	LevelWarn  = "warn"
	LevelError = "error"
)

// Formats lists every accepted value of LOG_FORMAT.
func Formats() []string { return []string{FormatJSON, FormatConsole} }

// Levels lists every accepted value of LOG_LEVEL.
func Levels() []string { return []string{LevelDebug, LevelInfo, LevelWarn, LevelError} }

// consoleTimeFormat drops the date: a developer reading a terminal already
// knows what day it is.
const consoleTimeFormat = "15:04:05.000"

// Options describe the logger a service wants.
type Options struct {
	Level   string
	Format  string
	Service string
	Env     string
	Output  io.Writer // defaults to stdout
}

// New builds the service logger. An unrecognised level or format falls back to
// the safe choice rather than failing — config validation is what rejects them.
func New(opts Options) *slog.Logger {
	out := opts.Output
	if out == nil {
		out = os.Stdout
	}
	level := ParseLevel(opts.Level)

	var handler slog.Handler
	if opts.Format == FormatConsole {
		handler = tint.NewTextHandler(out, &tint.Options{
			Level:       level,
			TimeFormat:  consoleTimeFormat,
			ReplaceAttr: RedactAttr,
		})
	} else {
		handler = slog.NewJSONHandler(out, &slog.HandlerOptions{
			Level:       level,
			ReplaceAttr: jsonAttr,
		})
	}

	return slog.New(&contextHandler{Handler: handler}).With(
		slog.String(FieldService, opts.Service),
		slog.String(FieldEnv, opts.Env),
	)
}

// ParseLevel maps a configured level name to its slog level, defaulting to info.
func ParseLevel(name string) slog.Level {
	switch strings.ToLower(name) {
	case LevelDebug:
		return slog.LevelDebug
	case LevelWarn:
		return slog.LevelWarn
	case LevelError:
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

// jsonAttr renames slog's built-in keys to the shared contract and then applies
// redaction. Levels are lowercased so they match the Python services exactly.
func jsonAttr(groups []string, attr slog.Attr) slog.Attr {
	if len(groups) == 0 {
		switch attr.Key {
		case slog.TimeKey:
			return slog.String(FieldTimestamp, attr.Value.Time().UTC().Format(time.RFC3339Nano))
		case slog.LevelKey:
			return slog.String(FieldLevel, strings.ToLower(attr.Value.String()))
		case slog.MessageKey:
			return slog.String(FieldMessage, attr.Value.String())
		}
	}
	return RedactAttr(groups, attr)
}

// contextHandler stamps every record with the request-scoped fields carried by
// the context, so callers never have to remember to pass the trace id.
type contextHandler struct {
	slog.Handler
}

func (h *contextHandler) Handle(ctx context.Context, record slog.Record) error {
	record.AddAttrs(FieldsFrom(ctx).attrs()...)
	return h.Handler.Handle(ctx, record)
}

func (h *contextHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return &contextHandler{Handler: h.Handler.WithAttrs(attrs)}
}

func (h *contextHandler) WithGroup(name string) slog.Handler {
	return &contextHandler{Handler: h.Handler.WithGroup(name)}
}

func (f Fields) attrs() []slog.Attr {
	pairs := []struct {
		key   string
		value string
	}{
		{FieldTraceID, f.TraceID},
		{FieldSpanID, f.SpanID},
		{FieldCorrelationID, f.CorrelationID},
		{FieldOrgID, f.OrgID},
		{FieldProjectID, f.ProjectID},
	}
	attrs := make([]slog.Attr, 0, len(pairs))
	for _, pair := range pairs {
		if pair.value != "" {
			attrs = append(attrs, slog.String(pair.key, pair.value))
		}
	}
	return attrs
}
