// Command log-writer writes log records to the telemetry store.
package main

import "github.com/nexovue-tech/nexovue/apps/workers/internal/worker"

// serviceName is what this binary calls itself in logs and health responses.
const serviceName = "log-writer"

func main() {
	worker.Main(serviceName)
}
