"""Bank of America modern "combined statement" parser - a newer
(2018+-era) combined-statement layout bundling multiple deposit accounts
(e.g. checking + savings) into one PDF. Each account's own block uses
the *exact* same per-account section layout bofa_checking.py already
parses ("Account number: XXXX", an "Account summary" block with a
"Beginning balance on <Month D, YYYY>" line, "Deposits and other
additions"/"Withdrawals and other subtractions"/"Service fees" sections,
MM/DD/YY transaction dates) - it's just wrapped in an outer "Your
combined statement\nfor <Month D, YYYY> to <Month D, YYYY>" header
instead of a single "Your <Product>\nfor ... to ..." header, and the
document contains more than one "Account number:" block.

This must be registered ahead of bofa_checking.py in bank_statement.py's
_PARSERS list: without that ordering, bofa_checking's own generic "Your
... for DATE to DATE" header regex also matches this file ("combined
statement" is, syntactically, a valid if nonsensical capture for its
product-name group), and it would then parse every account's
transactions as if they belonged to one account - the bug this module
fixes. Rather than duplicate bofa_checking's section-parsing logic, this
module reuses its private helpers directly (same package) on each
account's own slice of lines.

This is unrelated to bofa_combined_statement.py, an older (circa-2012)
combined-statement layout with a materially different textual structure
(MM-DD dates, a "Statement Period X-X-XX through Y-Y-YY" header, "Account
Number XXXX" with no colon, and a "Daily Balance Summary" per-account
footer) - see that module's docstring.
"""

import re
from datetime import datetime

import pdfplumber

from . import bofa_checking, check_ocr, common

# Must be tried before bofa_checking: bofa_checking's generic "Your ...
# for DATE to DATE" header regex also matches this modern combined
# statement's "Your combined statement for DATE to DATE" header (it would
# then treat every account's transactions as belonging to one account).
# Alphabetical order puts bofa_checking first, so sort ahead of it here.
PRIORITY = 90

_HEADER_RE = re.compile(
    r"Your combined statement\s*\n\s*for\s+(\w+ \d{1,2},? \d{4})\s+to\s+(\w+ \d{1,2},? \d{4})",
    re.IGNORECASE,
)

# An account's own block starts at a line reading exactly "Account
# number: XXXX XXXX NNNN", with the product name ("Your <Product Name>")
# on one of the next few lines.
_ACCOUNT_NUMBER_LINE_RE = re.compile(r"^Account number:\s*([\dXx*\s]{2,25})\s*$", re.IGNORECASE)
_PRODUCT_NAME_LINE_RE = re.compile(r"^Your\s+(.+)$", re.IGNORECASE)


def detect(head_text: str) -> tuple[bool, str]:
    if "bank of america" not in head_text.lower():
        return False, "'bank of america' not found"
    if not _HEADER_RE.search(head_text):
        return False, "'bank of america' found, but no 'Your combined statement for DATE to DATE' header"
    return True, "matched Bank of America modern combined-statement header"


def _classify_account_type(product_name: str) -> str:
    lower = product_name.lower()
    if "checking" in lower or "chkg" in lower:
        return "Checking"
    if "savings" in lower or "svgs" in lower:
        return "Savings"
    return product_name.strip()


def _statement_date(combined_text: str) -> str:
    m = _HEADER_RE.search(combined_text)
    if not m:
        return ""
    try:
        return datetime.strptime(m.group(2), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse(pages_text: list[str], pdf_path: str, vision: dict | None = None) -> dict:
    combined = "\n".join(pages_text)
    statement_date = _statement_date(combined)

    all_lines = []
    for page in pages_text:
        all_lines.extend(page.split("\n"))

    starts = [
        (i, m.group(1)) for i, line in enumerate(all_lines) for m in [_ACCOUNT_NUMBER_LINE_RE.match(line.strip())] if m
    ]

    accounts = []
    for idx, (start_idx, raw_account) in enumerate(starts):
        end_idx = starts[idx + 1][0] if idx + 1 < len(starts) else len(all_lines)
        block_lines = all_lines[start_idx:end_idx]

        product_name = ""
        for line in block_lines[1:4]:
            pm = _PRODUCT_NAME_LINE_RE.match(line.strip())
            if pm:
                product_name = pm.group(1).strip()
                break
        account_number = common.last4_digits(raw_account)
        account_type = _classify_account_type(product_name)

        block_text = "\n".join(block_lines)
        txns = bofa_checking._parse_transactions([block_text])
        common.apply_running_balance(txns, bofa_checking._extract_starting_balance(block_text))
        for t in txns:
            t["account"] = account_number
            t["accountType"] = account_type
        accounts.append({"account": account_number, "accountType": account_type, "transactions": txns})

    all_transactions = [t for acct in accounts for t in acct["transactions"]]

    # Check images (payee/memo) - identical to bofa_checking.py's own
    # handling (see check_ocr.py); a combined statement's per-account
    # blocks share one document-wide "Check images" section, so this
    # matches against all_transactions rather than per-account, the same
    # way check_ocr.enrich_checks already matches purely by check number.
    needs_vision_ocr = False
    if pdf_path:
        with pdfplumber.open(pdf_path) as pdf:
            if check_ocr.find_check_image_pages(pdf):
                if vision:
                    check_ocr.enrich_checks(all_transactions, pdf, vision)
                else:
                    needs_vision_ocr = True

    return {
        "institution": "Bank of America",
        "statementDate": statement_date,
        "transactions": all_transactions,
        "needsVisionOcr": needs_vision_ocr,
    }
