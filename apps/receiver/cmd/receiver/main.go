// Command receiver is the ingestion edge: it terminates OTLP, authenticates the
// caller, tags tenancy, normalizes to the internal envelope, and publishes to Kafka.
package main

import (
	"fmt"
	"os"
)

func main() {
	// Wiring lands in P1-64 (config + health) and P1-12 (the OTLP endpoint).
	fmt.Fprintln(os.Stderr, "receiver: not implemented yet")
	os.Exit(1)
}
