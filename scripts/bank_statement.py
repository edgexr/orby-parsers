#!/usr/bin/env python3
"""Parse a bank or brokerage statement PDF into structured rows, printed
as JSON on stdout.

This is a dispatcher: it reads just enough of the PDF (its first few
pages' text) to identify which institution/format produced it, then
hands the full document off to that format's parser under institutions/.
Bank/credit-card and brokerage statements are dispatched through this
one combined list rather than two separate scripts/PDF reads, because
they share the exact same first-page-text detection shape and nothing
about a PDF's extension/content tells you up front which kind it is -
see CLAUDE.md's "Adding support for a new bank/brokerage statement
format" for the rationale. To support a new institution (bank or
brokerage) or a new statement format from an existing one, add a module
under institutions/ exposing:

    detect(head_text: str) -> tuple[bool, str]  # (matched, reason) -
        # reason is a short diagnostic: on a match, what was found; on a
        # non-match, why (e.g. which marker text was missing), so a
        # rejected statement's output can say why nothing recognized it.

and, depending on which kind of statement the module parses (see
parser_common.KIND_BANK/KIND_BROKERAGE and parser_common.module_kind) -
KIND_BANK is the default a module need not opt into explicitly, since
every bundled bank/credit-card parser predates brokerage support:

    KIND_BANK (the default - checking/savings/credit-card statements):

        def parse(pages_text: list[str], pdf_path: str, vision: dict | None) -> dict
            # Must return a dict with exactly these keys - institution,
            # statementDate (both str), transactions (list[dict]), and
            # optionally needsVisionOcr (bool, defaults to False). Note:
            # no account/accountType here - a statement can cover more
            # than one account (e.g. a combined checking+savings
            # statement), so those only ever live per transaction, never
            # at this top level. Each transactions[] entry must be a dict
            # with exactly: date (str, YYYY-MM-DD), description
            # (non-empty str), amount (number), balance (number or
            # null), account (str, the last 3-4 digits, or "" if
            # unknown), accountType (str, e.g. "Checking"/"Savings"/
            # "Credit Card", or "" if unknown), and optionally reference
            # (str, the issuer's own reference number, only printed by
            # some statement types) and institution (str - set only when
            # this transaction's institution differs from the
            # statement's primary one, e.g. a rare combined-institution
            # statement; most parsers omit this). No extra keys, and no
            # missing non-optional keys, are allowed at either level - see
            # parser_common.validate_parse_result, which enforces this on
            # every KIND_BANK module's output (bundled and external
            # alike) before it's accepted. needsVisionOcr: True if the
            # statement has check images that could be OCR'd for
            # payee/memo but vision was None this call - see
            # py/institutions/check_ocr.py and README_bankcheck_ocr.md.
            # pdf_path is the original file, for the rare parser that
            # needs to go back to the PDF itself (e.g. bofa_checking.py
            # OCRing embedded check images); vision, when not None, is
            # {"endpoint", "model", "api_key"} for an OpenAI-compatible
            # vision-capable chat endpoint check_ocr.py can call. Most
            # parsers just ignore both.
            #
            # This dispatcher normalizes a KIND_BANK module's
            # "transactions" list into the same "tables" envelope a
            # KIND_BROKERAGE module returns directly (see below) before
            # printing - as tables={"cash_transactions": transactions} -
            # so callers see one unified output shape regardless of
            # which kind of module matched. A KIND_BANK module's own
            # parse()/validate contract is untouched by this - it still
            # just returns "transactions", same as before brokerage
            # support existed.

    KIND_BROKERAGE (module sets `KIND = parser_common.KIND_BROKERAGE` at
    module scope - brokerage statements, which commonly have more than
    one section worth structured data):

        def parse(pages_text: list[str], pdf_path: str) -> dict
            # Must return a dict with exactly these keys - institution,
            # statementDate (both str), and tables (dict[str,
            # list[dict]]). No vision/check-image-OCR concept here
            # (unlike KIND_BANK) - a brokerage statement has no check
            # images. Note: no account/accountType at this top level
            # either, same reasoning as KIND_BANK above - a brokerage
            # statement just as commonly covers more than one account
            # (e.g. IRA + taxable in one document), so every row in
            # every table below carries its own required account/
            # accountType instead.
            #
            # `tables` lets one parse() call populate any combination of
            # a holdings/positions table, an activity (buy/sell/
            # dividend/transfer) table, and a linked cash/settlement-
            # account section shaped like a plain bank transaction, in a
            # single pass: its keys must be a subset of
            # "cash_transactions", "brokerage_transactions",
            # "brokerage_holdings" (each list optional/may be omitted or
            # empty - a pure positions statement only needs
            # "brokerage_holdings", for instance). Every row's keys are
            # validated against that table's own schema by
            # parser_common.validate_multi_table_parse_result, which
            # enforces this on every KIND_BROKERAGE module's output
            # (bundled and external alike) before it's accepted - see
            # that function's own docstring, and CLAUDE.md's "Adding
            # support for a new bank/brokerage statement format", for
            # the exact required/optional keys per table. See
            # institutions/sample_brokerage.py for a worked example.

and register it in _PARSERS below (checked in order; first match wins,
regardless of kind - a bank and a brokerage statement are distinguished
by their own header text, not by position in this list).

Alternatively, for a parser that shouldn't live in this repo (no code
change/recompile needed), drop a .py file exposing the same detect/parse
contract into <orbyDir>/ingest/parsers/ (created automatically by
PyRuntime.ensure) - it's loaded dynamically via --extra-parsers-dir and
tried after every bundled parser above that it doesn't override (see
parser_common.load_extra_parsers and parser_common.merge_parsers). To
*fix* an existing bundled parser rather than add a new one, name the
dropped file after the bundled module it should replace (e.g.
bofa_checking.py) - the copy in extra_parsers_dir then takes that
module's exact spot in the try order, in place of the bundled one,
instead of being tried only after every bundled parser has already
rejected the statement. A dropped file whose name doesn't match any
bundled module is treated as a new parser and appended after all
bundled ones, same as before. It can `from institutions import common`
/ `from institutions.common import parse_amount, ...` the same way
bundled modules do, since this script's own directory (containing the
institutions/ package) is already on sys.path. This execution is
sandboxed - see CLAUDE.md's "Adding a parser without touching this
repo" for exactly what that means and its limitations (no network
access, no arbitrary filesystem access, no per-plugin dependency
installation).

<orbyDir>/ingest/parsers/ is shared with csv_statement.py's own
--extra-parsers-dir - a CSV-shaped detect()/parse() module dropped there
is simply never matched by this dispatcher (its detect() gets called
with the wrong number of arguments, which surfaces as an ordinary
per-parser "detect() raised: ..." rejection reason, same as any other
non-match) - see parser_common.py's module docstring for why that's
safe and requires no naming convention to keep the two kinds apart.

--kind (bank or brokerage), used internally by pkg/ingest when a caller
forces DocTypeBank/DocTypeBrokerage, restricts the try list to modules
of just that kind before detection - a plain DocTypeAuto call (no
--kind) tries every bundled and dropped-in module regardless of kind,
which is the whole point of the combined dispatch this module docstring
describes.

--expected-parser <filename.py>, used internally by pkg/ingest when
pkg/project/financeparser.runExtractor runs Build Transactions
Extractor's Verify step, asserts that a specific parser (bundled or
dropped into extra_parsers_dir) is the one that must match - if
detect() instead picks a different module (or nothing at all), this
prints {"error": ...} and exits 1 rather than proceeding to that other
module's parse() (see parser_common.check_expected_parser). This is how
Verify tells "the drafted parser under test was shadowed by another,
already-installed parser" apart from "the drafted parser's own parse()
has a bug" - the latter only ever surfaces once the named parser is
confirmed to be the one that actually ran.
"""

import json
import sys

import pdfplumber

import parser_common
import institutions

# Auto-discovered from every institutions/*.py exposing detect()/parse(),
# ordered by (PRIORITY, filename) - see parser_common.discover_parsers.
# Adding a parser is just adding a file under institutions/; a module
# whose detect() could shadow another's sets a module-scope PRIORITY
# (e.g. bofa_checking_combined, whose header regex is a superset of
# bofa_checking's).
_PARSERS = parser_common.discover_parsers(institutions)

# Number of leading pages checked to identify the statement format before
# committing to extracting/parsing the rest of the document. Statements
# for unsupported formats bail out after only reading this many pages.
_DETECT_PAGE_COUNT = 3

# --- parse() IO contract enforcement, extra-parsers loading, and
# dispatch are shared with csv_statement.py - see parser_common.py. ---


def _dump_text(pdf_path: str) -> None:
    """Prints every page's raw pdfplumber-extracted text as JSON, with no
    detection/parsing attempted. Used when authoring a new institutions/
    module, to see the exact text layout detect()/parse() will receive.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"failed to read PDF: {e}"}))
        sys.exit(1)
    print(json.dumps({"pages": pages}))


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


def _parse_matched(module, pages_text: list[str], pdf_path: str, vision: dict | None) -> dict:
    """Calls module.parse() with whichever signature its kind expects,
    validates the result against that kind's contract, and returns it
    normalized into the unified tables-dict envelope every match (bank
    or brokerage) is printed as - see this module's own docstring for
    why a KIND_BANK module's own parse()/validate contract is untouched
    by this normalization.
    """
    kind = parser_common.module_kind(module)
    if kind == parser_common.KIND_BROKERAGE:
        result = module.parse(pages_text, pdf_path)
        parser_common.validate_multi_table_parse_result(result, module.__name__)
        return result
    result = module.parse(pages_text, pdf_path, vision)
    parser_common.validate_parse_result(result, module.__name__)
    result["tables"] = {"cash_transactions": result.pop("transactions")}
    return result


def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--dump-text"]
    argv, vision_endpoint = _pop_flag_value(argv, "--vision-endpoint")
    argv, vision_model = _pop_flag_value(argv, "--vision-model")
    argv, vision_api_key = _pop_flag_value(argv, "--vision-api-key")
    argv, extra_parsers_dir = _pop_flag_value(argv, "--extra-parsers-dir")
    argv, only_extra_parser = _pop_flag_value(argv, "--only-extra-parser")
    argv, kind_filter = _pop_flag_value(argv, "--kind")
    argv, expected_parser = _pop_flag_value(argv, "--expected-parser")
    vision = None
    if vision_endpoint and vision_model:
        vision = {"endpoint": vision_endpoint, "model": vision_model, "api_key": vision_api_key or ""}

    if len(argv) < 1:
        print(json.dumps({"error": "usage: bank_statement.py <pdf-path> [--dump-text]"}))
        sys.exit(1)
    pdf_path = argv[0]

    if "--dump-text" in sys.argv[1:]:
        _dump_text(pdf_path)
        return

    extra_parsers, extra_misses = parser_common.load_extra_parsers(extra_parsers_dir, "pdf", only_extra_parser)
    parsers = extra_parsers if only_extra_parser else parser_common.merge_parsers(_PARSERS, extra_parsers)
    if kind_filter:
        parsers = [m for m in parsers if parser_common.module_kind(m) == kind_filter]

    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Check only the first few pages before doing any real work:
            # fail early if this isn't a supported statement format.
            head_text = [p.extract_text() or "" for p in pdf.pages[:_DETECT_PAGE_COUNT]]
            joined_head_text = "\n".join(head_text)
            module, reason = parser_common.detect(lambda m: m.detect(joined_head_text), parsers)
            if msg := parser_common.check_expected_parser(module, reason, expected_parser):
                print(json.dumps({"error": msg}))
                sys.exit(1)
            if module is None:
                if extra_misses:
                    reason = "; ".join([reason] + extra_misses) if reason else "; ".join(extra_misses)
                print(json.dumps({"detected": False, "reason": reason}))
                return

            tail_text = [p.extract_text() or "" for p in pdf.pages[_DETECT_PAGE_COUNT:]]
            pages_text = [t for t in head_text + tail_text if t]
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"failed to read PDF: {e}"}))
        sys.exit(1)

    try:
        result = _parse_matched(module, pages_text, pdf_path, vision)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"failed to parse statement: {e}"}))
        sys.exit(1)

    result["detected"] = True
    result.setdefault("needsVisionOcr", False)
    _attach_shadow_warning(result, module, only_extra_parser)
    print(json.dumps(result))


def _attach_shadow_warning(result: dict, module, only_extra_parser) -> None:
    """Adds a "warnings" list to result when the matched parser is a
    local extra-parsers-dir copy shadowing an equivalent bundled parser
    (see parser_common.bundled_shadow_of) - a non-fatal heads-up the Go
    side surfaces on Build Transactions Extractor's Verify step, so a
    user editing a parser they've already contributed upstream isn't
    just told nothing matched / the wrong thing matched.
    """
    if only_extra_parser:
        return
    shadowed = parser_common.bundled_shadow_of(_PARSERS, module)
    if not shadowed:
        return
    result.setdefault("warnings", []).append(
        f"This parser is now also bundled with Orby as {shadowed} (it was "
        f"merged upstream). Your local copy in the parsers directory is "
        f"taking precedence while you keep editing it; remove it from "
        f"Manage Parsers once you're done to use the bundled version."
    )


if __name__ == "__main__":
    main()
