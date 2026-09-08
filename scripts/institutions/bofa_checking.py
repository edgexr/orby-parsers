"""Bank of America checking/savings statement parser.

Format: monthly checking/savings statements with "Deposits and other
additions" / "Withdrawals and other subtractions" / "Service fees" /
"Checks" sections, and transaction dates as MM/DD/YY.
"""

import re
from datetime import datetime

import pdfplumber

from . import check_ocr, common

_SECTIONS = {
    "deposits and other additions": "credit",
    "withdrawals and other subtractions": "debit",
    "service fees": "debit",
}

# Matches "Account number: 1234 5678 9012", "Account # 1234 5678 9012",
# "Account #: 9041", etc. The captured run can include spaces (some
# statements print the number in space-separated groups), so the caller
# strips non-digits and keeps the last 4 rather than slicing this
# capture directly.
_ACCOUNT_NUMBER_RE = re.compile(
    r"account\s*(?:number|#)\s*:?\s*\n*\s*([\dXx*\s]{2,25})", re.IGNORECASE
)

# Matches e.g. "Beginning balance on April 14, 2016 $1,607.04". No
# per-transaction balance column is printed, so this is the seed for a
# running balance we compute ourselves (see common.apply_running_balance).
_BEGINNING_BALANCE_RE = re.compile(
    r"Beginning balance on\s+\w+ \d{1,2},\s*\d{4}\s+\$?(-?[\d,]+\.\d{2})", re.IGNORECASE
)

# Matches the statement header block, e.g.:
#   Your BofA Core Checking
#   for April 14, 2016 to May 12, 2016 Account number: 9041
# Up to a few lines (e.g. a Preferred Rewards tier line) can appear
# between the account name and the "for ... to ..." period line, so
# that gap is matched non-greedily rather than assuming they're
# adjacent.
_STATEMENT_HEADER_RE = re.compile(
    r"^Your\s+(.+?)\s*\n(?:.*\n){0,3}?\s*for\s+(\w+ \d{1,2}, \d{4})\s+to\s+(\w+ \d{1,2}, \d{4})",
    re.IGNORECASE | re.MULTILINE,
)


def detect(head_text: str) -> tuple[bool, str]:
    if "bank of america" not in head_text.lower():
        return False, "'bank of america' not found"
    if not _STATEMENT_HEADER_RE.search(head_text):
        return False, "'bank of america' found, but no 'Your ... for DATE to DATE' checking/savings header"
    return True, "matched Bank of America checking/savings header"


def _extract_account_number(combined_text: str) -> str:
    m = _ACCOUNT_NUMBER_RE.search(combined_text)
    if not m:
        return ""
    return common.last4_digits(m.group(1))


def _extract_statement_meta(combined_text: str) -> tuple[str, str]:
    """Returns (account_type, statement_date) parsed from the statement's
    header block, e.g. ("Checking", "2016-05-12"). statement_date is the
    period's closing date. Either may be "" if the header isn't found or
    doesn't match the expected format.
    """
    m = _STATEMENT_HEADER_RE.search(combined_text)
    if not m:
        return "", ""

    raw_type = m.group(1).strip()
    lower = raw_type.lower()
    if "checking" in lower or "chkg" in lower:
        account_type = "Checking"
    elif "savings" in lower or "svgs" in lower:
        account_type = "Savings"
    else:
        account_type = raw_type

    try:
        statement_date = datetime.strptime(m.group(3), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        statement_date = ""

    return account_type, statement_date


def _expand_year_yy(date_yy: str) -> str:
    """Convert MM/DD/YY to YYYY-MM-DD."""
    mm, dd, yy = date_yy.split("/")
    return f"20{yy}-{mm}-{dd}"


def _parse_transactions(pages_text: list[str]) -> list[dict]:
    transactions = []
    checks = []
    section_type = None  # "credit", "debit", or "check"
    pending_txn = None

    txn_re = re.compile(r"^(\d{2}/\d{2}/\d{2})\s+" r"(.+?)\s+" r"(-?[\d,]+\.\d{2})\s*$")
    check_entry_re = re.compile(r"(\d{2}/\d{2}/\d{2})\s+(\d+\*?)\s+(-[\d,]+\.\d{2})")

    def flush(txn):
        if txn:
            transactions.append(txn)

    for text in pages_text:
        for line in text.split("\n"):
            s = line.strip()

            if re.match(r"^(Page \d+ of \d+|PULL:|This page intentionally)", s):
                continue
            if re.match(r"^MARILYN .* Account #", s):
                continue

            s_lower = s.lower()
            s_lower_base = re.sub(r"\s*-\s*continued.*$", "", s_lower).strip()

            matched_section = None
            for sec, stype in _SECTIONS.items():
                if s_lower_base == sec or s_lower_base.startswith(sec):
                    matched_section = stype
                    break

            if matched_section:
                flush(pending_txn)
                pending_txn = None
                section_type = matched_section
                continue

            if re.match(r"^Checks?\b", s, re.IGNORECASE) and not re.match(
                r"^Checks?\s+-\s+continued", s, re.IGNORECASE
            ):
                flush(pending_txn)
                pending_txn = None
                if re.match(r"^Checks?\s*$", s, re.IGNORECASE):
                    section_type = "check"
                elif re.match(r"^Total checks", s, re.IGNORECASE):
                    section_type = None
                continue

            if re.match(
                r"^(Total deposits|Total withdrawals|Total other subtractions|Total service fees|Total #|Note your|To help you|Service fees)",
                s,
                re.IGNORECASE,
            ):
                flush(pending_txn)
                pending_txn = None
                if re.match(r"^Service fees", s, re.IGNORECASE):
                    section_type = "debit"
                else:
                    section_type = None
                continue

            if section_type is None:
                continue

            if re.match(
                r"^Date\s+(Description|Check #|Transaction description)\s+Amount", s
            ):
                continue

            if section_type == "check":
                for m in check_entry_re.finditer(s):
                    date = _expand_year_yy(m.group(1))
                    num = m.group(2).rstrip("*")
                    amount = common.parse_amount(m.group(3).lstrip("-"))
                    checks.append(
                        {
                            "date": date,
                            "description": f"CHECK #{num}",
                            # Checks are always withdrawals.
                            "amount": -amount,
                            "balance": None,
                        }
                    )
                continue

            m = txn_re.match(s)
            if m:
                flush(pending_txn)
                date = _expand_year_yy(m.group(1))
                description = m.group(2).strip()
                amount = common.parse_amount(m.group(3).lstrip("-"))
                # A single signed amount column: debits (withdrawals/fees)
                # are negative, credits (deposits) are positive.
                signed_amount = amount if section_type == "credit" else -amount
                pending_txn = {
                    "date": date,
                    "description": description,
                    "amount": signed_amount,
                    "balance": None,
                }
            elif pending_txn and s and not re.match(r"^\d{2}/\d{2}/\d{2}\b", s):
                if re.match(
                    r"^(continued on|Total |Page |\* There|Your checking|Your savings)",
                    s,
                    re.IGNORECASE,
                ):
                    continue
                pending_txn["description"] += " " + s
            else:
                flush(pending_txn)
                pending_txn = None

    flush(pending_txn)

    all_txns = transactions + checks
    all_txns.sort(key=lambda t: (t["date"], t["description"]))
    return all_txns


def _extract_starting_balance(combined_text: str) -> float | None:
    m = _BEGINNING_BALANCE_RE.search(combined_text)
    if not m:
        return None
    return common.parse_amount(m.group(1))


def parse(pages_text: list[str], pdf_path: str, vision: dict | None = None) -> dict:
    combined = "\n".join(pages_text)
    account_type, statement_date = _extract_statement_meta(combined)
    account = _extract_account_number(combined)
    transactions = _parse_transactions(pages_text)
    common.apply_running_balance(transactions, _extract_starting_balance(combined))
    common.tag_account(transactions, account, account_type)

    # Check images (payee/memo) are the one thing this parser needs the
    # actual PDF object for - everything else above works from pages_text
    # alone. See check_ocr.py/README_bankcheck_ocr.md.
    needs_vision_ocr = False
    if pdf_path:
        with pdfplumber.open(pdf_path) as pdf:
            if check_ocr.find_check_image_pages(pdf):
                if vision:
                    check_ocr.enrich_checks(transactions, pdf, vision)
                else:
                    needs_vision_ocr = True

    return {
        "institution": "Bank of America",
        "statementDate": statement_date,
        "transactions": transactions,
        "needsVisionOcr": needs_vision_ocr,
    }
