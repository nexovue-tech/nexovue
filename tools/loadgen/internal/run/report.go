package run

import (
	"fmt"
	"io"
	"sort"
	"time"

	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/config"
	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/lag"
	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/measure"
)

// Report is one run's observations, in the shape §4 of the phase 1 plan is
// filled in from.
type Report struct {
	Config    config.Config
	Transport measure.Result
	Lag       lag.Result
	// LagBoundBy is the poll interval every lag figure is accurate to.
	LagBoundBy time.Duration
}

// Write prints the report. Budgets it cannot speak to are named as such, so a
// gap in the measurement never reads as a passing number.
func (r Report) Write(w io.Writer) error {
	out := &printer{w: w}

	out.printf("\nnexovue load run — %s, %s offered at %.0f spans/sec across %d workers\n",
		r.Config.Endpoint, r.Config.Duration, r.Config.SpanRate, r.Config.Workers)
	out.printf("batch size %d, %d spans per trace, service %q\n\n",
		r.Config.BatchSize, r.Config.SpansPerTrace(), r.Config.ServiceName)

	r.writeTransport(out)
	r.writeLatency(out)
	r.writeThroughput(out)
	r.writeLag(out)

	out.printf("\nnot measured here: receiver CPU and memory (read `docker stats` during the run),\n")
	out.printf("trace-split re-encode CPU share (needs a profile or benchmark inside the receiver).\n")

	return out.err
}

func (r Report) writeTransport(out *printer) {
	t := r.Transport
	out.printf("requests           %d in %s\n", t.Requests, t.Window.Round(time.Millisecond))
	out.printf("spans offered      %d\n", t.SpansOffered)
	out.printf("spans accepted     %d\n", t.SpansAccepted)
	if t.TransportFailures > 0 {
		out.printf("unanswered         %d\n", t.TransportFailures)
	}
	out.printf("responses          %s\n", statusSummary(t.Statuses))
}

func (r Report) writeLatency(out *printer) {
	l := r.Transport.Latency
	out.printf("\naccept latency     p50 %s · p90 %s · p99 %s · max %s\n",
		ms(l.P50), ms(l.P90), ms(l.P99), ms(l.Max))
	out.printf("                   measured at the proxy, covering the receiver call only\n")
}

func (r Report) writeThroughput(out *printer) {
	t := r.Transport.Throughput
	if t.Seconds == 0 {
		out.printf("\nthroughput         — run too short to hold a full second\n")
		return
	}
	out.printf("\nthroughput         sustained %.0f spans/sec (median of %d whole seconds)\n",
		t.Sustained, t.Seconds)
	out.printf("                   mean %.0f · peak %.0f spans/sec\n", t.Mean, t.Peak)
}

func (r Report) writeLag(out *printer) {
	l := r.Lag
	if l.Failure != nil {
		out.printf("\nqueryable lag      — not measured: %v\n", l.Failure)
		return
	}
	if l.Samples == 0 {
		out.printf("\nqueryable lag      — no followed trace became queryable (%d lost)\n", l.Lost)
		return
	}
	out.printf("\nqueryable lag      p50 %s · p90 %s · p99 %s · max %s\n",
		l.P50.Round(time.Millisecond), l.P90.Round(time.Millisecond),
		l.P99.Round(time.Millisecond), l.Max.Round(time.Millisecond))
	out.printf("                   %d traces followed, %d never arrived, accurate to %s\n",
		l.Samples, l.Lost, r.LagBoundBy)
}

// printer writes the report and keeps the first failure, so a broken pipe is
// reported once rather than checked at every line.
type printer struct {
	w   io.Writer
	err error
}

func (p *printer) printf(format string, args ...any) {
	if p.err != nil {
		return
	}
	_, p.err = fmt.Fprintf(p.w, format, args...)
}

func statusSummary(statuses map[int]int) string {
	if len(statuses) == 0 {
		return "none"
	}
	codes := make([]int, 0, len(statuses))
	for code := range statuses {
		codes = append(codes, code)
	}
	sort.Ints(codes)

	summary := ""
	for i, code := range codes {
		if i > 0 {
			summary += " · "
		}
		summary += fmt.Sprintf("%d×%d", statuses[code], code)
	}
	return summary
}

func ms(d time.Duration) string {
	return fmt.Sprintf("%.2fms", float64(d.Nanoseconds())/float64(time.Millisecond))
}
