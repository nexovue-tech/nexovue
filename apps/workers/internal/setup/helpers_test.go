package setup

import (
	"io/fs"
	"os"
	"testing"
)

func osDir(t *testing.T, path string) fs.FS {
	t.Helper()
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	return os.DirFS(path)
}
