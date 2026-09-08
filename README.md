# orby-parsers

Bank, brokerage, and CSV/spreadsheet statement parsers for
[Orby](https://github.com/edgexr/orby)'s transaction ingest. This repo
lets users contribute parsers for their own institutions for others to
use, and runs standalone — no Orby checkout needed.

## Layout

```
scripts/
  bank_statement.py        PDF dispatcher (bank / credit-card / brokerage)
  csv_statement.py         CSV + .xlsx dispatcher
  parser_common.py         dispatch machinery + parse() IO-contract validation
  vision_client.py         OpenAI-compatible vision client (check-image OCR)
  institutions/            PDF parser modules + common.py + check_ocr.py
  csv_institutions/        CSV/.xlsx parser modules
tests/                     standalone pytest suite + committed synthetic fixtures + generators
CONTRACT.md                the detect() / parse() IO contract
CLAUDE.md                  how to add a parser
```

## Running the tests

```
make test          # provisions .venv (pdfplumber, openpyxl, pytest, pillow), runs pytest
make regen-fixtures  # rebuild every committed synthetic fixture from its generator
```

Committed fixtures are wholly synthetic. Tests over redacted real
statements skip themselves when the (gitignored) files aren't present.

## Trying a parser by hand

```
python scripts/bank_statement.py <statement.pdf>            # parse -> JSON
python scripts/bank_statement.py <statement.pdf> --dump-text  # raw extracted text
python scripts/csv_statement.py  <export.csv|.xlsx> --dump-rows
```

## How Orby uses this repo

Orby vendors this as a git submodule at `submodules/parsers` and
`//go:embed`s `scripts/` + `tests/` through a small Go shim (`embed.go`).
At runtime Orby writes the tree out and runs the dispatchers in a
sandbox. Users can also drop extra parser `.py` files into
`~/.orby/ingest/parsers/` without touching either repo — see `CLAUDE.md`.
