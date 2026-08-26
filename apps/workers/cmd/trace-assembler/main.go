// Command trace-assembler assembles spans into traces, then samples and writes them.
package main

import "github.com/nexovue-tech/nexovue/apps/workers/internal/worker"

// serviceName is what this binary calls itself in logs and health responses.
const serviceName = "trace-assembler"

func main() {
	worker.Main(serviceName)
}
