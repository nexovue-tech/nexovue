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
// The admin listener rather than a port of its own: a worker answers no traffic,
// so this is already the only port it opens and the only one an operator
// reaches. Off unless the deployment asks for it, because the endpoints are
// unauthenticated and hand out goroutine stacks, the command line and the live
// heap to anything that can reach the port.
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
