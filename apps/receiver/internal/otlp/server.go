package otlp

import (
	"net/http"
	"time"
)

// Timeouts for the ingest listener. Reading is generous because a compressed
// span batch arrives over whatever link the customer has; writing is generous
// because the handler is still waiting on the broker's acknowledgement when the
// clock is running.
const (
	readHeaderTimeout = 5 * time.Second
	readTimeout       = 30 * time.Second
	writeTimeout      = 30 * time.Second
	idleTimeout       = 60 * time.Second
)

// NewServer returns the ingest listener. It is separate from the admin listener
// so health probes still answer when this port is saturated.
func NewServer(addr string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: readHeaderTimeout,
		ReadTimeout:       readTimeout,
		WriteTimeout:      writeTimeout,
		IdleTimeout:       idleTimeout,
	}
}
