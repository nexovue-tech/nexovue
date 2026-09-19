package ratelimit

import (
	"context"
	"fmt"
	"time"

	"github.com/valkey-io/valkey-go"
)

// ValkeyCounter keeps the counters in Valkey.
type ValkeyCounter struct {
	client valkey.Client
}

// NewValkeyCounter returns a counter over an existing connection.
func NewValkeyCounter(client valkey.Client) ValkeyCounter {
	return ValkeyCounter{client: client}
}

// Increment pipelines INCRBY and EXPIRE into one round trip. No Lua: two commands
// sent together are cheaper than a script the server has to look up.
func (c ValkeyCounter) Increment(ctx context.Context, key string, by int64, ttl time.Duration) (int64, error) {
	results := c.client.DoMulti(ctx,
		c.client.B().Incrby().Key(key).Increment(by).Build(),
		// NX so only the first request of a window sets the TTL. Without it
		// every request would push the expiry out and the window would slide.
		c.client.B().Expire().Key(key).Seconds(int64(ttl.Seconds())).Nx().Build(),
	)

	count, err := results[0].AsInt64()
	if err != nil {
		return 0, fmt.Errorf("incrementing: %w", err)
	}
	if err := results[1].Error(); err != nil {
		return 0, fmt.Errorf("expiring: %w", err)
	}
	return count, nil
}
