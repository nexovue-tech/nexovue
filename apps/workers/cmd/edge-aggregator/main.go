// Command edge-aggregator derives service dependency edges from cross-service spans.
package main

import (
	"fmt"
	"os"
)

func main() {
	// Wiring lands in P1-64 (config + health); behaviour in M1/M2.
	fmt.Fprintln(os.Stderr, "edge-aggregator: not implemented yet")
	os.Exit(1)
}
