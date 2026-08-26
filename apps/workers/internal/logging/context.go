package logging

import "context"

// Field names are fixed here and mirrored by the Python services. One name per
// concept is what makes a single query work across every service's logs.
const (
	FieldTimestamp     = "ts"
	FieldLevel         = "level"
	FieldMessage       = "msg"
	FieldService       = "service"
	FieldEnv           = "env"
	FieldTraceID       = "trace_id"
	FieldSpanID        = "span_id"
	FieldCorrelationID = "correlation_id"
	FieldOrgID         = "org_id"
	FieldProjectID     = "project_id"
)

// Fields are the request-scoped values every log line carries when they are
// known. Empty values are omitted rather than logged blank.
type Fields struct {
	TraceID       string
	SpanID        string
	CorrelationID string
	OrgID         string
	ProjectID     string
}

type fieldsKey struct{}

// WithFields returns a context carrying next merged over the fields already
// present, so a later stage can add org and project without losing the trace.
func WithFields(ctx context.Context, next Fields) context.Context {
	return context.WithValue(ctx, fieldsKey{}, FieldsFrom(ctx).merge(next))
}

// FieldsFrom returns the fields carried by ctx, or the zero value if none are.
func FieldsFrom(ctx context.Context) Fields {
	fields, ok := ctx.Value(fieldsKey{}).(Fields)
	if !ok {
		return Fields{}
	}
	return fields
}

func (f Fields) merge(next Fields) Fields {
	overwrite(&f.TraceID, next.TraceID)
	overwrite(&f.SpanID, next.SpanID)
	overwrite(&f.CorrelationID, next.CorrelationID)
	overwrite(&f.OrgID, next.OrgID)
	overwrite(&f.ProjectID, next.ProjectID)
	return f
}

func overwrite(target *string, value string) {
	if value != "" {
		*target = value
	}
}
