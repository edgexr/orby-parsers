"""Sample Brokerage Services - a synthetic, portable brokerage-CSV
parser used purely for regression coverage (see
test/gen-brokerage-csv-synthetic-sample.py, which generates the fixture
this matches). It's also the reference example for authoring a real
KIND_BROKERAGE csv_institutions/ parser - the CSV sibling of
institutions/sample_brokerage.py (see that module and
csv_statement.py's module docstring for the full KIND_BROKERAGE
contract).

Real export header: "Trade Date,Action,Symbol,Description,Quantity,
Price,Amount". Unlike a PDF statement, nothing in the file itself names
the institution (see csv_statement.py's "Detection works differently"
note), so institution/accountType below are simply hardcoded - a real
brokerage-specific parser would do the same for whichever institution
its own export shape identifies.
"""

from institutions import common

KIND = "brokerage"

_REQUIRED_HEADER = {"Trade Date", "Action", "Symbol", "Description", "Quantity", "Price", "Amount"}


def detect(header: list[str], sample_rows: list[list[str]]) -> tuple[bool, str]:
    cols = set(header)
    missing = _REQUIRED_HEADER - cols
    if missing:
        return False, f"missing column(s) {sorted(missing)}"
    return True, "Sample Brokerage Services activity export header matched"


def _to_iso_date(raw: str) -> str:
    month, day, year = raw.strip().split("/")
    return f"{year}-{int(month):02d}-{int(day):02d}"


def parse(rows: list[dict[str, str]], csv_path: str) -> dict:
    transactions = []
    for row in rows:
        quantity_raw = (row.get("Quantity") or "").strip()
        price_raw = (row.get("Price") or "").strip()
        transactions.append(
            {
                "date": _to_iso_date(row["Trade Date"]),
                "description": row["Description"].strip(),
                "amount": common.parse_amount(row["Amount"]),
                "action": (row.get("Action") or "").strip(),
                "symbol": (row.get("Symbol") or "").strip(),
                "quantity": common.parse_amount(quantity_raw) if quantity_raw else None,
                "price": common.parse_amount(price_raw) if price_raw else None,
            }
        )
    common.tag_account(transactions, "", "Brokerage")
    return {
        "institution": "Sample Brokerage Services",
        "statementDate": "",
        "tables": {"brokerage_transactions": transactions},
    }
