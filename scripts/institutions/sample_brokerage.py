"""Sample Brokerage Services - a synthetic, portable brokerage-statement
parser used purely for regression coverage (see
test/gen-brokerage-synthetic-sample.py, which generates the fixture this
matches). It's also the reference example for authoring a real
KIND_BROKERAGE parser: unlike a bank/credit-card parser, it populates
more than one of the three shared tables (holdings, activity, and a
linked cash/settlement section) from a single parse() call, and returns
the "tables" dict shape validate_multi_table_parse_result checks instead
of a flat "transactions" list - see bank_statement.py's module docstring
for the full contract.

Format: a deliberately simple pipe-delimited layout (this fixture's job
is to prove the multi-table contract works end to end, not to stress-
test PDF text-extraction quirks the way the bank parsers' fixtures do):

    SAMPLE BROKERAGE SERVICES
    Statement Period 06/01/2026 - 06/30/2026
    Account 123456789 (Brokerage)

    HOLDINGS
    AAPL | Apple Inc | 10.000 | 210.50 | 2105.00 | 1800.00

    ACTIVITY
    06/03/2026 | Buy | AAPL | Bought 2 shares of AAPL | 2.000 | 205.00 | -410.00

    CASH ACTIVITY
    06/15/2026 | Dividend Received VOO | 12.45 | 512.45
"""

import re

from . import common

KIND = "brokerage"

_HEADER_MARKER = "SAMPLE BROKERAGE SERVICES"
_ACCOUNT_RE = re.compile(r"Account\s+(\d+)\s*\(([^)]+)\)")
_PERIOD_RE = re.compile(r"Statement Period\s+\d{2}/\d{2}/\d{4}\s*-\s*(\d{2})/(\d{2})/(\d{4})")


def detect(head_text: str) -> tuple[bool, str]:
    if _HEADER_MARKER not in head_text:
        return False, f"{_HEADER_MARKER!r} not found"
    return True, f"found {_HEADER_MARKER!r}"


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.split("|")]


def _maybe_amount(cell: str) -> float | None:
    return common.parse_amount(cell) if cell else None


def parse(pages_text: list[str], pdf_path: str) -> dict:
    text = "\n".join(pages_text)
    lines = [line.strip() for line in text.splitlines()]

    account_match = _ACCOUNT_RE.search(text)
    account = account_match.group(1) if account_match else ""
    account_type = account_match.group(2) if account_match else ""

    statement_date = ""
    period_match = _PERIOD_RE.search(text)
    if period_match:
        month, day, year = period_match.groups()
        statement_date = f"{year}-{month}-{day}"

    holdings: list[dict] = []
    brokerage_transactions: list[dict] = []
    cash_transactions: list[dict] = []

    section = None
    for line in lines:
        if line == "HOLDINGS":
            section = "holdings"
            continue
        if line == "ACTIVITY":
            section = "activity"
            continue
        if line == "CASH ACTIVITY":
            section = "cash"
            continue
        if not line or "|" not in line:
            continue

        cells = _split_row(line)
        if section == "holdings":
            symbol, description, quantity, price, current_value, cost_basis_total = cells
            holdings.append({
                "symbol": symbol,
                "description": description,
                "quantity": _maybe_amount(quantity),
                "price": _maybe_amount(price),
                "current_value": _maybe_amount(current_value),
                "cost_basis_total": _maybe_amount(cost_basis_total),
            })
        elif section == "activity":
            date, action, symbol, description, quantity, price, amount = cells
            month, day, year = date.split("/")
            brokerage_transactions.append({
                "date": f"{year}-{month}-{day}",
                "description": description,
                "amount": common.parse_amount(amount),
                "action": action,
                "symbol": symbol,
                "quantity": _maybe_amount(quantity),
                "price": _maybe_amount(price),
            })
        elif section == "cash":
            date, description, amount, balance = cells
            month, day, year = date.split("/")
            cash_transactions.append({
                "date": f"{year}-{month}-{day}",
                "description": description,
                "amount": common.parse_amount(amount),
                "balance": _maybe_amount(balance),
            })

    common.tag_account(holdings, account, account_type)
    common.tag_account(brokerage_transactions, account, account_type)
    common.tag_account(cash_transactions, account, account_type)

    return {
        "institution": _HEADER_MARKER.title(),
        "statementDate": statement_date,
        "tables": {
            "brokerage_holdings": holdings,
            "brokerage_transactions": brokerage_transactions,
            "cash_transactions": cash_transactions,
        },
    }
