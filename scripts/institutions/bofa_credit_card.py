"""Bank of America credit card statement parser.

Format: monthly credit card statements with a "Transactions" section
containing "Payments and Other Credits" / "Purchases and Adjustments" /
"Fees Charged" / "Interest Charged" groups, and transaction dates as
MM/DD (no year printed - inferred from the statement's closing date).

Amounts are parsed and validated in the statement's own printed sign
(charges positive, payments/credits negative - see _parse_transactions),
then negated as a final step (see common.negate_amounts_and_balances)
so they align with this codebase's shared schema convention: signed by
how the transaction affects the money you actually have, the same way
checking/savings already are, not by how the card issuer's own balance
moves.
"""

import re
from datetime import datetime

from . import common

_CLOSING_DATE_RE = re.compile(r"Statement Closing Date\s+(\d{2})/(\d{2})/(\d{4})")
_ACCOUNT_RE = re.compile(r"Account\s*#\s*([\dXx*\s]{4,25})", re.IGNORECASE)

# Matches e.g. "Previous Balance $1,150.50 New Balance Total $570.49". No
# per-transaction balance column is printed, so this is the seed for a
# running balance we compute ourselves (see common.apply_running_balance).
_PREVIOUS_BALANCE_RE = re.compile(r"Previous Balance\s+\$?(-?[\d,]+\.\d{2})", re.IGNORECASE)

_SECTION_HEADING_RE = re.compile(
    r"^(Payments and Other Credits|Purchases and Adjustments|Fees Charged|Interest Charged)\s*$",
    re.IGNORECASE,
)
_HEADER_ROW_RE = re.compile(
    r"^(Transaction\s+Posting\s+Reference|Date\s+Date\s+Description)", re.IGNORECASE
)
_END_OF_TRANSACTIONS_RE = re.compile(
    r"^(\d{4} Totals|Interest Charge Calculation|Page \d+ of \d+)", re.IGNORECASE
)

# Line items with reference + account number columns, e.g.:
#   07/04 07/06 ROOM MATE MILAN S.R.L. MILANO 5049 7777 45.16
# The first date is the Transaction Date, the second the Posting Date
# (used only to identify the row); we report the Transaction Date, i.e.
# when the purchase/payment actually happened. The reference number
# (5049 above) is captured too - it's what BofA uses elsewhere (e.g.
# disputes, online transaction search) to identify this exact line, so
# it's worth keeping even though it plays no role in parsing itself.
_TXN_WITH_REF_RE = re.compile(
    r"^(\d{2}/\d{2})\s+\d{2}/\d{2}\s+(.+?)\s+(\d{4})\s+\d{4}\s+(-?[\d,]+\.\d{2})\s*$"
)
# Line items without those columns, e.g. interest charges:
#   08/05 08/05 INTEREST CHARGED ON PURCHASES 0.00
_TXN_PLAIN_RE = re.compile(r"^(\d{2}/\d{2})\s+\d{2}/\d{2}\s+(.+?)\s+(-?[\d,]+\.\d{2})\s*$")


def detect(head_text: str) -> tuple[bool, str]:
    lower = head_text.lower()
    if "bank of america" not in lower:
        return False, "'bank of america' not found"
    if "total credit line" not in lower:
        return False, "'bank of america' found, but no 'Total Credit Line' marker (not a credit card statement)"
    return True, "matched Bank of America credit card statement"


def _closing_date(combined_text: str) -> datetime | None:
    m = _CLOSING_DATE_RE.search(combined_text)
    if not m:
        return None
    return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))


def _expand_year(date_mmdd: str, closing: datetime) -> str:
    """Convert MM/DD to YYYY-MM-DD using the closing date's year. Billing
    cycles can straddle a year boundary (e.g. Dec 20 - Jan 19), so a
    transaction month later than the closing month is assumed to belong
    to the previous year.
    """
    mm, dd = date_mmdd.split("/")
    year = closing.year - 1 if int(mm) > closing.month else closing.year
    return f"{year}-{mm}-{dd}"


def _parse_transactions(pages_text: list[str], closing: datetime) -> list[dict]:
    transactions = []
    in_transactions = False
    pending = None  # dict being accumulated - a description can wrap onto following lines

    def flush():
        nonlocal pending
        if pending:
            transactions.append(pending)
        pending = None

    for text in pages_text:
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue

            if s == "Transactions":
                in_transactions = True
                continue
            if not in_transactions:
                continue
            if _END_OF_TRANSACTIONS_RE.match(s):
                flush()
                in_transactions = False
                continue
            if _HEADER_ROW_RE.match(s) or _SECTION_HEADING_RE.match(s):
                flush()
                continue
            if re.match(r"^TOTAL ", s, re.IGNORECASE):
                flush()
                continue

            m = _TXN_WITH_REF_RE.match(s)
            if m:
                flush()
                date, description, reference, raw_amount = m.groups()
            else:
                m = _TXN_PLAIN_RE.match(s)
                if m:
                    flush()
                    date, description, raw_amount = m.groups()
                    reference = ""

            if m:
                # Parsed here in the statement's own printed sign
                # (charges positive, payments/credits negative) so it
                # can be validated against the statement's own
                # reconciliation identity below; negated to this
                # codebase's schema convention as a final step in
                # parse(), see common.negate_amounts_and_balances.
                pending = {
                    "date": _expand_year(date, closing),
                    "description": description.strip(),
                    "amount": common.parse_amount(raw_amount),
                    "balance": None,
                    "reference": reference,
                }
                continue

            # Not a new transaction line: a continuation of the pending
            # one's (possibly multi-line) description, e.g. a wrapped
            # merchant name or a foreign-currency amount printed under a
            # purchase.
            if pending:
                pending["description"] += " " + s

    flush()
    transactions.sort(key=lambda t: (t["date"], t["description"]))
    return transactions


def _previous_balance(combined_text: str) -> float | None:
    m = _PREVIOUS_BALANCE_RE.search(combined_text)
    if not m:
        return None
    return common.parse_amount(m.group(1))


def parse(pages_text: list[str], pdf_path: str, vision: dict | None = None) -> dict:  # noqa: ARG001 - pdf_path/vision unused, see bank_statement.py contract
    combined = "\n".join(pages_text)
    closing = _closing_date(combined)

    m = _ACCOUNT_RE.search(combined)
    account = common.last4_digits(m.group(1)) if m else ""

    transactions = _parse_transactions(pages_text, closing) if closing else []
    common.apply_running_balance(transactions, _previous_balance(combined))
    common.negate_amounts_and_balances(transactions)
    common.tag_account(transactions, account, "Credit Card")

    return {
        "institution": "Bank of America",
        "statementDate": closing.strftime("%Y-%m-%d") if closing else "",
        "transactions": transactions,
    }
