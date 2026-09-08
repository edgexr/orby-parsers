// Package orbyparsers embeds the bundled statement/transaction parsers
// (the Python dispatchers, the per-institution parser modules, and the
// shared parse() IO contract in parser_common.py) plus their standalone
// test suite, so a Go program - Orby - can write the whole tree to disk
// and run it without carrying its own copy.
//
// The Python side of this repo runs standalone with no Go at all (see
// the top-level Makefile / pyproject.toml). This file exists purely so
// Orby's build can //go:embed the same tree via a replace directive
// (github.com/edgexr/orby-parsers => ./submodules/parsers) rather than a
// build-time copy step.
package orbyparsers

import (
	"embed"
	"io/fs"
	"os"
	"path/filepath"
)

// ScriptsFS holds everything under scripts/: the two dispatchers
// (bank_statement.py, csv_statement.py), parser_common.py (dispatch
// machinery + parse() IO-contract validation), vision_client.py, and the
// institutions/ and csv_institutions/ parser packages. The on-disk shape
// is significant: a dispatcher sits next to the institutions/ package so
// "from institutions import common" resolves - WriteScripts preserves it.
//
//go:embed scripts
var ScriptsFS embed.FS

// TestsFS holds the standalone pytest suite under tests/ - conftest.py,
// the converted regression tests, the committed synthetic fixtures, and
// the fixture generators. Orby writes this alongside ScriptsFS when a
// user runs the parser test suite from the app (Build Transactions
// Extractor -> Manage Parsers -> Run Tests).
//
//go:embed tests
var TestsFS embed.FS

// WriteScripts writes the scripts/ tree (minus the "scripts/" prefix)
// into dir. Existing files are overwritten.
func WriteScripts(dir string) error { return writeTree(ScriptsFS, "scripts", dir) }

// WriteTests writes the tests/ tree (minus the "tests/" prefix) into dir.
func WriteTests(dir string) error { return writeTree(TestsFS, "tests", dir) }

func writeTree(efs embed.FS, root, dir string) error {
	return fs.WalkDir(efs, root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		out := filepath.Join(dir, rel)
		if d.IsDir() {
			return os.MkdirAll(out, 0o700)
		}
		data, err := efs.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(out, data, 0o600)
	})
}
