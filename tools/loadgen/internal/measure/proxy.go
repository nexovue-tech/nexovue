// Package measure sits between the generator and the receiver and is where
// every number about the transport comes from.
//
// It exists because telemetrygen reports nothing: its exporter neither counts
// what the receiver accepted nor times the answer, so a run driven by it alone
// can only report what was offered. Every export passes through here instead,
// and the clock covers the call to the receiver and nothing else — the
// generator's own loopback hop into this proxy is outside the measured window.
package measure

import (
	"bytes"
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"time"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

// accepted is the only status that means the receiver took the spans. OTLP puts
// partial failures in the response body, and the receiver never sends one: it
// answers 200 with an empty ExportTraceServiceResponse or it fails the request.
const accepted = http.StatusOK

// Connection settings for the measured hop. Idle connections outlive any run so
// that a request measures the receiver rather than a fresh handshake.
const (
	dialTimeout     = 5 * time.Second
	keepAlive       = 30 * time.Second
	idleConnTimeout = 90 * time.Second
	readHeaderLimit = 5 * time.Second
)

// hopByHopHeaders belong to one connection and must not be copied onto the next.
var hopByHopHeaders = []string{
	"Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
	"Te", "Trailer", "Transfer-Encoding", "Upgrade",
}

// TraceSink is told about a trace whose spans the receiver has just accepted,
// so something else can follow it into storage. Keeping it an interface is what
// lets this package stay ignorant of ClickHouse.
type TraceSink interface {
	Follow(traceID string, acceptedAt time.Time)
}

// Options are the collaborators the proxy needs. All are required.
type Options struct {
	// Target is the receiver's base URL; the request's own path is appended.
	Target   string
	Recorder *Recorder
	Sink     TraceSink
	Logger   *slog.Logger

	RequestTimeout time.Duration
	// SinkInterval spaces out the traces handed to the sink.
	SinkInterval time.Duration
	// Connections sizes the idle pool, so a run reuses connections instead of
	// measuring a fresh TCP handshake on most requests.
	Connections int
}

// Proxy forwards OTLP exports to the receiver and records what happened.
type Proxy struct {
	target   string
	client   *http.Client
	recorder *Recorder
	sink     TraceSink
	logger   *slog.Logger

	sinkInterval time.Duration
	sinkClock    *sampleClock
}

// NewProxy returns a proxy forwarding to opts.Target.
//
// The transport is built explicitly rather than cloned from the default: how
// connections are pooled decides whether a run measures the receiver or a TCP
// handshake, so it is part of the measurement setup and stated here.
func NewProxy(opts Options) *Proxy {
	transport := &http.Transport{
		DialContext:         (&net.Dialer{Timeout: dialTimeout, KeepAlive: keepAlive}).DialContext,
		MaxIdleConns:        opts.Connections,
		MaxIdleConnsPerHost: opts.Connections,
		MaxConnsPerHost:     opts.Connections,
		IdleConnTimeout:     idleConnTimeout,
	}

	return &Proxy{
		target:       opts.Target,
		client:       &http.Client{Transport: transport, Timeout: opts.RequestTimeout},
		recorder:     opts.Recorder,
		sink:         opts.Sink,
		logger:       opts.Logger,
		sinkInterval: opts.SinkInterval,
		sinkClock:    newSampleClock(),
	}
}

// Serve runs the proxy until ctx is cancelled, reporting the address it bound.
// The listener is opened before returning so the caller can point a generator
// at a port that is already accepting.
func (p *Proxy) Serve(ctx context.Context, addr string) (string, func() error, error) {
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		return "", nil, fmt.Errorf("listening on %s: %w", addr, err)
	}

	server := &http.Server{Handler: p, ReadHeaderTimeout: readHeaderLimit}
	go func() {
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			p.logger.Error("measuring proxy stopped", slog.Any("error", err))
		}
	}()

	shutdown := func() error {
		drain, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		return server.Shutdown(drain)
	}
	return listener.Addr().String(), shutdown, nil
}

func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		// A body that never finished arriving was never forwarded, and a partial
		// OTLP payload cannot be decoded, so the span count is unknowable: zero.
		p.recorder.ObserveNotSent(0)
		http.Error(w, "cannot read export body", http.StatusBadRequest)
		return
	}

	// Decoding before the clock starts keeps our own parsing out of the
	// receiver's measured latency.
	export := inspect(body)

	upstream, err := p.upstreamRequest(r, body)
	if err != nil {
		p.recorder.ObserveNotSent(export.spans)
		http.Error(w, "cannot build upstream request", http.StatusInternalServerError)
		return
	}

	start := time.Now()
	// The target is this tool's own configured receiver, which is the entire
	// point of a forwarding proxy; there is no caller-controlled destination.
	response, err := p.client.Do(upstream) //nolint:gosec // G704: target comes from LOADGEN_ENDPOINT
	latency := time.Since(start)
	if err != nil {
		p.observeUnanswered(export.spans, err)
		http.Error(w, "receiver unreachable", http.StatusBadGateway)
		return
	}
	defer func() { _ = response.Body.Close() }()

	acceptedAt := start.Add(latency)
	p.recorder.ObserveAnswered(Observation{
		Spans:      export.spans,
		Status:     response.StatusCode,
		Latency:    latency,
		AcceptedAt: acceptedAt,
	})
	if response.StatusCode == accepted {
		p.follow(export.traceID, acceptedAt)
	}

	copyHeaders(w.Header(), response.Header)
	w.WriteHeader(response.StatusCode)
	if _, err := io.Copy(w, response.Body); err != nil {
		p.logger.Warn("cannot return export response", slog.Any("error", err))
	}
}

// observeUnanswered records an export the receiver never answered, split by
// whether it got there at all.
//
// The split is what keeps the accounting honest. An export the receiver
// received may already have been published to Kafka before the answer was lost,
// and the SDK then retries the same batch — so folding these into "failed" made
// the pipeline look like it invented spans. They are offered with an unknown
// fate instead, which is what they are.
func (p *Proxy) observeUnanswered(spans int, err error) {
	if neverReachedReceiver(err) {
		p.recorder.ObserveNotSent(spans)
		p.logger.Warn("export not sent", slog.Any("error", err))
		return
	}
	p.recorder.ObserveUnknown(spans)
	p.logger.Warn("export answer not readable", slog.Any("error", err))
}

// neverReachedReceiver reports whether the transport gave up before any
// connection to the receiver existed.
//
// Dialling is the one stage whose failure proves nothing was sent: no socket,
// no bytes. Past that the request may have been written and acted on however
// the failure reads, and Go's client cannot tell a timeout waiting for headers
// from one where the receiver never saw the body. So the boundary is drawn at
// the only place it can be drawn soundly, and every later failure is unknown
// rather than assumed harmless.
func neverReachedReceiver(err error) bool {
	var opErr *net.OpError
	return errors.As(err, &opErr) && opErr.Op == "dial"
}

// follow hands one trace per interval to the sink, so the traces watched into
// storage are spread across the whole run instead of bunched at its start.
func (p *Proxy) follow(traceID string, acceptedAt time.Time) {
	if p.sink == nil || traceID == "" {
		return
	}
	if p.sinkClock.due(acceptedAt, p.sinkInterval) {
		p.sink.Follow(traceID, acceptedAt)
	}
}

func (p *Proxy) upstreamRequest(r *http.Request, body []byte) (*http.Request, error) {
	upstream, err := http.NewRequestWithContext( //nolint:gosec // G704: target comes from LOADGEN_ENDPOINT
		r.Context(), r.Method, p.target+r.URL.Path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	copyHeaders(upstream.Header, r.Header)
	upstream.ContentLength = int64(len(body))
	return upstream, nil
}

func copyHeaders(destination, source http.Header) {
	for name, values := range source {
		if isHopByHop(name) {
			continue
		}
		for _, value := range values {
			destination.Add(name, value)
		}
	}
}

func isHopByHop(name string) bool {
	for _, hop := range hopByHopHeaders {
		if http.CanonicalHeaderKey(name) == hop {
			return true
		}
	}
	return false
}

// export is what one request turned out to contain.
type export struct {
	spans int
	// traceID is the first trace in the request, lower-case hex, matching how
	// the receiver and ClickHouse both write it.
	traceID string
}

// inspect counts the spans in an OTLP request. A body it cannot decode counts
// as zero rather than failing the run: the receiver's own answer to that body
// is the thing being measured, and it is still recorded.
func inspect(body []byte) export {
	var request collectorpb.ExportTraceServiceRequest
	if err := proto.Unmarshal(body, &request); err != nil {
		return export{}
	}

	result := export{}
	for _, resourceSpans := range request.GetResourceSpans() {
		for _, scopeSpans := range resourceSpans.GetScopeSpans() {
			for _, span := range scopeSpans.GetSpans() {
				result.spans++
				if result.traceID == "" {
					result.traceID = hex.EncodeToString(span.GetTraceId())
				}
			}
		}
	}
	return result
}
