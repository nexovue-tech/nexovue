package traceassembly

import (
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

// statusCodeError is the flattened form of OTLP's STATUS_CODE_ERROR, and the
// only thing that makes a span — and so its trace — a failure.
const statusCodeError = "ERROR"

// errored reports whether this span failed.
func (r spanRow) errored() bool { return r.StatusCode == statusCodeError }

// endpoint is what the counters group by inside a service. http.route is the
// low-cardinality name of an HTTP endpoint; a span without one is named by the
// SDK, and that name is the closest thing it has to an endpoint.
func (r spanRow) endpoint() string {
	if r.HTTPRoute != "" {
		return r.HTTPRoute
	}
	return r.SpanName
}

// traceKey is the trace this span belongs to. The id is already hex, fixed
// width and immutable, which makes it a map key with no further work.
func (r spanRow) traceKey() string { return string(r.TraceID) }

// countable is this span as the derive stage sees it.
func (r spanRow) countable() derive.Span {
	return derive.Span{
		TenantID:    r.TenantID,
		ProjectID:   r.ProjectID,
		ServiceName: r.ServiceName,
		Endpoint:    r.endpoint(),
		HTTPMethod:  r.HTTPMethod,
		Timestamp:   r.Timestamp,
		DurationMS:  r.DurationMS,
		Errored:     r.errored(),
	}
}

// sampleFacts is this span as the sample stage sees it.
func (r spanRow) sampleFacts() sample.Facts {
	return sample.Facts{
		TraceID:   r.traceKey(),
		ProjectID: r.ProjectID,
		Start:     r.Timestamp,
		Duration:  millisecondsDuration(r.DurationMS),
		Errored:   r.errored(),
	}
}

// stages are the per-record half of the pipeline: decode → mask → derive →
// sample. They are grouped here, away from the broker and the store, because
// the order between them is an invariant and an invariant needs a test that
// does not require a running cluster.
type stages struct {
	masker   *mask.Masker
	counters *derive.Counters
	buffer   *sample.Buffer[spanRow]
}

func newStages(cfg config.Config, opts Options) (*stages, error) {
	buffer, err := sample.New[spanRow](samplingOptions(cfg.Sampling), samplingSource(cfg, opts))
	if err != nil {
		return nil, err
	}
	return &stages{
		masker:   mask.New(maskingSource(opts)),
		counters: derive.NewCounters(cfg.Metrics.LateGrace),
		buffer:   buffer,
	}, nil
}

// absorbed is what one message produced: the rows to write now, and the newest
// counter bucket it fed. The bucket is what tells the caller when this
// message's counters have reached storage and its offset may move.
type absorbed struct {
	rows         []spanRow
	latestMinute time.Time
	spans        int
}

// absorbMessage runs one Kafka message through every stage.
//
// counted is false for a redelivery: its spans still travel — re-inserting an
// identical row changes nothing — but they must not be added to a counter
// twice.
func (s *stages) absorbMessage(now time.Time, message []byte, counted bool) (absorbed, error) {
	payload, err := decodeEnvelope(message)
	if err != nil {
		return absorbed{}, err
	}

	// Mask first: from here on no stage can see the original values.
	s.masker.MaskResourceSpans(payload.envelope.GetProjectId(), payload.request.GetResourceSpans())

	var result absorbed
	for _, row := range payload.rows() {
		// Derive before sample: the counters see every span, whatever the
		// sampler decides about the trace it belongs to (ADR 0010).
		if counted {
			result.latestMinute = later(result.latestMinute, s.counters.Observe(row.countable()))
		}
		result.rows = append(result.rows, s.buffer.Add(now, row.sampleFacts(), row)...)
		result.spans++
	}
	return result, nil
}
