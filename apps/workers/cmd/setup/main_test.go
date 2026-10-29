package main

import (
	"os"
	"path/filepath"
	"testing"
)

// `setup schema` names the newest ClickHouse migration the image carries: `nexovue rollback`
// compares two versions' answers to tell whether an upgrade changed the schema.
func TestSchemaIsTheNewestMigration(t *testing.T) {
	directory := t.TempDir()
	for _, name := range []string{
		"000001_first.up.sql", "000001_first.down.sql",
		"000012_twelfth.up.sql", "000012_twelfth.down.sql",
	} {
		if err := os.WriteFile(filepath.Join(directory, name), []byte("SELECT 1"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("SETUP_MIGRATIONS_DIR", directory)

	newest, err := newestMigration()
	if err != nil {
		t.Fatal(err)
	}
	if newest != 12 {
		t.Fatalf("newest migration = %d, want 12", newest)
	}
}
