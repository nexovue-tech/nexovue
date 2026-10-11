// Command worker runs the processing pipelines: trace assembly, log writing,
// metric derivation and service-edge aggregation.
//
// One binary runs all of them by default. WORKER_PIPELINES narrows the set, so
// the same image can be deployed several times with different selections once
// load justifies separating them.
package main

import (
	"os"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/health"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/worker"
)

const serviceName = "worker"

func main() {
	// `worker -health` is the container healthcheck; the production image is
	// distroless and has no shell to run one with.
	health.RunSelfCheckIfAsked(os.Args, config.SelfCheckAddr())

	worker.Main(serviceName)
}
