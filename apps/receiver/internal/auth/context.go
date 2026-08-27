package auth

import "context"

type identityKey struct{}

// NewContext returns a context carrying the tenancy a request authenticated as.
// Only the auth gate puts one there, so anything downstream that finds none is
// running outside the gate order and is a bug, not an unauthenticated request.
func NewContext(ctx context.Context, identity Identity) context.Context {
	return context.WithValue(ctx, identityKey{}, identity)
}

// FromContext returns the tenancy ctx was authenticated as.
func FromContext(ctx context.Context) (Identity, bool) {
	identity, ok := ctx.Value(identityKey{}).(Identity)
	return identity, ok
}
