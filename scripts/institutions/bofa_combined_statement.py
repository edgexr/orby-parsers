"""Bank of America legacy "Combined Statement" parser.

Format: an older (circa-2012-era) monthly statement style with a
"Statement Period MM-DD-YY through MM-DD-YY" header, "Account Number
XXXX XXXX NNNN" (no colon) per account, a "Daily Balance Summary" footer
per account, and "Additions"/"Subtractions" transaction sections. Dates
are printed as MM-DD-YY in the header period line and MM-DD (no year) on
transaction lines. This is a materially different layout from the modern
single-account format bofa_checking.py handles ("Your <Product> for
<Month D, YYYY> to <Month D, YYYY>"), which is why it's a separate
module. Despite the module name, this format isn't exclusive to
statements bundling more than one account - BofA issued single-account
statements in this same legacy layout too (e.g. a lone savings account),
and parse() below handles that correctly as a one-account-long version of
the same per-account-block loop.

Since this format covers more than one account per statement, every
transaction dict here carries its own "account"/"accountType" set
directly from whichever account block it came from - required anyway by
parser_common's IO contract (account/accountType are per-transaction
keys, never top-level - see Transaction in pkg/ingest/statement.go).
"""

import re
from datetime import datetime

from . import common

# Matched separately from _DATE_RANGE_RE (rather than one combined regex
# requiring the date range to immediately follow this label with only
# non-digit characters in between) because the front page's address
# column can land text containing digits - e.g. a zip code like
# "33622-5118" - between "Statement Period" and the actual date range in
# the extracted text, when pdfplumber's column layout merges them onto
# the same line. A single regex requiring \D* between the label and the
# date range fails to match in that case even though both are clearly
# present in the document.
_PERIOD_LABEL_RE = re.compile(r"Statement Period", re.IGNORECASE)
_DATE_RANGE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{2})\s+through\s+(\d{2})-(\d{2})-(\d{2})")

# Rows in the front-page "Statement Summary" table, e.g.:
#   Adv Tiered Interest Chkg 0000 0000 0000 12-06 84,653.73
#   Regular Savings 0000 0000 0000 12-06 124,704.89
# Used to map each account's last-4 to its product name, so later
# per-account blocks (which only print "Account Number XXXX XXXX 7777",
# not the product name) can be classified Checking/Savings/other.
_SUMMARY_ROW_RE = re.compile(
    r"^(.+?)\s+([\dXx*][\dXx*\s]{3,20}[\dXx*])\s+\d{2}-\d{2}\s+-?[\d,]+\.\d{2}\s*$"
)

# An account's own "<name> Additions"/"<name> Subtractions" grouping
# header, e.g. "Adv Tiered Interest Chkg Additions" or "Regular Savings
# Subtractions" - found within that account's own block (see
# _in_block_product_name), so this is the *primary* way parse()
# classifies each account, not the front-page summary table above (kept
# only as a fallback for an account with neither section present this
# statement, e.g. zero activity).
_SECTION_GROUP_RE = re.compile(r"^(.+?)\s+(?:Additions|Subtractions)\s*$", re.IGNORECASE)

_ACCOUNT_NUMBER_RE = re.compile(r"^Account Number\s+([\dXx*\s]{2,25})$", re.IGNORECASE)
_BEGINNING_BALANCE_RE = re.compile(r"Beginning Balance on\s+\d{2}-\d{2}-\d{2}\s+\$?\s*(-?[\d,]+\.\d{2})", re.IGNORECASE)
_DAILY_BALANCE_SUMMARY_RE = re.compile(r"^Daily Balance Summary\s*$", re.IGNORECASE)

# Column-header lines that start each transaction subsection - these are
# product-name-independent (unlike the outer "<Product> Additions"/
# "<Product> Subtractions" grouping headers), so they're what this parser
# actually keys off to know what kind of rows follow.
_SECTION_STARTS = {
    "deposits and other additions date posted amount($)": "credit",
    "check # posting date amount($)": "check",
    "atm and debit card subtractions date posted amount($)": "debit",
    "other subtractions date posted amount($)": "debit",
}

_TOTAL_LINE_RE = re.compile(r"^Total\b", re.IGNORECASE)
_TXN_RE = re.compile(r"^(.+?)\s+(\d{2})-(\d{2})\s+(-?[\d,]+\.\d{2})\s*$")
_CHECK_RE = re.compile(r"^(\d+\*?)\s+(\d{2})-(\d{2})\s+(-?[\d,]+\.\d{2})\s*$")

# Page furniture that can appear between sections (running headers,
# reprinted period/account boilerplate, OCR-garbled MICR/scanline noise)
# - never a transaction line, and must not be absorbed as a continuation
# of a pending transaction's description if a page break happens to land
# mid-section. Every alternative is anchored to the *whole* line (note
# the trailing $): without it, the lone "H" alternative (for the
# standalone "H" header line) would match the start of any line merely
# beginning with "H" - e.g. a real "Hoa Online Pay ..." transaction line
# - and silently swallow it as noise instead.
_NOISE_RE = re.compile(
    r"^(?:H|Combined Statement|Statement Period|Number of checks enclosed:.*|"
    r"B \d\d .*|P a g e .*|Page \d+ of \d+.*|\d[\d\s]{40,}.*)$",
    re.IGNORECASE,
)


def detect(head_text: str) -> tuple[bool, str]:
    if "bank of america" not in head_text.lower():
        return False, "'bank of america' not found"
    if not _PERIOD_LABEL_RE.search(head_text) or not _DATE_RANGE_RE.search(head_text):
        return False, "'bank of america' found, but no 'Statement Period ... through ...' combined-statement header"
    return True, "matched Bank of America legacy combined-statement header"


_SAVINGS_WORD_RE = re.compile(r"\bsav\b", re.IGNORECASE)


def _classify_account_type(product_name: str) -> str:
    lower = product_name.lower()
    if "chkg" in lower or "checking" in lower:
        return "Checking"
    # "svgs"/"savings" cover common abbreviations/full spellings; "money
    # market" and the standalone abbreviation "sav" (e.g. "Platinum Money
    # Market Sav") are savings-type products too, seen on real legacy
    # statements. \b on "sav" avoids false-matching an unrelated word
    # that merely contains "sav" as a substring.
    if "svgs" in lower or "savings" in lower or "money market" in lower or _SAVINGS_WORD_RE.search(lower):
        return "Savings"
    # Defensive: a "product name" that's actually boilerplate/noise
    # (see _NOISE_RE) - e.g. if a future statement's layout lets
    # _in_block_product_name/_summary_product_names latch onto a running
    # header instead of a real product name - is worth surfacing as
    # unclassified ("") rather than as a bogus, confusing account type
    # like "Combined Statement" or "Statement Period".
    if _NOISE_RE.match(product_name.strip()) or not product_name.strip():
        return ""
    return product_name.strip()


def _statement_period(combined_text: str) -> tuple[str, int, int, int]:
    """Returns (statement_date, end_year, start_year, end_month) -
    statement_date is the period's closing date (YYYY-MM-DD); the rest
    are used to resolve transaction dates that only print MM-DD,
    including the rare case of a statement period straddling a year
    boundary (see _expand_year)."""
    m = _DATE_RANGE_RE.search(combined_text)
    if not m:
        return "", 0, 0, 0
    start_mm, start_dd, start_yy, end_mm, end_dd, end_yy = m.groups()
    end_year = 2000 + int(end_yy)
    start_year = 2000 + int(start_yy)
    statement_date = datetime(end_year, int(end_mm), int(end_dd)).strftime("%Y-%m-%d")
    return statement_date, end_year, start_year, int(end_mm)


def _summary_product_names(combined_text: str) -> list[str]:
    """Returns the product names listed (in order) in the front-page
    account summary table, e.g. ["Adv Tiered Interest Chkg", "Regular
    Savings"]. Matched positionally against the per-account detail
    sections later in parse() (assumed to appear in the same order)
    rather than by account number: the summary table's account-number
    column is sometimes redacted differently than the detail sections'
    (e.g. fully zeroed there but showing a real last-4 elsewhere in the
    same, otherwise-consistently-redacted statement), so it can't be
    trusted as a join key."""
    names = []
    in_summary = False
    for line in combined_text.split("\n"):
        s = line.strip()
        if s.lower().startswith("bank deposit accounts"):
            in_summary = True
            continue
        if not in_summary:
            continue
        if s.lower().startswith("total deposit account balance"):
            break
        m = _SUMMARY_ROW_RE.match(s)
        if m:
            names.append(m.group(1).strip())
    return names


def _in_block_product_name(lines: list[str]) -> str:
    """Returns the product name from this account's own "<name>
    Additions"/"<name> Subtractions" grouping header, found within lines
    (that account's own block - see parse()). Preferred over
    _summary_product_names since it's self-contained: no risk of a
    positional mismatch against a separate table elsewhere in the
    document. "" if neither header is present in this block (e.g. an
    account with no activity in either section this statement)."""
    for line in lines:
        m = _SECTION_GROUP_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    return ""


# Precedes the product name in each account's own front-matter block,
# e.g. "Deposit Accounts\nPlatinum Money Market Sav\nPlatinum Privileges
# Relationship Account\n...\nYour Account at a Glance\nAccount Number
# XXXX XXXX 8888" - this sits *before* that account's "Account Number"
# line (see parse()'s block slicing), so it's outside block_lines and
# needs its own lookback (see _preceding_product_name).
_DEPOSIT_ACCOUNTS_RE = re.compile(r"^Deposit Accounts\s*$", re.IGNORECASE)
# How many lines before an "Account Number" line to look for a preceding
# "Deposit Accounts" header - generous enough to cover the few
# boilerplate lines (Privileges tier, redacted name, "Your Account at a
# Glance") that sit in between on every real statement seen so far.
_DEPOSIT_ACCOUNTS_LOOKBACK = 8


def _preceding_product_name(all_lines: list[str], start_idx: int) -> str:
    """Returns the product name from the "Deposit Accounts\n<Product
    Name>" pair immediately preceding this account's own "Account
    Number ..." line (all_lines[start_idx]), within a short lookback
    window. Used as a fallback when the account has neither a "<Product>
    Additions"/"<Product> Subtractions" section to find a name in (e.g.
    zero activity this period - see _in_block_product_name) nor a
    front-page summary table (e.g. a single-account statement, which
    doesn't print one - see _summary_product_names). "" if no "Deposit
    Accounts" line is found in the lookback window."""
    window_start = max(0, start_idx - _DEPOSIT_ACCOUNTS_LOOKBACK)
    for i in range(window_start, start_idx):
        if _DEPOSIT_ACCOUNTS_RE.match(all_lines[i].strip()) and i + 1 < start_idx:
            return all_lines[i + 1].strip()
    return ""


def _expand_year(mm: str, dd: str, end_year: int, start_year: int, end_month: int) -> str:
    """MM-DD -> YYYY-MM-DD, using end_year unless the period spans a year
    boundary (start_year != end_year, e.g. a Dec 20 - Jan 19 statement)
    and this date's month is later than the period's end month, in which
    case it must belong to start_year instead."""
    year = end_year
    if start_year != end_year and int(mm) > end_month:
        year = start_year
    return f"{year}-{mm}-{dd}"


def _parse_account_block(
    lines: list[str], account_number: str, account_type: str, end_year: int, start_year: int, end_month: int
) -> tuple[list[dict], float | None]:
    """Parses one account's transactions out of lines (the slice of the
    document between its "Account Number" line and its "Daily Balance
    Summary" line). Returns (transactions, beginning_balance)."""
    beginning_balance = None
    section_type = None  # "credit", "debit", "check", or None
    pending = None
    transactions: list[dict] = []
    checks: list[dict] = []

    def flush():
        nonlocal pending
        if pending:
            transactions.append(pending)
        pending = None

    for line in lines:
        s = line.strip()
        if not s:
            continue

        if beginning_balance is None:
            m = _BEGINNING_BALANCE_RE.search(s)
            if m:
                beginning_balance = common.parse_amount(m.group(1))

        section_key = s.lower()
        if section_key in _SECTION_STARTS:
            flush()
            section_type = _SECTION_STARTS[section_key]
            continue
        if _TOTAL_LINE_RE.match(s):
            flush()
            section_type = None
            continue

        if section_type is None:
            continue
        if _NOISE_RE.match(s):
            continue

        if section_type == "check":
            m = _CHECK_RE.match(s)
            if m:
                num, mm, dd, raw_amount = m.groups()
                checks.append(
                    {
                        "date": _expand_year(mm, dd, end_year, start_year, end_month),
                        "description": f"CHECK #{num.rstrip('*')}",
                        "amount": -common.parse_amount(raw_amount),  # checks are always withdrawals
                        "balance": None,
                        "account": account_number,
                        "accountType": account_type,
                    }
                )
            continue

        m = _TXN_RE.match(s)
        if m:
            flush()
            description, mm, dd, raw_amount = m.groups()
            amount = common.parse_amount(raw_amount)
            pending = {
                "date": _expand_year(mm, dd, end_year, start_year, end_month),
                "description": description.strip(),
                "amount": amount if section_type == "credit" else -amount,
                "balance": None,
                "account": account_number,
                "accountType": account_type,
            }
        elif pending:
            pending["description"] += " " + s

    flush()
    all_txns = transactions + checks
    all_txns.sort(key=lambda t: (t["date"], t["description"]))
    common.apply_running_balance(all_txns, beginning_balance)
    return all_txns, beginning_balance


def parse(pages_text: list[str], pdf_path: str, vision: dict | None = None) -> dict:  # noqa: ARG001 - pdf_path/vision unused, see bank_statement.py contract
    combined = "\n".join(pages_text)
    statement_date, end_year, start_year, end_month = _statement_period(combined)
    product_names = _summary_product_names(combined)

    all_lines = []
    for page in pages_text:
        all_lines.extend(page.split("\n"))

    # Each account's data runs from its "Account Number ..." line up to
    # (and including, for section-end detection purposes) its own
    # "Daily Balance Summary" line - these alternate 1:1 in document
    # order, one pair per account.
    starts = [i for i, line in enumerate(all_lines) if _ACCOUNT_NUMBER_RE.match(line.strip())]
    ends = [i for i, line in enumerate(all_lines) if _DAILY_BALANCE_SUMMARY_RE.match(line.strip())]

    accounts = []
    for i, start_idx in enumerate(starts):
        end_idx = next((e for e in ends if e > start_idx), len(all_lines))
        m = _ACCOUNT_NUMBER_RE.match(all_lines[start_idx].strip())
        account_number = common.last4_digits(m.group(1))
        block_lines = all_lines[start_idx + 1 : end_idx]
        product_name = _in_block_product_name(block_lines)
        if not product_name:
            product_name = _preceding_product_name(all_lines, start_idx)
        if not product_name and i < len(product_names):
            product_name = product_names[i]
        account_type = _classify_account_type(product_name)
        txns, beginning_balance = _parse_account_block(
            block_lines, account_number, account_type, end_year, start_year, end_month
        )
        accounts.append(
            {
                "account": account_number,
                "accountType": account_type,
                "transactions": txns,
            }
        )

    all_transactions = [t for acct in accounts for t in acct["transactions"]]

    return {
        "institution": "Bank of America",
        "statementDate": statement_date,
        "transactions": all_transactions,
    }
