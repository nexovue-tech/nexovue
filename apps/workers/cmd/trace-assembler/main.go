// Command trace-assembler assembles spans into traces, then samples and writes them.
package main

import (
	"fmt"
	"os"
)

func main() {
	// Wiring lands in P1-64 (config + health); behaviour in M1/M2.
	fmt.Fprintln(os.Stderr, "trace-assembler: not implemented yet")
	os.Exit(1)
}
