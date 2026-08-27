package measure

import (
	"sort"
	"sync"
	"time"
)

// bucketWidth is the accounting period for sustained throughput. One second
// matches the unit the budget is written in.
const bucketWidth = time.Second

// Observation is what the proxy learned from forwarding one export.
type Observation struct {
	Spans      int
	Status     int
	Latency    time.Duration
	AcceptedAt time.Time
}

// Recorder accumulates every observation of a run.
//
// A forwarded export ends in one of three states, and they are kept apart
// because they mean different things to a count: the receiver answered, the
// receiver was reached but its answer could not be read, or nothing left this
// process. Only the first two put spans on the wire.
//
// It holds one latency sample per request rather than per span: at realistic
// batch sizes a minute of load is a few thousand samples, which is small enough
// to keep exactly and so avoids the error an approximate histogram would add to
// a p99 the budget is judged on.
type Recorder struct {
	mu        sync.Mutex
	latencies []time.Duration
	statuses  map[int]int
	// perSecond counts accepted spans against the second they landed in.
	perSecond map[int64]int

	requests      int
	spansOffered  int
	spansAccepted int
	spansRefused  int

	unknownRequests int
	unknownSpans    int
	notSentRequests int
	notSentSpans    int

	firstAcceptAt  time.Time
	lastAcceptedAt time.Time
}

// NewRecorder returns an empty recorder.
func NewRecorder() *Recorder {
	return &Recorder{
		statuses:  make(map[int]int),
		perSecond: make(map[int64]int),
	}
}

// ObserveAnswered records one forwarded export the receiver replied to.
func (r *Recorder) ObserveAnswered(o Observation) {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.requests++
	r.spansOffered += o.Spans
	r.latencies = append(r.latencies, o.Latency)
	r.statuses[o.Status]++

	if o.Status != accepted {
		r.spansRefused += o.Spans
		return
	}
	r.spansAccepted += o.Spans
	r.perSecond[o.AcceptedAt.Unix()] += o.Spans
	if r.firstAcceptAt.IsZero() {
		r.firstAcceptAt = o.AcceptedAt
	}
	r.lastAcceptedAt = o.AcceptedAt
}

// ObserveUnknown records an export that reached the receiver but whose reply
// could not be read. The spans still count as offered — they went out — and the
// receiver may already have published them, so they are neither accepted nor
// refused and the run's exact-count comparison is only good to within them.
//
// It contributes no latency sample: a request with no answer has no round trip
// to time, and a timeout entered as a latency would be the request timeout
// rather than anything the receiver did.
func (r *Recorder) ObserveUnknown(spans int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.requests++
	r.spansOffered += spans
	r.unknownRequests++
	r.unknownSpans += spans
}

// ObserveNotSent records an export that never reached the receiver, so its
// spans were never offered and cannot be anywhere downstream.
func (r *Recorder) ObserveNotSent(spans int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.requests++
	r.notSentRequests++
	r.notSentSpans += spans
}

// Latencies holds the distribution the accept-latency budget is read from.
type Latencies struct {
	P50 time.Duration
	P90 time.Duration
	P99 time.Duration
	Max time.Duration
}

// Throughput holds the accepted-span rate over the run.
type Throughput struct {
	// Sustained is the median full second of the run. The budget asks for a
	// sustained rate, so the typical second is the honest answer, not the mean
	// over a window whose ends are partial.
	Sustained float64
	Mean      float64
	Peak      float64
	// Seconds is how many whole seconds the median was taken over.
	Seconds int
}

// Result is everything one run observed at the transport.
//
// SpansOffered is what went out to the receiver, so it splits exactly three
// ways: SpansAccepted + SpansRefused + UnknownSpans. NotSentSpans sit outside
// that sum because they never left this process.
type Result struct {
	Requests      int
	SpansOffered  int
	SpansAccepted int
	SpansRefused  int

	// Unknown covers requests the receiver was given and did not answer
	// readably. Their spans may or may not have been published, which is the
	// tolerance on any comparison against a downstream count.
	UnknownRequests int
	UnknownSpans    int

	// NotSent covers requests that failed before reaching the receiver.
	NotSentRequests int
	NotSentSpans    int

	Statuses   map[int]int
	Window     time.Duration
	Latency    Latencies
	Throughput Throughput
}

// AnsweredRequests is how many requests the receiver replied to, which is the
// population every latency percentile is taken over.
func (r Result) AnsweredRequests() int {
	return r.Requests - r.UnknownRequests - r.NotSentRequests
}

// Result summarises the run. Safe to call once the load has stopped.
func (r *Recorder) Result() Result {
	r.mu.Lock()
	defer r.mu.Unlock()

	statuses := make(map[int]int, len(r.statuses))
	for status, count := range r.statuses {
		statuses[status] = count
	}

	return Result{
		Requests:        r.requests,
		SpansOffered:    r.spansOffered,
		SpansAccepted:   r.spansAccepted,
		SpansRefused:    r.spansRefused,
		UnknownRequests: r.unknownRequests,
		UnknownSpans:    r.unknownSpans,
		NotSentRequests: r.notSentRequests,
		NotSentSpans:    r.notSentSpans,
		Statuses:        statuses,
		Window:          r.lastAcceptedAt.Sub(r.firstAcceptAt),
		Latency:         latencies(r.latencies),
		Throughput:      throughput(r.perSecond),
	}
}

func latencies(samples []time.Duration) Latencies {
	if len(samples) == 0 {
		return Latencies{}
	}
	sorted := make([]time.Duration, len(samples))
	copy(sorted, samples)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })

	return Latencies{
		P50: percentile(sorted, 0.50),
		P90: percentile(sorted, 0.90),
		P99: percentile(sorted, 0.99),
		Max: sorted[len(sorted)-1],
	}
}

// percentile uses the nearest-rank definition: the smallest sample at or above
// the requested share. No interpolation, so every figure reported is a latency
// that was actually observed.
func percentile(sorted []time.Duration, share float64) time.Duration {
	rank := int(share*float64(len(sorted)) + 0.5)
	if rank < 1 {
		rank = 1
	}
	if rank > len(sorted) {
		rank = len(sorted)
	}
	return sorted[rank-1]
}

// throughput summarises the per-second buckets, dropping the first and last.
// Those two are partial by construction — the run starts and stops mid-second —
// and counting them would understate a rate the rest of the run held.
func throughput(perSecond map[int64]int) Throughput {
	if len(perSecond) <= 2 {
		return Throughput{}
	}

	seconds := make([]int64, 0, len(perSecond))
	for second := range perSecond {
		seconds = append(seconds, second)
	}
	sort.Slice(seconds, func(i, j int) bool { return seconds[i] < seconds[j] })

	counts := make([]int, 0, len(seconds)-2)
	total := 0
	peak := 0
	for _, second := range seconds[1 : len(seconds)-1] {
		count := perSecond[second]
		counts = append(counts, count)
		total += count
		if count > peak {
			peak = count
		}
	}

	sort.Ints(counts)
	return Throughput{
		Sustained: float64(counts[len(counts)/2]) / bucketWidth.Seconds(),
		Mean:      float64(total) / float64(len(counts)) / bucketWidth.Seconds(),
		Peak:      float64(peak) / bucketWidth.Seconds(),
		Seconds:   len(counts),
	}
}
