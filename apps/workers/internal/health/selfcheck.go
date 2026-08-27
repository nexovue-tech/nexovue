package health

import (
	"fmt"
	"net"
	"net/http"
	"os"
	"time"
)

// selfCheckTimeout bounds the probe so a wedged server fails the check rather
// than hanging the container runtime's healthcheck slot.
const selfCheckTimeout = 3 * time.Second

// RunSelfCheckIfAsked handles `<binary> -health`, exiting 0 when this process's
// own liveness endpoint answers and 1 when it does not.
//
// It exists because the production image is distroless: there is no shell, no
// curl and no wget for a container healthcheck to call. The binary is the only
// executable in the image, so it has to be able to check itself.
func RunSelfCheckIfAsked(args []string, addr string) {
	if len(args) < 2 || args[1] != "-health" {
		return
	}

	// The listener binds every interface; the probe always talks to loopback.
	_, port, err := net.SplitHostPort(addr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "health: cannot parse address %q: %v\n", addr, err)
		os.Exit(1)
	}

	client := &http.Client{Timeout: selfCheckTimeout}
	resp, err := client.Get("http://127.0.0.1:" + port + "/healthz")
	if err != nil {
		fmt.Fprintf(os.Stderr, "health: %v\n", err)
		os.Exit(1)
	}
	// The process exits either way; a failed close changes nothing.
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		fmt.Fprintf(os.Stderr, "health: got %s\n", resp.Status)
		os.Exit(1)
	}
	os.Exit(0)
}
