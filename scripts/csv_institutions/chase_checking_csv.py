"""Chase checking/savings "Download account activity" CSV export.

Real export header (as of 2026): "Details,Posting Date,Description,
Amount,Type,Balance,Check or Slip #". This is distinctive enough (no
other bundled/common export uses "Posting Date" + "Check or Slip #"
together) that detect() can match on header shape alone - Chase's own
name is never printed anywhere in the file itself, unlike a PDF
statement.

Amount is already signed the way this codebase's shared schema wants
(withdrawals/debits negative, deposits/credits positive) - no sign
flip needed, unlike a credit-card export (see
institutions/common.negate_amounts_and_balances for that case).
"""

from institutions import common

_REQUIRED_HEADER = {"Posting Date", "Description", "Amount", "Balance", "Check or Slip #"}


def detect(header: list[str], sample_rows: list[list[str]]) -> tuple[bool, str]:
    cols = set(header)
    missing = _REQUIRED_HEADER - cols
    if missing:
        return False, f"missing column(s) {sorted(missing)}"
    return True, "Chase checking/savings export header matched"


def parse(rows: list[dict[str, str]], csv_path: str) -> dict:
    transactions = []
    for row in rows:
        date = _to_iso_date(row["Posting Date"])
        balance_raw = (row.get("Balance") or "").strip()
        transactions.append(
            {
                "date": date,
                "description": row["Description"].strip(),
                "amount": common.parse_amount(row["Amount"]),
                "balance": common.parse_amount(balance_raw) if balance_raw else None,
                "reference": (row.get("Check or Slip #") or "").strip(),
            }
        )
    common.tag_account(transactions, "", "Checking")
    return {
        "institution": "Chase",
        "statementDate": "",
        "transactions": transactions,
    }


def _to_iso_date(raw: str) -> str:
    """Chase prints Posting Date as MM/DD/YYYY."""
    month, day, year = raw.strip().split("/")
    return f"{year}-{int(month):02d}-{int(day):02d}"
