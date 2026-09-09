#!/usr/bin/env python3
"""Parse a bank/credit-card or brokerage spreadsheet export - a plain
CSV or an .xlsx workbook - into structured rows, printed as JSON on
stdout.

A .csv is just a one-sheet spreadsheet, so both formats are read into
the same flat row grid (see _read_grid: csv.reader for .csv, openpyxl
for .xlsx, sheets concatenated) and handed to the same
csv_institutions/ parser. A single per-institution parser therefore
covers both formats - there is no separate .csv vs .xlsx parser.

This is a dispatcher: it reads just the real column-header row (any
preamble above it is skipped) and a handful of sample data rows to
identify which institution/format produced the export (most exports
don't print their own institution name anywhere in the file the way a
PDF statement does, so detection here works off column shape rather
than body text), then hands the full document off to that format's
parser under csv_institutions/. Bank/credit-card and brokerage exports
are dispatched through this one combined list rather than two separate
scripts, for the same reason
bank_statement.py combines its own institutions/ list - see that
script's module docstring for the shared rationale. To support a new
institution (bank or brokerage) or CSV export format, add a module
there exposing:

    detect(header: list[str], sample_rows: list[list[str]]) -> tuple[bool, str]
        # (matched, reason) - reason is a short diagnostic: on a match,
        # what was found; on a non-match, why (e.g. which column name
        # was missing), so a rejected export's output can say why nothing
        # recognized it. header is the real column-header row (any
        # preamble above it is skipped - see
        # parser_common.grid_header_and_rows); sample_rows is up to
        # _DETECT_SAMPLE_ROWS raw data rows (as
        # string lists, same column order as header) - present for
        # parsers that need to sniff cell shape (e.g. a date format) to
        # disambiguate from another institution using the same column
        # names.

and, depending on which kind of export the module parses (see
parser_common.KIND_BANK/KIND_BROKERAGE and parser_common.module_kind) -
KIND_BANK is the default a module need not opt into explicitly, since
every bundled CSV parser predates brokerage support:

    KIND_BANK (the default - checking/savings/credit-card exports):

        def parse(rows: list[dict[str, str]], csv_path: str) -> dict
            # rows is every data row as a header-name -> raw string
            # value dict (csv.DictReader shape). Must return a dict
            # with exactly these keys - institution, statementDate
            # (both str), transactions (list[dict]) - see
            # parser_common.validate_parse_result for the exact contract
            # (the same one bank_statement.py's KIND_BANK parsers use;
            # note account/accountType are NOT top-level here - a CSV
            # export can cover more than one account, so they're
            # required per transaction instead, same reasoning as
            # bank_statement.py's). No needsVisionOcr key - not
            # applicable to a CSV export, there are no check images to
            # OCR. csv_path is the original file, for the rare parser
            # that needs to re-read it directly (e.g. a multi-row
            # header, or a preamble before the real header row) rather
            # than work from the already-parsed rows dict.
            #
            # This dispatcher normalizes a KIND_BANK module's
            # "transactions" list into the same "tables" envelope a
            # KIND_BROKERAGE module returns directly (see below) before
            # printing - as tables={"cash_transactions": transactions} -
            # so callers see one unified output shape regardless of
            # which kind of module matched, exactly mirroring
            # bank_statement.py. A KIND_BANK module's own parse()/
            # validate contract is untouched by this.

    KIND_BROKERAGE (module sets `KIND = parser_common.KIND_BROKERAGE` at
    module scope - brokerage activity/holdings exports):

        def parse(rows: list[dict[str, str]], csv_path: str) -> dict
            # Must return a dict with exactly these keys - institution,
            # statementDate (both str), and tables (dict[str,
            # list[dict]]) - no account/accountType at this top level,
            # same reasoning as above; every row in every table carries
            # its own required account/accountType instead. Validated by
            # parser_common.validate_multi_table_parse_result - the
            # exact same "tables" contract bank_statement.py's own
            # KIND_BROKERAGE modules use (see that script's module
            # docstring for the full per-table schema). Most brokerage
            # CSV exports only ever populate one of
            # "brokerage_transactions"/"brokerage_holdings" per file
            # (unlike a PDF statement, which can print several sections
            # in one document) - see csv_institutions/
            # sample_brokerage_csv.py for a worked example.

and register it in _PARSERS below (checked in order; first match wins,
regardless of kind).

Alternatively, for a parser that shouldn't live in this repo (no code
change/recompile needed), drop a .py file exposing the same detect/parse
contract into <orbyDir>/ingest/parsers/ (created automatically by
PyRuntime.ensure) - it's loaded dynamically via --extra-parsers-dir and
tried after every bundled parser above that it doesn't override (see
parser_common.load_extra_parsers and parser_common.merge_parsers). This
is the exact same directory bank_statement.py loads its own extra
parsers from - a PDF-shaped detect()/parse() module dropped there is
simply never matched by this dispatcher (its detect() gets called with
the wrong number of arguments, which surfaces as an ordinary per-parser
"detect() raised: ..." rejection reason, same as any other non-match) -
see parser_common.py's module docstring for why that's safe and
requires no naming convention to keep the two kinds apart. It can
`from institutions import common` / `from institutions.common import
parse_amount, ...` the same way csv_institutions/ modules do, since this
script's own directory (containing both the institutions/ and
csv_institutions/ packages) is already on sys.path. This execution is
sandboxed - see CLAUDE.md's "Adding a parser without touching this
repo" for exactly what that means and its limitations (no network
access, no arbitrary filesystem access, no per-plugin dependency
installation).

--kind (bank or brokerage), used internally by pkg/ingest when a caller
forces DocTypeBank/DocTypeBrokerage, restricts the try list to modules
of just that kind before detection - a plain DocTypeAuto call (no
--kind) tries every bundled and dropped-in module regardless of kind.

--expected-parser <filename.py>, used internally by pkg/ingest when
pkg/project/financeparser.runExtractor runs Build Transactions
Extractor's Verify step, asserts that a specific parser (bundled or
dropped into extra_parsers_dir) is the one that must match - if
detect() instead picks a different module (or nothing at all), this
prints {"error": ...} and exits 1 rather than proceeding to that other
module's parse() (see parser_common.check_expected_parser and
bank_statement.py's own --expected-parser doc for the full rationale).
"""

import csv
import datetime
import json
import sys

import parser_common
import csv_institutions

# Auto-discovered from every csv_institutions/*.py exposing detect()/
# parse(), ordered by (PRIORITY, filename) - see
# parser_common.discover_parsers. Adding a parser is just adding a file
# under csv_institutions/; set a module-scope PRIORITY only if its
# detect() could shadow another's.
_PARSERS = parser_common.discover_parsers(csv_institutions)

# Number of leading data rows read (alongside the header) to identify
# the export format before committing to parsing the rest of the file.
_DETECT_SAMPLE_ROWS = 10


def _xlsx_cell(value) -> str:
    """openpyxl cell value -> string, the way a parser expects to see it:
    a real date/datetime cell becomes ISO YYYY-MM-DD (or 'YYYY-MM-DD
    HH:MM:SS' when it carries a time), everything else is str()'d, None
    is "".
    """
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def _read_grid(path: str) -> list[list[str]]:
    """Every row of the export as a list of string cells. A .csv is read
    with csv.reader; an .xlsx workbook is read with openpyxl, its sheets
    concatenated in order (a blank row between sheets) so a multi-sheet
    workbook still surfaces every sheet's rows - a parser that needs to
    keep sheets apart can re-read `path` itself.
    """
    if path.lower().endswith(".xlsx"):
        import openpyxl  # lazy: only .xlsx paths need it

        # Not read_only: a workbook written by something other than Excel
        # (excelize, the redactor, exporters) often stores a wrong or
        # missing sheet <dimension>, and read_only mode trusts it - which
        # silently truncates iter_rows to a fraction of the real data.
        wb = openpyxl.load_workbook(path, data_only=True)
        try:
            grid: list[list[str]] = []
            for si, ws in enumerate(wb.worksheets):
                if si:
                    grid.append([])
                for row in ws.iter_rows(values_only=True):
                    grid.append([_xlsx_cell(c) for c in row])
            return grid
        finally:
            wb.close()
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [list(row) for row in csv.reader(f)]


def _read_header_and_sample(path: str) -> tuple[list[str], list[list[str]]]:
    """Returns (header, sample_rows): the real column-header row (a
    preamble before it is skipped - see parser_common.grid_header_and_rows)
    and up to _DETECT_SAMPLE_ROWS following data rows, for detect() to
    sniff column shape from.
    """
    header, data = parser_common.grid_header_and_rows(_read_grid(path))
    return header, data[:_DETECT_SAMPLE_ROWS]


def _read_rows(path: str, header: list[str]) -> list[dict[str, str]]:
    """Returns every data row as a header-name -> string value dict, keyed
    by header (as identified by _read_header_and_sample) - so a parser
    sees the exact same header this dispatcher used for detect(), even
    when the file has a preamble or a trailing disclosures block.
    """
    _, data = parser_common.grid_header_and_rows(_read_grid(path))
    return [
        {header[i]: (row[i] if i < len(row) else "") for i in range(len(header))}
        for row in data
    ]


def _dump_rows(csv_path: str) -> None:
    """Prints the sniffed header and sample data rows as JSON, with no
    detection/parsing attempted. Used when authoring a new
    csv_institutions/ module, to see the exact input detect()/parse()
    will receive.
    """
    try:
        header, sample_rows = _read_header_and_sample(csv_path)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"failed to read the export: {e}"}))
        sys.exit(1)
    print(json.dumps({"header": header, "sampleRows": sample_rows}))


def _pop_flag_value(argv: list[str], flag: str) -> tuple[list[str], str | None]:
    """Removes flag and its following value from argv if present,
    returning (remaining_argv, value) - value is None if flag wasn't
    given.
    """
    if flag not in argv:
        return argv, None
    i = argv.index(flag)
    if i + 1 >= len(argv):
        return argv[:i], None
    return argv[:i] + argv[i + 2 :], argv[i + 1]


def _parse_matched(module, rows: list[dict[str, str]], csv_path: str) -> dict:
    """Calls module.parse(), validates the result against its kind's
    contract, and returns it normalized into the unified tables-dict
    envelope every match (bank or brokerage) is printed as - see this
    module's own docstring for why a KIND_BANK module's own parse()/
    validate contract is untouched by this normalization.
    """
    result = module.parse(rows, csv_path)
    if parser_common.module_kind(module) == parser_common.KIND_BROKERAGE:
        parser_common.validate_multi_table_parse_result(result, module.__name__)
        return result
    parser_common.validate_parse_result(result, module.__name__)
    result["tables"] = {"cash_transactions": result.pop("transactions")}
    return result


def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--dump-rows"]
    argv, extra_parsers_dir = _pop_flag_value(argv, "--extra-parsers-dir")
    argv, only_extra_parser = _pop_flag_value(argv, "--only-extra-parser")
    argv, kind_filter = _pop_flag_value(argv, "--kind")
    argv, expected_parser = _pop_flag_value(argv, "--expected-parser")

    if len(argv) < 1:
        print(json.dumps({"error": "usage: csv_statement.py <csv-or-xlsx-path> [--dump-rows]"}))
        sys.exit(1)
    csv_path = argv[0]

    if "--dump-rows" in sys.argv[1:]:
        _dump_rows(csv_path)
        return

    extra_parsers, extra_misses = parser_common.load_extra_parsers(extra_parsers_dir, "csv", only_extra_parser)
    parsers = extra_parsers if only_extra_parser else parser_common.merge_parsers(_PARSERS, extra_parsers)
    if kind_filter:
        parsers = [m for m in parsers if parser_common.module_kind(m) == kind_filter]

    try:
        header, sample_rows = _read_header_and_sample(csv_path)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"failed to read the export: {e}"}))
        sys.exit(1)

    module, reason = parser_common.detect(lambda m: m.detect(header, sample_rows), parsers)
    if msg := parser_common.check_expected_parser(module, reason, expected_parser):
        print(json.dumps({"error": msg}))
        sys.exit(1)
    if module is None:
        if extra_misses:
            reason = "; ".join([reason] + extra_misses) if reason else "; ".join(extra_misses)
        print(json.dumps({"detected": False, "reason": reason}))
        return

    try:
        rows = _read_rows(csv_path, header)
        result = _parse_matched(module, rows, csv_path)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"failed to parse the export: {e}"}))
        sys.exit(1)

    result["detected"] = True
    result.setdefault("needsVisionOcr", False)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
