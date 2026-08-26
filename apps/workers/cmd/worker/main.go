// Command worker runs the processing pipelines: trace assembly, log writing,
// metric derivation and service-edge aggregation.
//
// One binary runs all of them by default. WORKER_PIPELINES narrows the set, so
// the same image can be deployed several times with different selections once
// load justifies separating them.
package main

import "github.com/nexovue-tech/nexovue/apps/workers/internal/worker"

const serviceName = "worker"

func main() {
	worker.Main(serviceName)
}
