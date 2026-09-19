//go:build storeprobe

package allowance

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// The allowance statement, proved against a real control plane (F31, FR27).
//
// `usageFrom` is tested as arithmetic elsewhere; this runs `readMonthlyUsage`
// itself, which is the only thing that proves a complimentary grant is read:
// while active its plan prices the allowance and its services size it, and once
// ended the paid plan governs again with nothing rewritten.
//
// Behind a build tag, like the workers' store probe: it needs a running Postgres
// with the migrations applied. Run it with
//
//	PROBE_PG_DSN=postgres://nexovue:nexovue_dev@127.0.0.1:15432/nexovue \
//		go test -tags storeprobe -count=1 ./internal/allowance/

const probeSlug = "__f31_grant_probe__"

func TestAComplimentaryGrantGovernsTheAllowanceUntilItEnds(t *testing.T) {
	ctx := context.Background()
	dsn := os.Getenv("PROBE_PG_DSN")
	if dsn == "" {
		t.Fatal("PROBE_PG_DSN is not set; this probe needs a live control plane")
	}
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatalf("connecting: %v", err)
	}
	t.Cleanup(pool.Close)

	var orgID string
	if err := pool.QueryRow(ctx,
		`INSERT INTO organizations (slug, name) VALUES ($1, 'Grant probe') RETURNING id::text`,
		probeSlug,
	).Scan(&orgID); err != nil {
		t.Fatalf("seeding the organization: %v", err)
	}
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(), `DELETE FROM organizations WHERE id = $1`, orgID)
	})

	reader := NewPostgresUsage(pool)
	now := time.Now().UTC()
	read := func() Usage {
		t.Helper()
		usage, err := reader.UsageFor(ctx, orgID, monthOf(now), now)
		if err != nil {
			t.Fatalf("reading the allowance: %v", err)
		}
		return usage
	}

	// On `free`: the plan's own ceiling of three services at 2 GiB each.
	if got, want := read().IncludedBytes, 3*2*gib; got != want {
		t.Fatalf("free allowance = %d, want %d", got, want)
	}

	// Comped to `pro` for five services: 10 GiB each, sized on the grant.
	if _, err := pool.Exec(ctx, `
		INSERT INTO plan_grants (org_id, plan_slug, services, starts_at, ends_at, reason)
		VALUES ($1, 'pro', 5, now() - interval '1 minute', now() + interval '30 days', 'probe')`,
		orgID,
	); err != nil {
		t.Fatalf("granting: %v", err)
	}
	granted := read()
	if !granted.Enforced || granted.IncludedBytes != 5*10*gib {
		t.Fatalf("granted allowance = %+v, want 5 services of pro (%d)", granted, 5*10*gib)
	}

	// Ended early: the paid plan governs again, and `organizations.plan` was never touched.
	if _, err := pool.Exec(ctx,
		`UPDATE plan_grants SET ended_at = now() - interval '1 second' WHERE org_id = $1`, orgID,
	); err != nil {
		t.Fatalf("ending the grant: %v", err)
	}
	if got, want := read().IncludedBytes, 3*2*gib; got != want {
		t.Fatalf("allowance after the grant ended = %d, want the free %d", got, want)
	}
}
