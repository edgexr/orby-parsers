"""Shared dispatcher machinery for py/bank_statement.py and
py/csv_statement.py: both are "read a small amount of the document,
ask an ordered list of per-institution modules which one recognizes it,
then hand the whole document to that module's parse()" dispatchers,
built on the exact same result/transaction dict contract (see
_validate_parse_result). The only thing that differs between them is
*how* detect()/parse() are called (bank_statement.py passes PDF text,
csv_statement.py passes spreadsheet rows) - not this shared machinery.

csv_statement.py handles both plain CSV and .xlsx workbooks: a .csv is
just a one-sheet spreadsheet, so both are flattened to the same row
grid and the same csv_institutions/ parser (detect(header, sample_rows)
/ parse(rows, path)) handles either - see looks_like_header_row /
grid_header_and_rows below for the header/footer detection that shared
path relies on.

Because bank_statement.py's --extra-parsers-dir and csv_statement.py's
--extra-parsers-dir point at the very same <orbyDir>/ingest/parsers
directory (see pyruntime.go and CLAUDE.md's "Adding a parser without
touching this repo"), a dropped .py file of either shape gets loaded
successfully by _load_extra_parsers under *both* dispatchers (it only
checks hasattr(module, "detect")/hasattr(module, "parse"), true for
either shape) - but _detect calling module.detect(...) with the wrong
number of positional arguments for that file's shape raises a plain
TypeError, which _detect already treats like any other non-match. So a
CSV-shaped module simply never matches under bank_statement.py (and
vice versa) without either dispatcher needing to know which shape a
given file is - see _detect's docstring.
"""

import glob
import importlib
import importlib.util
import os
import pkgutil
import re

# --- spreadsheet header/footer detection, shared by csv_statement.py's
# CSV *and* .xlsx paths. A real export often brackets its table with a
# preamble ("Custom report created on: ...") and/or a trailing
# disclosures block, so "the first non-empty row is the header" is wrong
# for those files - looks_like_header_row / grid_header_and_rows find the
# real table instead. Ported from looksLikeHeaderRow in
# pkg/ingest/csv_redact.go (kept behaviourally in sync). ---
_HEADER_DATE_RES = [
    re.compile(r"^\s*\d{4}-\d{1,2}-\d{1,2}\s*$"),
    re.compile(r"^\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s*$"),
    re.compile(r"^\s*\d{4}[/-]\d{1,2}[/-]\d{1,2}\s*$"),
]
_HEADER_NUMBER_RE = re.compile(r"^\s*[(+\-]?\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*[%)]?\s*$")


def _cell_is_date_or_number(cell: str) -> bool:
    c = cell.strip()
    if not c:
        return False
    if _HEADER_NUMBER_RE.match(c):
        return True
    return any(rx.match(c) for rx in _HEADER_DATE_RES)


def looks_like_header_row(row: list[str]) -> bool:
    """True when row reads like a table header: at least two non-empty
    cells, none of which parses as a bare date or number. Used to skip a
    preamble and land on the real column-header row.
    """
    non_empty = [c for c in row if c and c.strip()]
    if len(non_empty) < 2:
        return False
    return not any(_cell_is_date_or_number(c) for c in non_empty)


def grid_header_and_rows(grid: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Given a spreadsheet's full row grid, return (header, data_rows):
    the first row that looks_like_header_row (skipping any leading
    preamble), the rows after it, minus a trailing run of rows that have
    at most one non-empty cell (a disclosures / footer block). Blank rows
    anywhere are dropped.
    """
    rows = [r for r in grid if any(c and c.strip() for c in r)]
    header_idx = next((i for i, r in enumerate(rows) if looks_like_header_row(r)), None)
    if header_idx is None:
        return (rows[0] if rows else []), (rows[1:] if len(rows) > 1 else [])
    header = rows[header_idx]
    data = rows[header_idx + 1 :]
    while data and sum(1 for c in data[-1] if c and c.strip()) <= 1:
        data.pop()
    return header, data

# --- parse() IO contract, shared by both dispatchers - see each
# dispatcher's own module docstring for the human-readable version.
# account/accountType are NOT top-level result keys: a statement/export
# can cover more than one account (e.g. a combined checking+savings
# statement, or a multi-account CSV export), so they're only ever
# meaningful per transaction - see _REQUIRED_TXN_KEYS below. ---
_RESULT_KEYS = {"institution", "statementDate", "transactions", "needsVisionOcr"}
_REQUIRED_RESULT_KEYS = _RESULT_KEYS - {"needsVisionOcr"}
# account/accountType are required per transaction (every transaction
# belongs to some account, even if the parser couldn't determine its
# number/type - in which case use ""); reference is optional - the
# issuer's own transaction reference number, only printed by some
# statement/export types; institution is optional - set only when a
# transaction's institution differs from the statement's primary one
# (e.g. a combined multi-institution export); provider_account_id is
# optional - the fullest account identifier the source disclosed, for
# telling accounts apart when the trailing digits in "account" are not
# enough (several accounts at one institution can share them, and a
# redacted statement may leave none at all). Matches Transaction's json
# tags on the Go side (see statement.go).
_REQUIRED_TXN_KEYS = {"date", "description", "amount", "balance", "account", "accountType"}
_OPTIONAL_TXN_KEYS = {"reference", "institution", "provider_account_id"}
_TXN_KEYS = _REQUIRED_TXN_KEYS | _OPTIONAL_TXN_KEYS
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --- kind tagging, shared by bank_statement.py's combined institutions/
# list - see module_kind's docstring. ---
KIND_BANK = "bank"
KIND_BROKERAGE = "brokerage"


def module_kind(module) -> str:
    """Returns module's KIND attribute (KIND_BANK or KIND_BROKERAGE), or
    KIND_BANK if it doesn't define one - the large majority of
    institutions/ modules (every bundled bank/credit-card parser, and
    any externally-dropped plugin written before brokerage support
    existed) return the flat institution["transactions"] shape
    validate_parse_result checks, so that's the default a module need
    not opt into explicitly. A brokerage parser sets `KIND =
    parser_common.KIND_BROKERAGE` at module scope instead, and returns
    the "tables" shape validate_multi_table_parse_result checks - see
    bank_statement.py's module docstring for how the dispatcher uses
    this to decide which parse()/validate contract a matched module
    gets called with.
    """
    return getattr(module, "KIND", KIND_BANK)


# Default sort key for a discovered parser that doesn't set its own
# PRIORITY - see discover_parsers.
DEFAULT_PRIORITY = 100


def discover_parsers(package):
    """Imports every submodule of `package` (a parser package like
    `institutions` or `csv_institutions`) that exposes a detect()/parse()
    pair, and returns them as a dispatcher's ordered try-list - so adding
    a parser is just dropping a new file in that package's directory, with
    no _PARSERS list (and no import block) in the dispatcher to edit.

    Order (first match wins in `detect`) is `(PRIORITY, module name)`: a
    module may set a module-scope `PRIORITY` int (default
    DEFAULT_PRIORITY) to sort ahead of / behind its alphabetical
    neighbours when two modules' detect() could both match the same
    document and precedence matters (e.g. institutions/bofa_checking_combined,
    whose header regex is a superset of bofa_checking's). Most modules
    need no PRIORITY - alphabetical order is fine when detect()s are
    mutually exclusive.

    A submodule that doesn't define both detect() and parse() (helper
    modules like institutions/common.py, institutions/check_ocr.py) is
    skipped, as is a private `_`-prefixed one. An import error is NOT
    swallowed here - a bundled module that won't import is a build bug and
    should fail loudly (unlike an externally-dropped plugin, whose import
    errors load_extra_parsers folds into the detect diagnostic).
    """
    modules = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        if hasattr(module, "detect") and hasattr(module, "parse"):
            modules.append(module)
    modules.sort(key=lambda m: (getattr(m, "PRIORITY", DEFAULT_PRIORITY), m.__name__.rsplit(".", 1)[-1]))
    return modules


# --- tables-dict parse() contract, used by KIND_BROKERAGE modules (a
# brokerage statement commonly has more than one section worth
# structured data - see bank_statement.py's module docstring for the
# full picture). Table row schemas mirror pkg/ingest/statement.go's
# Transaction/BrokerageTransaction/BrokerageHolding JSON tags exactly -
# see CLAUDE.md's brokerage-statement section for the human-readable
# version of this contract. Like the
# KIND_BANK contract above, account/accountType are NOT top-level result
# keys here either - a brokerage statement/export just as commonly
# covers more than one account (e.g. IRA + taxable in one download), so
# they're required per row in every table instead - see
# _BROKERAGE_TXN_REQUIRED_KEYS/_BROKERAGE_HOLDING_REQUIRED_KEYS. ---
_MULTI_RESULT_KEYS = {"institution", "statementDate", "tables"}

_BROKERAGE_TXN_REQUIRED_KEYS = {"date", "description", "amount", "account", "accountType"}
_BROKERAGE_TXN_OPTIONAL_KEYS = {
    "action", "transaction_type", "subtype", "symbol", "security_id",
    "security_id_type", "quantity", "price", "commission_and_fees",
    "currency_code", "transaction_time", "status", "reference",
    "cancel_reference", "provider_account_id", "institution",
    # What a sale actually realized, which the statement prints per row
    # and nothing else in this schema records - the input to any
    # tax-year gain/loss question. Signed: a gain is positive, a loss
    # negative. realized_gain_term is "Short-term"/"Long-term"/"" for a
    # row that reports both.
    "realized_gain", "realized_gain_term",
    # The *other* security in a corporate action, as the issuer's own
    # identifier for it: on a merger's outgoing row the security the
    # position was exchanged for, on the incoming row the one it came
    # from. Nothing else in this schema can express "this holding became
    # that one", which is the only record of why a share count changed
    # with no trade behind it - see the securities table's
    # successor_symbol, which is built from these.
    "related_security_id",
}
_BROKERAGE_TXN_KEYS = _BROKERAGE_TXN_REQUIRED_KEYS | _BROKERAGE_TXN_OPTIONAL_KEYS

_BROKERAGE_HOLDING_REQUIRED_KEYS = {"symbol", "account", "accountType"}
_BROKERAGE_HOLDING_OPTIONAL_KEYS = {
    # What the position is expected to pay out over the next twelve
    # months, and that as a share of its value. Statements print both per
    # holding; without them a forward-income question has to be guessed
    # at from past dividends.
    "estimated_annual_income", "estimated_yield",
    "description", "quantity", "price", "current_value", "cost_basis_total",
    "average_cost_basis", "percent_of_account", "type",
    "subtype", "security_id", "security_id_type", "cusip", "isin", "sedol",
    "figi", "currency_code", "price_as_of", "price_time", "vested_quantity",
    "vested_value", "position_type", "market_identifier_code", "sector",
    "industry", "is_cash_equivalent", "tax_lots_json", "provider_account_id",
    "institution",
}
_BROKERAGE_HOLDING_KEYS = _BROKERAGE_HOLDING_REQUIRED_KEYS | _BROKERAGE_HOLDING_OPTIONAL_KEYS

# Per-table (required keys, all allowed keys, numeric keys, nullable
# numeric keys) - cash_transactions reuses the exact same schema
# bank_statement.py's own KIND_BANK parsers use for a "transactions"
# row, so a cash-shaped row means the same thing regardless of which
# kind of parser produced it.
_TABLE_SCHEMAS = {
    "cash_transactions": (_REQUIRED_TXN_KEYS, _TXN_KEYS, {"amount", "balance"}, {"balance"}),
    "brokerage_transactions": (
        _BROKERAGE_TXN_REQUIRED_KEYS, _BROKERAGE_TXN_KEYS,
        {"amount", "quantity", "price", "commission_and_fees", "realized_gain"},
        {"quantity", "price", "commission_and_fees", "realized_gain"},
    ),
    "brokerage_holdings": (
        _BROKERAGE_HOLDING_REQUIRED_KEYS, _BROKERAGE_HOLDING_KEYS,
        {"quantity", "price", "current_value", "cost_basis_total", "average_cost_basis", "percent_of_account", "vested_quantity", "vested_value", "estimated_annual_income", "estimated_yield"},
        {"quantity", "price", "current_value", "cost_basis_total", "average_cost_basis", "percent_of_account", "vested_quantity", "vested_value", "estimated_annual_income", "estimated_yield"},
    ),
}
_STRING_ROW_KEYS = {
    "action", "transaction_type", "subtype", "symbol", "description",
    "security_id", "security_id_type", "currency_code", "transaction_time",
    "status", "reference", "cancel_reference", "provider_account_id",
    "institution", "account", "accountType", "type", "cusip", "isin",
    "sedol", "figi", "price_as_of", "price_time", "position_type",
    "market_identifier_code", "sector", "industry", "tax_lots_json",
    "realized_gain_term", "related_security_id",
}
_BOOL_ROW_KEYS = {"is_cash_equivalent"}


def validate_parse_result(result: dict, module_name: str) -> None:
    """Raises ValueError with a specific, plugin-author-facing message if
    result doesn't exactly match the parse() contract documented at the
    top of bank_statement.py/csv_statement.py. Applied uniformly to
    every parser's output (bundled and externally-loaded, PDF and CSV
    alike) so a schema regression is caught here, at the one place all
    of them funnel through, rather than surfacing later as silently-
    wrong transaction data in the database (e.g. a typo'd "amout" key
    would otherwise silently become Amount: 0 for every transaction on
    the Go side).
    """
    if not isinstance(result, dict):
        raise ValueError(f"{module_name}.parse() must return a dict, got {type(result).__name__}")
    extra = set(result) - _RESULT_KEYS
    if extra:
        raise ValueError(f"{module_name}.parse() returned unexpected top-level key(s): {sorted(extra)}")
    missing = _REQUIRED_RESULT_KEYS - set(result)
    if missing:
        raise ValueError(f"{module_name}.parse() is missing required key(s): {sorted(missing)}")
    for key in ("institution", "statementDate"):
        if not isinstance(result[key], str):
            raise ValueError(f"{module_name}.parse(): {key!r} must be a str")
    if result["statementDate"] and not _DATE_RE.match(result["statementDate"]):
        raise ValueError(f"{module_name}.parse(): statementDate must be YYYY-MM-DD or '', got {result['statementDate']!r}")
    if "needsVisionOcr" in result and not isinstance(result["needsVisionOcr"], bool):
        raise ValueError(f"{module_name}.parse(): needsVisionOcr must be a bool")
    txns = result["transactions"]
    if not isinstance(txns, list):
        raise ValueError(f"{module_name}.parse(): 'transactions' must be a list")
    for i, t in enumerate(txns):
        if not isinstance(t, dict):
            raise ValueError(f"{module_name}.parse(): transactions[{i}] must be a dict")
        extra = set(t) - _TXN_KEYS
        if extra:
            raise ValueError(f"{module_name}.parse(): transactions[{i}] has unexpected key(s): {sorted(extra)}")
        missing = _REQUIRED_TXN_KEYS - set(t)
        if missing:
            raise ValueError(f"{module_name}.parse(): transactions[{i}] is missing key(s): {sorted(missing)}")
        if not isinstance(t["date"], str) or not _DATE_RE.match(t["date"]):
            raise ValueError(f"{module_name}.parse(): transactions[{i}]['date'] must be YYYY-MM-DD, got {t['date']!r}")
        if not isinstance(t["description"], str) or not t["description"].strip():
            raise ValueError(f"{module_name}.parse(): transactions[{i}]['description'] must be a non-empty str")
        if isinstance(t["amount"], bool) or not isinstance(t["amount"], (int, float)):
            raise ValueError(f"{module_name}.parse(): transactions[{i}]['amount'] must be a number")
        if t["balance"] is not None and (isinstance(t["balance"], bool) or not isinstance(t["balance"], (int, float))):
            raise ValueError(f"{module_name}.parse(): transactions[{i}]['balance'] must be a number or null")
        for key in ("reference", "institution", "account", "accountType"):
            if key in t and not isinstance(t[key], str):
                raise ValueError(f"{module_name}.parse(): transactions[{i}][{key!r}] must be a str")


def validate_multi_table_parse_result(result: dict, module_name: str) -> None:
    """The KIND_BROKERAGE sibling of validate_parse_result: raises
    ValueError with a specific, plugin-author-facing message if result
    doesn't exactly match the tables-dict parse() contract documented at
    the top of bank_statement.py and in _TABLE_SCHEMAS above. Applied to
    every KIND_BROKERAGE module's output (bundled and externally-loaded
    alike) before bank_statement.py normalizes/prints it, for the same
    reason validate_parse_result exists for KIND_BANK modules - catching
    a schema regression here rather than letting silently-wrong data
    reach the database.
    """
    if not isinstance(result, dict):
        raise ValueError(f"{module_name}.parse() must return a dict, got {type(result).__name__}")
    extra = set(result) - _MULTI_RESULT_KEYS
    if extra:
        raise ValueError(f"{module_name}.parse() returned unexpected top-level key(s): {sorted(extra)}")
    missing = _MULTI_RESULT_KEYS - set(result)
    if missing:
        raise ValueError(f"{module_name}.parse() is missing required key(s): {sorted(missing)}")
    for key in ("institution", "statementDate"):
        if not isinstance(result[key], str):
            raise ValueError(f"{module_name}.parse(): {key!r} must be a str")
    if result["statementDate"] and not _DATE_RE.match(result["statementDate"]):
        raise ValueError(f"{module_name}.parse(): statementDate must be YYYY-MM-DD or '', got {result['statementDate']!r}")
    tables = result["tables"]
    if not isinstance(tables, dict):
        raise ValueError(f"{module_name}.parse(): 'tables' must be a dict")
    extra_tables = set(tables) - set(_TABLE_SCHEMAS)
    if extra_tables:
        raise ValueError(f"{module_name}.parse(): unrecognized table name(s) in 'tables': {sorted(extra_tables)}")
    for table_name, rows in tables.items():
        required_keys, all_keys, numeric_keys, nullable_numeric_keys = _TABLE_SCHEMAS[table_name]
        if not isinstance(rows, list):
            raise ValueError(f"{module_name}.parse(): tables[{table_name!r}] must be a list")
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}] must be a dict")
            extra_keys = set(row) - all_keys
            if extra_keys:
                raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}] has unexpected key(s): {sorted(extra_keys)}")
            missing_keys = required_keys - set(row)
            if missing_keys:
                raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}] is missing key(s): {sorted(missing_keys)}")
            if "date" in row and (not isinstance(row["date"], str) or not _DATE_RE.match(row["date"])):
                raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}]['date'] must be YYYY-MM-DD, got {row['date']!r}")
            if "description" in required_keys and (not isinstance(row["description"], str) or not row["description"].strip()):
                raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}]['description'] must be a non-empty str")
            if "symbol" in required_keys and (not isinstance(row["symbol"], str) or not row["symbol"].strip()):
                raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}]['symbol'] must be a non-empty str")
            for key in numeric_keys:
                if key not in row:
                    continue
                val = row[key]
                if val is None:
                    if key in nullable_numeric_keys:
                        continue
                    raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}][{key!r}] must be a number")
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    suffix = " or null" if key in nullable_numeric_keys else ""
                    raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}][{key!r}] must be a number{suffix}")
            for key in _STRING_ROW_KEYS:
                if key in row and not isinstance(row[key], str):
                    raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}][{key!r}] must be a str")
            for key in _BOOL_ROW_KEYS:
                if key in row and not isinstance(row[key], bool):
                    raise ValueError(f"{module_name}.parse(): tables[{table_name!r}][{i}][{key!r}] must be a bool")


def load_extra_parsers(extra_parsers_dir: str | None, loader_tag: str, only_name: str | None = None):
    """Dynamically loads *.py files in extra_parsers_dir (sorted for
    determinism), or only only_name when provided, as dispatcher-style
    parser modules, so a user can add
    support for a new statement/export format by dropping a file in
    there - no code change or recompile needed (see bank_statement.py/
    csv_statement.py's module docstrings and CLAUDE.md). Each file must
    expose a detect()/parse() pair (of whichever shape the calling
    dispatcher expects - see this module's own docstring for how a
    mismatched shape degrades harmlessly); one that doesn't, or that
    fails to import, is skipped rather than crashing dispatch for every
    other statement/export - its failure is folded into the same
    per-parser "reason" diagnostic detect() already produces for an
    ordinary non-match, so a bad plugin file is visible in the same
    place a legitimate rejection reason would be, not a silent no-op or
    an opaque crash.

    loader_tag namespaces the synthetic module name (e.g. "pdf" or
    "csv") so bank_statement.py and csv_statement.py loading the same
    directory in the same process (as tests do) never collide in
    sys.modules.

    A loaded module can do `from institutions import common` (or
    `from institutions.common import parse_amount, ...`) exactly like a
    bundled module: the dispatcher script's own directory (which
    contains the institutions/ package alongside it - see pyruntime.go's
    writeScripts) is already on sys.path[0] as the running script's
    directory.
    """
    modules = []
    misses = []
    if not extra_parsers_dir:
        return modules, misses
    if only_name:
        if os.path.basename(only_name) != only_name or not only_name.endswith(".py"):
            return modules, [f"{only_name}: --only-extra-parser must be a .py filename, not a path"]
        paths = [os.path.join(extra_parsers_dir, only_name)]
        if not os.path.isfile(paths[0]):
            return modules, [f"{only_name}: not found in extra parsers directory"]
    else:
        paths = sorted(glob.glob(os.path.join(extra_parsers_dir, "*.py")))
    for path in paths:
        name = os.path.basename(path)
        if name == "__init__.py":
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"_extra_parser_{loader_tag}_{name[:-3]}", path)
            if spec is None or spec.loader is None:
                raise ImportError("could not create module spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not (hasattr(module, "detect") and hasattr(module, "parse")):
                raise AttributeError("module must define detect(...) and parse(...)")
        except Exception as e:  # noqa: BLE001
            misses.append(f"{name}: failed to load: {e}")
            continue
        module.__name__ = name[:-3]  # so _detect's misses list reports the filename (minus .py), not the synthetic loader name
        modules.append(module)
    return modules, misses


def merge_parsers(bundled: list, extra: list) -> list:
    """Combines bundled (a dispatcher's own _PARSERS) with extra (from
    load_extra_parsers) into the final try-in-order list: an extra
    module whose filename (module.__name__, already stripped of .py by
    load_extra_parsers) matches a bundled module's own name replaces
    that bundled module in place, keeping its original position in the
    try order - this is how a user fixes a bug in a bundled parser
    (e.g. bofa_checking.py) without a code change/recompile, by
    dropping a same-named, edited copy into extra_parsers_dir (see
    bank_statement.py's module docstring). An extra module with no
    matching bundled name is a new parser, appended after every bundled
    one, same as before this override behavior existed.
    """
    merged = list(bundled)
    unmatched = []
    for module in extra:
        for i, bundled_module in enumerate(merged):
            bundled_name = bundled_module.__name__.rsplit(".", 1)[-1]
            if bundled_name == module.__name__:
                merged[i] = module
                break
        else:
            unmatched.append(module)
    return merged + unmatched


def check_expected_parser(module, reason: str, expected_parser: str | None) -> str | None:
    """Returns a plugin-author-facing error message if expected_parser is
    set and module (the result of a dispatcher's own detect(), None if
    nothing matched) isn't the one it names, else None. Used by
    bank_statement.py/csv_statement.py's --expected-parser flag - see
    each dispatcher's module docstring - which lets a caller (Build
    Transactions Extractor's Verify step, pkg/project/financeparser.
    runExtractor) assert that a specific drafted parser is the one that
    actually recognizes its sample file, rather than some other,
    already-installed parser claiming it first and running its parse()
    instead - possibly crashing on data it doesn't expect - with nothing
    telling the caller the intended parser was never reached. reason is
    whatever detect() already produced (its own per-parser miss
    diagnostics when module is None), folded into the message so a
    "nothing matched" case is still actionable.
    """
    if not expected_parser:
        return None
    expected_name = expected_parser[:-3] if expected_parser.endswith(".py") else expected_parser
    if module is not None:
        matched_name = module.__name__.rsplit(".", 1)[-1]
        if matched_name == expected_name:
            return None
        return f"expected parser {expected_parser} to match, but {matched_name}.py matched first instead"
    if reason:
        return f"expected parser {expected_parser} to match, but nothing matched instead: {reason}"
    return f"expected parser {expected_parser} to match, but nothing matched instead"


def detect(call_detect, parsers):
    """Returns (module, reason) for the first parser in parsers whose
    detect() (invoked via call_detect(module), a small closure the
    caller supplies - e.g. `lambda m: m.detect(head_text)` for
    bank_statement.py, `lambda m: m.detect(header, sample_rows)` for
    csv_statement.py) reports a match, or (None, reason) where reason
    explains why every parser rejected the input - one line per parser
    tried, e.g. "bofa_checking: 'bank of america' not found;
    bofa_credit_card: 'bank of america' not found". A parser whose
    detect() raises (including a TypeError from being called with the
    wrong shape's arguments - see this module's own docstring), or
    doesn't return (bool, str), is treated the same as an ordinary
    non-match rather than crashing dispatch for every other parser -
    this applies to bundled and externally-loaded parsers alike, and is
    exactly how a CSV-shaped parser module is silently skipped when
    tried under bank_statement.py, and vice versa.
    """
    misses = []
    for module in parsers:
        try:
            result = call_detect(module)
        except Exception as e:  # noqa: BLE001
            misses.append(f"{module.__name__}: detect() raised: {e}")
            continue
        if not (isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool) and isinstance(result[1], str)):
            misses.append(f"{module.__name__}: detect() must return (bool, str), got {result!r}")
            continue
        matched, reason = result
        if matched:
            return module, reason
        misses.append(f"{module.__name__.rsplit('.', 1)[-1]}: {reason}")
    return None, "; ".join(misses)
