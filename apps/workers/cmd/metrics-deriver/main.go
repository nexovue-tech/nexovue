// Command metrics-deriver derives exact counters from every trace, before sampling.
package main

import "github.com/nexovue-tech/nexovue/apps/workers/internal/worker"

// serviceName is what this binary calls itself in logs and health responses.
const serviceName = "metrics-deriver"

func main() {
	worker.Main(serviceName)
}
