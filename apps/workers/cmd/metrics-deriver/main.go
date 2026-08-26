// Command metrics-deriver derives exact counters from every trace, before sampling.
package main

import (
	"fmt"
	"os"
)

func main() {
	// Wiring lands in P1-64 (config + health); behaviour in M1/M2.
	fmt.Fprintln(os.Stderr, "metrics-deriver: not implemented yet")
	os.Exit(1)
}
