"""Vanguard brokerage "Custom Activity Report" - the account activity
export downloadable from Vanguard as either a CSV or an .xlsx workbook.
csv_statement.py reads both formats into the same row grid, so this one
module handles either (see that dispatcher's module docstring).

Shape (one table, KIND_BROKERAGE -> brokerage_transactions):

    Custom report created on: <date>.                     <- preamble (skipped)
    This report only includes transactions ...            <- preamble (skipped)
    Settlement date,Trade date,Symbol,Name,Type,Account type,Quantity,Price,Commission & fees**,Amount
    8/30/2020,6/3/2018,VMRAX,<fund name>,TRANSFER FROM ...,CASH,16.4119,,,
    3/19/2024,7/29/2016,VMRAX,<fund name>,Dividend,CASH,,,,$74.2886
    ...
    DISCLOSURES                                           <- footer (skipped)
    *Note on account protection: ...                      <- footer (skipped)

The dispatcher already strips the preamble and the trailing single-cell
disclosures block (parser_common.grid_header_and_rows), so parse() only
ever sees real activity rows.

Amounts print in Vanguard's own sign (money in positive, money out
negative), which is already Orby's convention - returned as-is, no flip.
A share-only movement with no cash (an internal "Transfer from ...")
has a blank Amount and is emitted with amount 0.0.
"""

import re

from institutions import common

KIND = "brokerage"

# The full header of the Custom Activity Report. Detection requires the
# distinctive combination (Settlement date + Trade date + the
# double-asterisked "Commission & fees**") so a plain 3-column export
# from another broker can't match.
_REQUIRED_COLUMNS = {
    "Settlement date",
    "Trade date",
    "Symbol",
    "Name",
    "Type",
    "Account type",
    "Amount",
}
_SIGNATURE_COLUMN = "Commission & fees**"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")


def detect(header: list[str], sample_rows: list[list[str]]) -> tuple[bool, str]:
    cols = {c.strip() for c in header}
    missing = _REQUIRED_COLUMNS - cols
    if missing:
        return False, f"missing column(s) {sorted(missing)}"
    if _SIGNATURE_COLUMN not in cols:
        return False, f"missing the distinctive {_SIGNATURE_COLUMN!r} column"
    return True, "Vanguard Custom Activity Report header matched"


def _to_iso(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if _ISO_DATE_RE.match(raw):
        return raw
    m = _SLASH_DATE_RE.match(raw)
    if not m:
        raise ValueError(f"unrecognized Vanguard date {raw!r}")
    month, day, year = m.group(1), m.group(2), m.group(3)
    if len(year) == 2:
        year = ("20" if int(year) < 70 else "19") + year
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


# Vanguard writes "Free" (and occasionally "--") in a numeric column
# when there is no charge - a zero, not a missing value.
_ZERO_TOKENS = {"free", "--", "-", "n/a", "none"}


def _num(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.lower() in _ZERO_TOKENS:
        return 0.0
    return common.parse_amount(raw)


def parse(rows: list[dict[str, str]], path: str) -> dict:
    transactions = []
    for row in rows:
        settlement = (row.get("Settlement date") or "").strip()
        trade = (row.get("Trade date") or "").strip()
        date = _to_iso(settlement or trade)
        if not date:
            continue  # a row with no usable date isn't a transaction

        activity = (row.get("Type") or "").strip()
        name = (row.get("Name") or "").strip()
        description = " ".join(p for p in (activity, name) if p) or (row.get("Symbol") or "").strip()

        amount_raw = (row.get("Amount") or "").strip()
        txn = {
            "date": date,
            "description": description or activity or "transaction",
            "amount": common.parse_amount(amount_raw) if amount_raw else 0.0,
            "action": activity,
            "symbol": (row.get("Symbol") or "").strip(),
        }
        qty = _num(row.get("Quantity"))
        if qty is not None:
            txn["quantity"] = qty
        price = _num(row.get("Price"))
        if price is not None:
            txn["price"] = price
        fees = _num(row.get(_SIGNATURE_COLUMN))
        if fees is not None:
            txn["commission_and_fees"] = fees
        transactions.append(txn)

    common.tag_account(transactions, "", "Brokerage")
    return {
        "institution": "Vanguard",
        "statementDate": "",
        "tables": {"brokerage_transactions": transactions},
    }
