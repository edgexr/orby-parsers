# Adding / fixing a parser

The `detect()` / `parse()` IO contract every parser must satisfy is in
**[CONTRACT.md](CONTRACT.md)** — read it first. This file is the
workflow.

## Where a parser lives

| kind | directory | dispatcher |
|---|---|---|
| bank / credit-card / brokerage PDF | `scripts/institutions/` | `scripts/bank_statement.py` |
| CSV / `.xlsx` export | `scripts/csv_institutions/` | `scripts/csv_statement.py` |

**There is no `_PARSERS` list to edit.** Each dispatcher builds its
try-list by discovering every `*.py` in its parser directory that
exposes a `detect()`/`parse()` pair (`parser_common.discover_parsers`) —
adding a parser is just adding a file. The order is `(PRIORITY,
filename)`: set a module-scope `PRIORITY` int (default 100) only when a
module's `detect()` could shadow another's and precedence matters —
`institutions/bofa_checking_combined.py` (`PRIORITY = 90`) is the only
current example, since its header regex is a superset of
`bofa_checking`'s. Helper modules without both `detect()` and `parse()`
(`common.py`, `check_ocr.py`) are skipped automatically.

`bank_statement.py` is a **combined** dispatcher: bank/credit-card
(`KIND_BANK`, the implicit default) and brokerage (`KIND =
parser_common.KIND_BROKERAGE`) parsers sit in one `institutions/`
directory, tried against one shared first-page read, because nothing
about a PDF says up front which kind it is. First match wins regardless
of kind.

Reuse `scripts/institutions/common.py` (`parse_amount`, `last4_digits`,
`apply_running_balance`, `negate_amounts_and_balances`) — a
`csv_institutions/` module reaches it with `from institutions import
common`.

## Workflow

1. Get a **sample file**. For a PDF, redact a real statement (Orby's
   Tools → PDF Redactor) or invent a synthetic one the way
   `tests/generators/gen-*.py` do. For a CSV/`.xlsx`, export it and strip
   account-identifying cells — and note which institution it's from, the
   file usually won't say (CSV `detect()` fingerprints the column-header
   shape, not body text).
2. **Dump what the parser will actually see**:
   `python scripts/bank_statement.py <file> --dump-text` /
   `python scripts/csv_statement.py <file> --dump-rows`. Design regexes
   against *that* — pdfplumber's whitespace/line breaks don't match the
   visual layout.
3. Add `scripts/<dir>/<name>.py` with `detect()` / `parse()`. The
   dispatcher picks it up automatically — no registration step.
4. `python scripts/bank_statement.py <file>` — iterate until institution,
   account, accountType, statementDate and every row (date, description,
   correctly-signed amount, running balance) are right.
5. Add a regression test under `tests/`. Put the fixture in
   `tests/fixtures/`; if it's synthetic, add a
   `tests/generators/gen-<name>.py` producing it from invented data so it
   can be committed. Use `conftest.py`'s `run_statement` fixture.
6. `make test`.

Many parsers here arrive as PRs opened from Orby's "Submit via git/gh"
button (Build Transactions Extractor → Manage Parsers): a user generates
a parser against their own statement and contributes it back. Those PRs
carry the parser under `scripts/<institutions|csv_institutions>/`
(filename dash→underscore normalized, auto-discovered — no dispatcher
edit) and a
wholly-synthetic `tests/test_<name>.py` + `tests/fixtures/<...>` +
`tests/generators/gen-<name>-sample.py` — reviewed the same as any
hand-written parser.

## Fixing a bundled parser without a release

Copy the bundled module to `~/.orby/ingest/parsers/<same-name>.py`, edit
it there. A same-named file in that directory *replaces* the bundled
module at its original position in the try order
(`parser_common.merge_parsers`). A differently-named file is a new
parser, tried after all bundled ones.

**This directory's files run sandboxed** (no network, filesystem limited
to the target file's directory and the parsers directory). Stdlib +
`pdfplumber` + `openpyxl` only — no per-plugin dependency install. A file
that fails to import, or lacks `detect`/`parse`, is skipped with its
error folded into the "why wasn't this recognized" diagnostic.

A CSV-shaped module dropped in is simply never matched by
`bank_statement.py` (its `detect()` gets the wrong argument count → an
ordinary non-match), and vice versa — no naming convention needed to
keep the two kinds apart.
