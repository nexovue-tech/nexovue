package health

import (
	"net/http"
	"net/http/pprof"
	"time"
)

// ProfilingPath is where Go's runtime profiles are served. Fixed, because
// `go tool pprof` builds its URL from it.
const ProfilingPath = "/debug/pprof/"

// WithProfiling mounts Go's runtime profiles in front of the probe endpoints,
// on the same admin listener.
//
// **The admin listener and never a data port.** The receiver's data ports are
// the two OTLP ones, and they are the ports the internet reaches; the admin
// listener carries the probes and nothing else. Checked rather than assumed, on
// 2026-09-07, in all four places that could publish it: the dev compose file
// maps only 4317 and 4318, the app one the same two, the on-prem one publishes
// no receiver port at all, and `deploy/caddy/Caddyfile` proxies only to
// `receiver:4318`. Nothing routes to RECEIVER_HEALTH_ADDR's :8081 from outside
// the compose network. Anything that changes which ports are published has to
// check this again, because the failure is silent: these endpoints are
// unauthenticated, a CPU profile is a remote way to make the process do work,
// and the heap and command line are a remote way to read it.
//
// Off unless the deployment asks for it, for that same reason. The worker made
// the same choice for the same reason and this is deliberately its shape: two
// Go modules, so the code cannot be shared, and a divergence between them would
// be a receiver profiled on terms nobody chose.
func WithProfiling(handler http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/", handler)
	// Index serves every named profile under the subtree — heap, goroutine,
	// allocs. The four beside it are the ones it does not.
	mux.HandleFunc(ProfilingPath, untimed(pprof.Index))
	mux.HandleFunc(ProfilingPath+"cmdline", untimed(pprof.Cmdline))
	mux.HandleFunc(ProfilingPath+"profile", untimed(pprof.Profile))
	mux.HandleFunc(ProfilingPath+"symbol", untimed(pprof.Symbol))
	mux.HandleFunc(ProfilingPath+"trace", untimed(pprof.Trace))
	return mux
}

// untimed clears the listener's write deadline for one profile. A CPU profile
// samples for 30 seconds by default and the admin listener allows a response 10,
// so without this the profiles worth taking are cut off mid-body.
func untimed(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// The zero time means no deadline. A listener that cannot set one leaves
		// the server's shorter timeout in force, which is the safe direction.
		_ = http.NewResponseController(w).SetWriteDeadline(time.Time{})
		next(w, r)
	}
}
