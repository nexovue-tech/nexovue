package auth

import "github.com/prometheus/client_golang/prometheus"

// StaleServes counts keys accepted from the stale window because the control
// plane could not answer (group 9, C1). Steady growth is a Postgres outage the
// receiver is riding out; a monitor should say so before the window closes.
var StaleServes = prometheus.NewCounter(prometheus.CounterOpts{
	Name: "nexovue_receiver_stale_key_serves_total",
	Help: "Ingest and site keys accepted from the stale window during a control-plane outage.",
})
