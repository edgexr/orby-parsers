"""Chase credit card statement parser.

Format: monthly card statements (United MileagePlus, Freedom, Sapphire,
...) with an "ACCOUNT ACTIVITY" table whose columns are:

    Date of Transaction | Merchant Name or Transaction Description | $ Amount

Dates are MM/DD with no year; the year comes from the statement's
"Opening/Closing Date" range, which also resolves statements spanning a
year boundary.

Unlike the Wells Fargo checking format, direction is printed explicitly:
payments and credits carry a leading minus, purchases don't - so amounts
are taken at face value (rather than being inferred from section
headings) and checked against the statement's own ACCOUNT SUMMARY using
the accounting identity

    Previous Balance + sum(transactions) == New Balance

so a dropped, duplicated or mis-signed row fails the parse instead of
being reported as fact. Only after that validation are amounts (and the
derived running balance) negated - see common.negate_amounts_and_balances
- to this codebase's schema convention: signed by how the transaction
affects the money you actually have, the same way checking/savings
already are, not by how the card issuer's own balance moves (which is
what the statement prints, and what the identity above is checked
against).

Two quirks of Chase's PDFs are worth knowing:

  * Bold headings extract with every character doubled -
    "AACCCCOOUUNNTT SSUUMMMMAARRYY" is "ACCOUNT SUMMARY". Nothing here
    matches on those: the summary field labels ("Previous Balance",
    "Opening/Closing Date"), the activity table's column header and the
    section labels all extract normally, so the doubled headings are
    simply ignored.
  * Statements downloaded from chase.com are encrypted with an owner
    password but an empty user password, so pdfplumber opens them
    without any credential. No decryption step is needed.
"""

import re
from datetime import datetime

from . import common

_EPS = 0.005

# "Account Number: XXXX XXXX XXXX 0451". Masked digits are kept as-is and
# the caller takes the trailing 4.
_ACCOUNT_RE = re.compile(r"Account Number:\s*([\dXx*\s]{4,25})", re.IGNORECASE)

# "Opening/Closing Date 06/22/26 - 07/21/26" - the statement period. The
# closing date is the statement date; both ends are needed to give MM/DD
# transaction dates a year.
_PERIOD_RE = re.compile(
    r"Opening/Closing Date\s+(\d{2})/(\d{2})/(\d{2})\s*-\s*(\d{2})/(\d{2})/(\d{2})"
)

# ACCOUNT SUMMARY figures used to validate the parse.
_PREVIOUS_BALANCE_RE = re.compile(r"^Previous Balance\s+\$?(-?[\d,]+\.\d{2})", re.MULTILINE)
_NEW_BALANCE_RE = re.compile(r"^New Balance\s+\$?(-?[\d,]+\.\d{2})", re.MULTILINE)

# The activity table's column header, reprinted on each page the table
# spans. Not doubled, unlike the "ACCOUNT ACTIVITY" heading above it.
_TABLE_HEADER_RE = re.compile(
    r"^Transaction\s+Merchant Name or Transaction Description\s+\$?\s*Amount", re.IGNORECASE
)

# A transaction row: "MM/DD <description> <amount>", amount signed as
# printed. Rows without a trailing amount are continuation lines (e.g.
# the "07/14 POUND STERLING" line of a foreign-currency purchase), which
# is why the amount is required rather than optional.
#
# The digits before the decimal point are optional because Chase prints
# sub-dollar amounts with no leading zero (".99", not "0.99"). Requiring
# them silently dropped those rows into the continuation branch below,
# which _validate then caught as a reconciliation failure.
_TXN_RE = re.compile(r"^(\d{2})/(\d{2})\s+(.*?)\s+(-?[\d,]*\.\d{2})\s*$")

# Ends the activity table: the year-to-date totals block, the interest
# charges block, or a page footer.
_END_TABLE_RE = re.compile(
    r"^(?:\d{4} Totals Year-to-Date|INTEREST CHARGES)\b|Page\s*\d+\s+of\s+\d+", re.IGNORECASE
)


def detect(head_text: str) -> tuple[bool, str]:
    if "chase.com" not in head_text.lower():
        return False, "'chase.com' not found"
    if not _PERIOD_RE.search(head_text):
        return False, "'chase.com' found, but no 'Opening/Closing Date MM/DD/YY - MM/DD/YY' summary line"
    return True, "matched Chase credit card account summary"


def _statement_period(combined_text: str):
    """Returns (opening_date, closing_date) as datetimes, or (None, None)
    if the Opening/Closing Date line isn't found or doesn't parse.
    """
    m = _PERIOD_RE.search(combined_text)
    if not m:
        return None, None
    om, od, oy, cm, cd, cy = (int(g) for g in m.groups())
    try:
        return datetime(2000 + oy, om, od), datetime(2000 + cy, cm, cd)
    except ValueError:
        return None, None


def _txn_date(month: int, day: int, opening: datetime, closing: datetime) -> str:
    """Gives an MM/DD transaction date a year from the statement period.
    Only matters when the period straddles a year boundary (a January
    statement listing December activity), where the month decides which
    side of the boundary the transaction falls on.
    """
    year = opening.year
    if opening.year != closing.year:
        year = opening.year if month >= opening.month else closing.year
    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_transactions(pages_text: list[str], opening: datetime, closing: datetime) -> list[dict]:
    transactions: list[dict] = []
    in_table = False

    for text in pages_text:
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue

            if _TABLE_HEADER_RE.match(s):
                in_table = True
                continue
            if not in_table:
                continue
            if _END_TABLE_RE.search(s):
                in_table = False
                continue

            m = _TXN_RE.match(s)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                transactions.append(
                    {
                        "date": _txn_date(month, day, opening, closing),
                        "description": m.group(3).strip(),
                        # Signed as printed (payments/credits negative,
                        # purchases positive) so _validate can check it
                        # against the statement's own reconciliation
                        # identity; negated to this codebase's schema
                        # convention as a final step in parse().
                        "amount": common.parse_amount(m.group(4)),
                        "balance": None,
                        "reference": "",
                    }
                )
            elif transactions and not re.match(r"^(?:PAYMENTS|PURCHASE|CASH ADVANCE|FEES|Date of)", s, re.IGNORECASE):
                # A wrapped detail line for the row above, e.g. an
                # itinerary ("090226 1 R SFO SIN") or a currency
                # conversion ("10.00 X 1.342000000 (EXCHG RATE)").
                transactions[-1]["description"] += " " + s

    for txn in transactions:
        txn["description"] = re.sub(r"\s+", " ", txn["description"]).strip()
    return transactions


def _validate(combined_text: str, transactions: list[dict]) -> float | None:
    """Checks the parse against the statement's own ACCOUNT SUMMARY and
    returns the previous balance to seed the running balance with.
    Raises if the accounting identity doesn't hold, so a dropped or
    mis-signed row can't be reported as fact. Returns None (skipping the
    check) if the summary lines aren't present.
    """
    prev_m = _PREVIOUS_BALANCE_RE.search(combined_text)
    new_m = _NEW_BALANCE_RE.search(combined_text)
    if not prev_m or not new_m:
        return None

    previous = common.parse_amount(prev_m.group(1))
    new_balance = common.parse_amount(new_m.group(1))
    total = round(sum(t["amount"] for t in transactions), 2)
    if abs(previous + total - new_balance) > _EPS:
        raise ValueError(
            f"transactions total {total} does not reconcile previous balance "
            f"{previous} with new balance {new_balance} "
            f"(off by {round(previous + total - new_balance, 2)})"
        )
    return previous


def parse(pages_text: list[str], pdf_path: str, vision: dict | None = None) -> dict:  # noqa: ARG001 - pdf_path/vision unused, see bank_statement.py contract
    combined = "\n".join(pages_text)

    opening, closing = _statement_period(combined)
    m = _ACCOUNT_RE.search(combined)
    account = common.last4_digits(m.group(1)) if m else ""

    transactions = _parse_transactions(pages_text, opening, closing) if opening and closing else []
    common.apply_running_balance(transactions, _validate(combined, transactions))
    common.negate_amounts_and_balances(transactions)
    common.tag_account(transactions, account, "Credit Card")

    return {
        "institution": "Chase",
        "statementDate": closing.strftime("%Y-%m-%d") if closing else "",
        "transactions": transactions,
    }
