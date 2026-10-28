// Package setup prepares the stores a deployment needs before any service starts: the ClickHouse
// schema and the Kafka topics (docs/changes/ready-to-deploy, T1). Running it twice changes nothing.
package setup

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// NoVersion is the ledger's answer before any migration has run. golang-migrate wrote the same
// value, so a database it migrated carries on here unchanged.
const NoVersion int64 = -1

// Migration is one numbered pair of files, `000033_name.up.sql` and `000033_name.down.sql`.
type Migration struct {
	Version int64
	Name    string
	Up      string
	Down    string
}

// Ledger is where a database records how far it has been migrated.
type Ledger interface {
	// Ensure creates the record if the database has none yet.
	Ensure(ctx context.Context) error
	// Current is the last version recorded, and whether a migration to it failed halfway.
	Current(ctx context.Context) (version int64, dirty bool, err error)
	// Record notes a version; dirty is true while its migration is running.
	Record(ctx context.Context, version int64, dirty bool) error
	// Exec runs one migration's statement.
	Exec(ctx context.Context, statement string) error
}

// ErrDirty means an earlier run stopped halfway through a migration. A person has to look:
// the statement may or may not have taken effect, and running it again could do either harm.
var ErrDirty = errors.New("a migration stopped halfway")

var fileName = regexp.MustCompile(`^(\d+)_(.+)\.(up|down)\.sql$`)

// Load reads every migration in the directory, in order, refusing a gap in the pairs.
func Load(dir fs.FS) ([]Migration, error) {
	entries, err := fs.ReadDir(dir, ".")
	if err != nil {
		return nil, fmt.Errorf("read migrations: %w", err)
	}
	byVersion := map[int64]*Migration{}
	for _, entry := range entries {
		parts := fileName.FindStringSubmatch(entry.Name())
		if entry.IsDir() || parts == nil {
			continue
		}
		version, err := strconv.ParseInt(parts[1], 10, 64)
		if err != nil {
			return nil, fmt.Errorf("migration %s: %w", entry.Name(), err)
		}
		body, err := fs.ReadFile(dir, entry.Name())
		if err != nil {
			return nil, fmt.Errorf("read %s: %w", entry.Name(), err)
		}
		found := byVersion[version]
		if found == nil {
			found = &Migration{Version: version, Name: parts[2]}
			byVersion[version] = found
		} else if found.Name != parts[2] {
			return nil, fmt.Errorf("migration %d has two names: %s and %s", version, found.Name, parts[2])
		}
		if parts[3] == "up" {
			found.Up = strings.TrimSpace(string(body))
		} else {
			found.Down = strings.TrimSpace(string(body))
		}
	}

	migrations := make([]Migration, 0, len(byVersion))
	for _, found := range byVersion {
		if found.Up == "" || found.Down == "" {
			return nil, fmt.Errorf("migration %d (%s) needs both an up and a down file", found.Version, found.Name)
		}
		migrations = append(migrations, *found)
	}
	sort.Slice(migrations, func(i, j int) bool { return migrations[i].Version < migrations[j].Version })
	return migrations, nil
}

// Up applies every migration above the recorded version and returns how many ran.
func Up(ctx context.Context, ledger Ledger, migrations []Migration) (int, error) {
	current, err := start(ctx, ledger)
	if err != nil {
		return 0, err
	}
	applied := 0
	for _, migration := range migrations {
		if migration.Version <= current {
			continue
		}
		if err := apply(ctx, ledger, migration.Version, migration.Up); err != nil {
			return applied, fmt.Errorf("migration %d (%s): %w", migration.Version, migration.Name, err)
		}
		applied++
	}
	return applied, nil
}

// Down reverts the most recent `steps` migrations. A development tool: a deployment only goes up.
func Down(ctx context.Context, ledger Ledger, migrations []Migration, steps int) (int, error) {
	current, err := start(ctx, ledger)
	if err != nil {
		return 0, err
	}
	reverted := 0
	for i := len(migrations) - 1; i >= 0 && reverted < steps; i-- {
		migration := migrations[i]
		if migration.Version > current {
			continue
		}
		previous := NoVersion
		if i > 0 {
			previous = migrations[i-1].Version
		}
		if err := apply(ctx, ledger, previous, migration.Down); err != nil {
			return reverted, fmt.Errorf("reverting migration %d (%s): %w", migration.Version, migration.Name, err)
		}
		current = previous
		reverted++
	}
	return reverted, nil
}

func start(ctx context.Context, ledger Ledger) (int64, error) {
	if err := ledger.Ensure(ctx); err != nil {
		return 0, fmt.Errorf("migration ledger: %w", err)
	}
	current, dirty, err := ledger.Current(ctx)
	if err != nil {
		return 0, fmt.Errorf("migration ledger: %w", err)
	}
	if dirty {
		return 0, fmt.Errorf("%w at version %d: check the schema by hand, then record the version", ErrDirty, current)
	}
	return current, nil
}

// apply marks the target dirty, runs the statement, and marks it clean — golang-migrate's order,
// so a crash in between leaves the same evidence it did.
func apply(ctx context.Context, ledger Ledger, target int64, statement string) error {
	if err := ledger.Record(ctx, target, true); err != nil {
		return err
	}
	if err := ledger.Exec(ctx, statement); err != nil {
		return err
	}
	return ledger.Record(ctx, target, false)
}
