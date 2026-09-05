package usage

import (
	"regexp"
	"slices"
	"strings"
	"testing"
)

// The counters statement is read here as text, because the defect it guards
// against is invisible everywhere else. A counter added to the INSERT list and
// forgotten in the ON CONFLICT clause lands on the first write for a tenant-day
// and never moves again: the column keeps a plausible number, no test that goes
// through `columnsOf` or the meter notices, and the door it feeds silently
// stops refusing. That is what happened to `browser_bytes` — deleting its line
// from the SET clause left the whole suite green (ADR 0097).
//
// Proving it against a real table needs Postgres; `storeprobe_test.go` is that
// test. This one runs everywhere and covers the whole class, not one column.

// keyColumns is the conflict target: the row's identity, not a counter. These
// are the columns the SET clause must leave alone.
var keyColumns = []string{"org_id", "project_id", "day"}

// Every counter the statement inserts is also accumulated when the row already
// exists. Adding rather than replacing is the rule the statement's own comment
// gives: a day's row is written many times as the day goes on.
func TestEveryCounterInsertedIsAlsoAccumulatedOnConflict(t *testing.T) {
	inserted := columnsListedAfter(t, addCounters, "INSERT INTO usage_daily AS existing")
	updates := oneLine(after(t, addCounters, "DO UPDATE"))

	for _, column := range inserted {
		if slices.Contains(keyColumns, column) {
			continue
		}
		want := column + " = existing." + column + " + excluded." + column
		if !strings.Contains(updates, want) {
			t.Errorf("%q is inserted but never accumulated; want %q in the SET clause.\n"+
				"A counter written only on the first flush of a tenant-day stops moving, "+
				"and whatever reads it stops being true", column, want)
		}
	}
}

// The row's identity is never assigned on conflict: it is what the conflict
// matched on, so writing it is at best a no-op and at worst a row moved to
// another tenant.
func TestTheConflictKeyIsNotOverwritten(t *testing.T) {
	updates := oneLine(after(t, addCounters, "DO UPDATE"))

	for _, column := range keyColumns {
		if strings.Contains(updates, column+" =") {
			t.Errorf("the SET clause assigns %q, which is part of the conflict key", column)
		}
	}
}

// The inserted columns, the `unnest` arity and the alias the rows are read
// through move together. A column added to two of the three is a statement
// Postgres rejects at runtime and nothing here would otherwise catch.
func TestTheColumnListTheUnnestAndTheAliasAgree(t *testing.T) {
	inserted := columnsListedAfter(t, addCounters, "INSERT INTO usage_daily AS existing")
	aliased := columnsListedAfter(t, addCounters, "AS counted")

	if !slices.Equal(inserted, aliased) {
		t.Errorf("inserted columns %v, read as %v: the statement writes one row shape "+
			"and reads another", inserted, aliased)
	}
	if placeholders := distinctPlaceholders(addCounters); placeholders != len(inserted) {
		t.Errorf("%d placeholders for %d columns: unnest and the column list disagree",
			placeholders, len(inserted))
	}
}

// columnsListedAfter is the parenthesised column list following a marker.
func columnsListedAfter(t *testing.T, statement, marker string) []string {
	t.Helper()

	rest := after(t, statement, marker)
	open := strings.Index(rest, "(")
	closed := strings.Index(rest, ")")
	if open < 0 || closed < open {
		t.Fatalf("no column list after %q", marker)
	}

	var columns []string
	for _, name := range strings.Split(rest[open+1:closed], ",") {
		columns = append(columns, strings.TrimSpace(name))
	}
	return columns
}

// after is everything past a marker the statement must contain.
func after(t *testing.T, statement, marker string) string {
	t.Helper()

	at := strings.Index(statement, marker)
	if at < 0 {
		t.Fatalf("the statement no longer contains %q", marker)
	}
	return statement[at+len(marker):]
}

// oneLine collapses the alignment whitespace the statement is written with, so
// a match can be written the way it reads rather than as a regexp.
func oneLine(sql string) string { return strings.Join(strings.Fields(sql), " ") }

var placeholderPattern = regexp.MustCompile(`\$\d+`)

func distinctPlaceholders(statement string) int {
	found := placeholderPattern.FindAllString(statement, -1)
	slices.Sort(found)
	return len(slices.Compact(found))
}
