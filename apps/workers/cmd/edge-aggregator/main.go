// Command edge-aggregator derives service dependency edges from cross-service spans.
package main

import "github.com/nexovue-tech/nexovue/apps/workers/internal/worker"

// serviceName is what this binary calls itself in logs and health responses.
const serviceName = "edge-aggregator"

func main() {
	worker.Main(serviceName)
}
