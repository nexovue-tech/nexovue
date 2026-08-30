package serviceedges

import (
	"reflect"
	"testing"
	"time"
)

// baseTime is a fixed instant inside one minute, so a test never straddles a
// bucket boundary by accident.
var baseTime = time.Date(2026, 8, 28, 10, 0, 30, 0, time.UTC)

// settledTime is late enough that every bucket touched at baseTime is closed and
// every parked span has expired.
var settledTime = baseTime.Add(2 * time.Minute)

func testOptions() Options {
	return Options{PairWindow: 10 * time.Second, LateGrace: 5 * time.Second, MaxPending: 1000}
}

func newTestAccumulator(t *testing.T) *Accumulator {
	t.Helper()
	accumulator, err := New(testOptions())
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return accumulator
}

func testSpan(kind, service string, spanID, parentSpanID byte) Span {
	span := Span{
		TenantID:      "tenant-a",
		ProjectID:     "project-a",
		RetentionDays: 395,
		TraceID:       []byte{0xaa},
		SpanID:        []byte{spanID},
		ServiceName:   service,
		Kind:          kind,
		Timestamp:     baseTime,
		ReceivedAt:    baseTime,
		DurationMS:    12,
	}
	if parentSpanID != 0 {
		span.ParentSpanID = []byte{parentSpanID}
	}
	return span
}

func drainEdges(t *testing.T, accumulator *Accumulator, spans ...Span) []Edge {
	t.Helper()
	for _, span := range spans {
		accumulator.Observe(baseTime, span)
	}
	return accumulator.Drain(settledTime)
}

func TestClientPairsWithItsServerChild(t *testing.T) {
	accumulator := newTestAccumulator(t)
	client := testSpan(KindClient, "checkout", 0x01, 0)
	client.DurationMS = 40
	server := testSpan(KindServer, "payments", 0x02, 0x01)
	server.DurationMS = 30

	edges := drainEdges(t, accumulator, client, server)

	if len(edges) != 1 {
		t.Fatalf("expected one edge, got %d: %+v", len(edges), edges)
	}
	edge := edges[0]
	if edge.ClientService != "checkout" || edge.ServerService != "payments" {
		t.Fatalf("wrong services: %q -> %q", edge.ClientService, edge.ServerService)
	}
	if edge.Kind != EdgeCall {
		t.Fatalf("expected edge kind %q, got %q", EdgeCall, edge.Kind)
	}
	if edge.CallCount != 1 || edge.ErrorCount != 0 {
		t.Fatalf("expected one clean call, got calls=%d errors=%d", edge.CallCount, edge.ErrorCount)
	}
	// The caller's duration, not the callee's: the edge is what the caller
	// experienced, network included.
	if len(edge.LatencyMS) != 1 || edge.LatencyMS[0] != 40 {
		t.Fatalf("expected the client's 40ms, got %v", edge.LatencyMS)
	}
	if edge.Minute != baseTime.Truncate(time.Minute) {
		t.Fatalf("wrong minute: %s", edge.Minute)
	}
	if got := accumulator.Stats().Paired; got != 1 {
		t.Fatalf("expected one pairing, got %d", got)
	}
}

func TestPairsWhicheverHalfArrivesFirst(t *testing.T) {
	client := testSpan(KindClient, "checkout", 0x01, 0)
	server := testSpan(KindServer, "payments", 0x02, 0x01)

	clientFirst := drainEdges(t, newTestAccumulator(t), client, server)
	serverFirst := drainEdges(t, newTestAccumulator(t), server, client)

	if len(clientFirst) != 1 || len(serverFirst) != 1 {
		t.Fatalf("expected one edge each way, got %d and %d", len(clientFirst), len(serverFirst))
	}
	if !sameEdge(clientFirst[0], serverFirst[0]) {
		t.Fatalf("arrival order changed the edge: %+v vs %+v", clientFirst[0], serverFirst[0])
	}
}

func TestUnpairedClientReportsAnUninstrumentedCallee(t *testing.T) {
	accumulator := newTestAccumulator(t)
	edges := drainEdges(t, accumulator, testSpan(KindClient, "checkout", 0x01, 0))

	if len(edges) != 1 {
		t.Fatalf("expected the call to be reported, got %d edges", len(edges))
	}
	if edges[0].ServerService != UninstrumentedService {
		t.Fatalf("expected callee %q, got %q", UninstrumentedService, edges[0].ServerService)
	}
	if edges[0].CallCount != 1 {
		t.Fatalf("expected one call, got %d", edges[0].CallCount)
	}
	if got := accumulator.Stats().Uninstrumented; got != 1 {
		t.Fatalf("expected one uninstrumented edge counted, got %d", got)
	}
}

func TestProducerPairsWithItsConsumerChildAsAQueueHop(t *testing.T) {
	accumulator := newTestAccumulator(t)
	producer := testSpan(KindProducer, "checkout", 0x01, 0)
	consumer := testSpan(KindConsumer, "fulfilment", 0x02, 0x01)

	edges := drainEdges(t, accumulator, producer, consumer)

	if len(edges) != 1 {
		t.Fatalf("expected one edge, got %d: %+v", len(edges), edges)
	}
	if edges[0].Kind != EdgeQueue {
		t.Fatalf("expected edge kind %q, got %q", EdgeQueue, edges[0].Kind)
	}
	if edges[0].ClientService != "checkout" || edges[0].ServerService != "fulfilment" {
		t.Fatalf("wrong services: %q -> %q", edges[0].ClientService, edges[0].ServerService)
	}
}

// A consumer that has not run yet is the normal state of a queue, so an
// unpaired producer must not invent an uninstrumented service beside it.
func TestUnpairedProducerReportsNothing(t *testing.T) {
	accumulator := newTestAccumulator(t)
	edges := drainEdges(t, accumulator, testSpan(KindProducer, "checkout", 0x01, 0))

	if len(edges) != 0 {
		t.Fatalf("expected no edge for an unconsumed message, got %+v", edges)
	}
}

// The callee alone says nothing about who called it, and inventing a caller
// would draw an edge from a service that may not exist.
func TestUnpairedServerReportsNothing(t *testing.T) {
	accumulator := newTestAccumulator(t)
	edges := drainEdges(t, accumulator, testSpan(KindServer, "payments", 0x02, 0x09))

	if len(edges) != 0 {
		t.Fatalf("expected no edge for an unpaired server, got %+v", edges)
	}
}

func TestKindsThatAreNotEndsOfACallAreIgnored(t *testing.T) {
	accumulator := newTestAccumulator(t)
	internal := testSpan("INTERNAL", "checkout", 0x03, 0x01)
	rootServer := testSpan(KindServer, "checkout", 0x01, 0) // an entry point, called by nobody we can see
	unusable := testSpan(KindClient, "checkout", 0x00, 0)
	unusable.SpanID = nil

	for _, span := range []Span{internal, rootServer, unusable} {
		if minute, _ := accumulator.Observe(baseTime, span); !minute.IsZero() {
			t.Fatalf("%s span was given a settlement minute %s", span.Kind, minute)
		}
	}
	if edges := accumulator.Drain(settledTime); len(edges) != 0 {
		t.Fatalf("expected no edges, got %+v", edges)
	}
	if got := accumulator.Stats().Ignored; got != 3 {
		t.Fatalf("expected three ignored spans, got %d", got)
	}
}

// A CLIENT is only answered by a SERVER: a mismatched child is not a pair even
// though the parent link points at it.
func TestClientDoesNotPairWithAConsumer(t *testing.T) {
	accumulator := newTestAccumulator(t)
	edges := drainEdges(t, accumulator,
		testSpan(KindClient, "checkout", 0x01, 0),
		testSpan(KindConsumer, "fulfilment", 0x02, 0x01),
	)

	if len(edges) != 1 {
		t.Fatalf("expected only the unpaired client, got %+v", edges)
	}
	if edges[0].ServerService != UninstrumentedService {
		t.Fatalf("expected the client to stay unpaired, got %q", edges[0].ServerService)
	}
}

func TestEitherEndErroringFailsTheEdge(t *testing.T) {
	for name, errored := range map[string]func(client, server *Span){
		"client": func(client, _ *Span) { client.Errored = true },
		"server": func(_, server *Span) { server.Errored = true },
	} {
		t.Run(name, func(t *testing.T) {
			client := testSpan(KindClient, "checkout", 0x01, 0)
			server := testSpan(KindServer, "payments", 0x02, 0x01)
			errored(&client, &server)

			edges := drainEdges(t, newTestAccumulator(t), client, server)
			if len(edges) != 1 || edges[0].ErrorCount != 1 {
				t.Fatalf("expected one failed call, got %+v", edges)
			}
		})
	}
}

func TestServiceWithoutANameFallsIntoOneReservedBucket(t *testing.T) {
	client := testSpan(KindClient, "", 0x01, 0)
	server := testSpan(KindServer, "", 0x02, 0x01)

	edges := drainEdges(t, newTestAccumulator(t), client, server)
	if len(edges) != 1 {
		t.Fatalf("expected one edge, got %+v", edges)
	}
	if edges[0].ClientService != UnknownService || edges[0].ServerService != UnknownService {
		t.Fatalf("expected both ends named %q, got %q -> %q",
			UnknownService, edges[0].ClientService, edges[0].ServerService)
	}
}

// A full buffer degrades the graph; it never loses the call that made the edge.
func TestAFullBufferSettlesTheOldestSpanRatherThanDroppingIt(t *testing.T) {
	options := testOptions()
	options.MaxPending = 1
	accumulator, err := New(options)
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	first := testSpan(KindClient, "checkout", 0x01, 0)
	second := testSpan(KindClient, "cart", 0x02, 0)
	accumulator.Observe(baseTime, first)
	accumulator.Observe(baseTime, second)

	edges := accumulator.Drain(settledTime)
	if len(edges) != 2 {
		t.Fatalf("expected both calls reported, got %+v", edges)
	}
	if got := accumulator.Stats().Evicted; got != 1 {
		t.Fatalf("expected one eviction, got %d", got)
	}
}

func TestPairingBufferEmptiesAsSpansSettle(t *testing.T) {
	accumulator := newTestAccumulator(t)
	accumulator.Observe(baseTime, testSpan(KindClient, "checkout", 0x01, 0))
	if got := accumulator.Stats().PendingSpans; got != 1 {
		t.Fatalf("expected the client to be waiting, got %d pending", got)
	}
	accumulator.Drain(settledTime)
	if got := accumulator.Stats().PendingSpans; got != 0 {
		t.Fatalf("expected the buffer to have drained, got %d pending", got)
	}
}

func TestDrainAllSettlesSpansThatAreStillWaiting(t *testing.T) {
	accumulator := newTestAccumulator(t)
	accumulator.Observe(baseTime, testSpan(KindClient, "checkout", 0x01, 0))

	// Nothing has expired and the minute is still open.
	if edges := accumulator.Drain(baseTime); len(edges) != 0 {
		t.Fatalf("expected nothing to be drained yet, got %+v", edges)
	}
	if edges := accumulator.DrainAll(); len(edges) != 1 {
		t.Fatalf("expected shutdown to flush the call, got %+v", edges)
	}
}

func sameEdge(a, b Edge) bool {
	return reflect.DeepEqual(a, b)
}

/* ------------------------------------------------ queue hops, paired on links */

// consumerTraceID is a second trace: a consumer that links back to its producer
// is not in the producer's trace, which is the whole reason links are read.
var consumerTraceID = []byte{0xbb}

// linkedConsumer is a CONSUMER in its own trace, naming producers by link the
// way messaging instrumentation does — no parent, because its producer is not
// its parent.
func linkedConsumer(service string, spanID byte, producers ...Span) Span {
	span := testSpan(KindConsumer, service, spanID, 0)
	span.TraceID = consumerTraceID
	for _, producer := range producers {
		span.Links = append(span.Links, SpanLink{
			TraceID: producer.TraceID,
			SpanID:  producer.SpanID,
		})
	}
	return span
}

func TestConsumerLinkedToAProducerInAnotherTraceMakesAQueueEdge(t *testing.T) {
	accumulator := newTestAccumulator(t)
	producer := testSpan(KindProducer, "checkout", 0x01, 0)
	producer.DurationMS = 7

	edges := drainEdges(t, accumulator, producer, linkedConsumer("accounting", 0x02, producer))

	if len(edges) != 1 {
		t.Fatalf("expected one edge, got %d: %+v", len(edges), edges)
	}
	edge := edges[0]
	if edge.Kind != EdgeQueue {
		t.Fatalf("expected edge kind %q, got %q", EdgeQueue, edge.Kind)
	}
	if edge.ClientService != "checkout" || edge.ServerService != "accounting" {
		t.Fatalf("wrong services: %q -> %q", edge.ClientService, edge.ServerService)
	}
	if edge.CallCount != 1 {
		t.Fatalf("expected one message delivered, got %d", edge.CallCount)
	}
	// The producer's own duration: how long the publish took. It is not the
	// delivery lag, which crosses two clocks.
	if len(edge.LatencyMS) != 1 || edge.LatencyMS[0] != 7 {
		t.Fatalf("expected the producer's 7ms publish, got %v", edge.LatencyMS)
	}
}

func TestALinkedQueueHopPairsWhicheverHalfArrivesFirst(t *testing.T) {
	producer := testSpan(KindProducer, "checkout", 0x01, 0)
	consumer := linkedConsumer("accounting", 0x02, producer)

	producerFirst := drainEdges(t, newTestAccumulator(t), producer, consumer)
	consumerFirst := drainEdges(t, newTestAccumulator(t), consumer, producer)

	if len(producerFirst) != 1 || len(consumerFirst) != 1 {
		t.Fatalf("expected one edge each way, got %d and %d", len(producerFirst), len(consumerFirst))
	}
	if !sameEdge(producerFirst[0], consumerFirst[0]) {
		t.Fatalf("arrival order changed the edge: %+v vs %+v", producerFirst[0], consumerFirst[0])
	}
}

// One published message reaches every consumer group. In the OpenTelemetry Demo
// that is `accounting` and `fraud-detection` reading the same `orders` topic,
// and both dependencies are real.
func TestOneProducerReachesEveryConsumingService(t *testing.T) {
	accumulator := newTestAccumulator(t)
	producer := testSpan(KindProducer, "checkout", 0x01, 0)

	edges := drainEdges(t, accumulator, producer,
		linkedConsumer("accounting", 0x02, producer),
		linkedConsumer("fraud-detection", 0x03, producer),
	)

	if len(edges) != 2 {
		t.Fatalf("expected an edge to each consumer, got %+v", edges)
	}
	for _, edge := range edges {
		if edge.ClientService != "checkout" || edge.CallCount != 1 {
			t.Fatalf("expected one message on %q -> %q, got %d",
				edge.ClientService, edge.ServerService, edge.CallCount)
		}
	}
	if edges[0].ServerService != "accounting" || edges[1].ServerService != "fraud-detection" {
		t.Fatalf("wrong callees: %q and %q", edges[0].ServerService, edges[1].ServerService)
	}
}

// A batch consume links every message it took. The edge counts messages, not
// batches: one batch of three is three deliveries.
func TestABatchConsumeCountsOneCallPerMessage(t *testing.T) {
	accumulator := newTestAccumulator(t)
	first := testSpan(KindProducer, "checkout", 0x01, 0)
	second := testSpan(KindProducer, "checkout", 0x02, 0)
	third := testSpan(KindProducer, "checkout", 0x03, 0)

	edges := drainEdges(t, accumulator, first, second, third,
		linkedConsumer("accounting", 0x04, first, second, third))

	if len(edges) != 1 {
		t.Fatalf("expected one edge, got %+v", edges)
	}
	if edges[0].CallCount != 3 {
		t.Fatalf("expected three messages on the edge, got %d", edges[0].CallCount)
	}
}

// A batch that spans two publishers is two dependencies, each carrying the
// messages that came from it.
func TestABatchConsumeSplitsAcrossThePublishersItTookFrom(t *testing.T) {
	accumulator := newTestAccumulator(t)
	fromCheckout := testSpan(KindProducer, "checkout", 0x01, 0)
	fromCart := testSpan(KindProducer, "cart", 0x02, 0)

	edges := drainEdges(t, accumulator, fromCheckout, fromCart,
		linkedConsumer("accounting", 0x03, fromCheckout, fromCart))

	if len(edges) != 2 {
		t.Fatalf("expected one edge per publisher, got %+v", edges)
	}
	for _, edge := range edges {
		if edge.CallCount != 1 {
			t.Fatalf("expected one message on %q -> %q, got %d",
				edge.ClientService, edge.ServerService, edge.CallCount)
		}
	}
}

// The conventions have a consumer emit a receive span and a process span, both
// linked to the same message. That is one delivery, not two.
func TestOneServiceReceivingAndProcessingOneMessageCountsItOnce(t *testing.T) {
	accumulator := newTestAccumulator(t)
	producer := testSpan(KindProducer, "checkout", 0x01, 0)

	edges := drainEdges(t, accumulator, producer,
		linkedConsumer("accounting", 0x02, producer),
		linkedConsumer("accounting", 0x03, producer),
	)

	if len(edges) != 1 {
		t.Fatalf("expected one edge, got %+v", edges)
	}
	if edges[0].CallCount != 1 {
		t.Fatalf("expected one delivery, got %d", edges[0].CallCount)
	}
}

// An instrumentation that both makes the consumer a child and links back names
// one message twice.
func TestAConsumerThatIsBothChildAndLinkedCountsOneMessage(t *testing.T) {
	accumulator := newTestAccumulator(t)
	producer := testSpan(KindProducer, "checkout", 0x01, 0)
	consumer := testSpan(KindConsumer, "accounting", 0x02, 0x01)
	consumer.Links = []SpanLink{{TraceID: producer.TraceID, SpanID: producer.SpanID}}

	edges := drainEdges(t, accumulator, producer, consumer)

	if len(edges) != 1 || edges[0].CallCount != 1 {
		t.Fatalf("expected one message on one edge, got %+v", edges)
	}
}

// A link on a SERVER span is not evidence of a call. A retry naming the attempt
// it repeats would otherwise be counted as a second call the client never made.
func TestALinkOnAServerSpanDoesNotMakeACallEdge(t *testing.T) {
	accumulator := newTestAccumulator(t)
	client := testSpan(KindClient, "checkout", 0x01, 0)
	server := testSpan(KindServer, "payments", 0x02, 0)
	server.TraceID = consumerTraceID
	server.Links = []SpanLink{{TraceID: client.TraceID, SpanID: client.SpanID}}

	edges := drainEdges(t, accumulator, client, server)

	if len(edges) != 1 {
		t.Fatalf("expected only the unpaired client, got %+v", edges)
	}
	if edges[0].ServerService != UninstrumentedService {
		t.Fatalf("expected the client to stay unpaired, got %q", edges[0].ServerService)
	}
}

// Ids reach this package hex-encoded into fixed-width columns, so an absent
// parent is a run of NUL bytes rather than an empty slice, and an all-zeroes id
// is a run of ASCII zeroes. Neither names a span.
func TestAPaddedAbsentParentIsNotMistakenForACaller(t *testing.T) {
	accumulator := newTestAccumulator(t)
	rootServer := testSpan(KindServer, "checkout", 0x01, 0)
	rootServer.ParentSpanID = make([]byte, 16)
	zeroed := testSpan(KindServer, "cart", 0x02, 0)
	zeroed.ParentSpanID = []byte("0000000000000000")

	for _, span := range []Span{rootServer, zeroed} {
		if minute, _ := accumulator.Observe(baseTime, span); !minute.IsZero() {
			t.Fatalf("%s was given a settlement minute %s", span.ServiceName, minute)
		}
	}
	if got := accumulator.Stats().Ignored; got != 2 {
		t.Fatalf("expected both root spans ignored, got %d", got)
	}
	if got := accumulator.Stats().PendingSpans; got != 0 {
		t.Fatalf("expected nothing parked, got %d", got)
	}
}

// A queue hop crosses traces, and trace ids are not unique across tenants.
func TestALinkDoesNotPairAcrossTenants(t *testing.T) {
	accumulator := newTestAccumulator(t)
	producer := testSpan(KindProducer, "checkout", 0x01, 0)
	consumer := linkedConsumer("accounting", 0x02, producer)
	consumer.TenantID = "tenant-b"

	if edges := drainEdges(t, accumulator, producer, consumer); len(edges) != 0 {
		t.Fatalf("expected no cross-tenant edge, got %+v", edges)
	}
}
