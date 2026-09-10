"""Vanguard Brokerage statement parser (KIND_BROKERAGE).

Covers the two layouts Vanguard has shipped for a Vanguard Brokerage
Account statement:

  * the older "Vanguard Voyager Select Services" quarter-to-date
    statement (the "Mutual funds" holdings table carries an "Average
    price per share" and "Total cost" column pair), and
  * the current "Vanguard Personal Investor" monthly transaction
    statement (the holdings table drops that pair, and the settlement
    fund is relabelled "Sweep program").

Both have the same shape otherwise:

  * a "Balances and holdings for Vanguard Brokerage Account—<n>" block
    with a "Settlement fund"/"Sweep program" section (the federal money
    market fund) and a "Mutual funds" section, and
  * an "Account activity for Vanguard Brokerage Account—<n>" block whose
    "Completed transactions" table is a flat list of
    dividend/reinvestment/transfer/sweep rows.

Everything structured goes into two of the three shared brokerage
tables:

  brokerage_holdings     <- Settlement fund / Sweep program + Mutual funds
  brokerage_transactions <- Completed transactions

There is no cash_transactions output: Vanguard's "Completed
transactions" table has no running balance and is not shaped like a
plain bank ledger, and the settlement fund's own activity (the "Sweep
in"/"Sweep out" rows) is already in that same table.

Every regex below is written against the text
`go run . extract-statement <pdf> --dump` prints, not the PDF's visual
layout - pdfplumber linearises each table row onto one line with the
security name wrapping onto the following line(s).

Regression coverage: TestExtractStatementVanguardBrokerage in
pkg/ingest, against the two synthetic fixtures
test/gen-vanguard-brokerage-synthetic-sample.py builds (one per layout,
wholly invented data so they can be committed - test/*.pdf is
gitignored). Originally developed against two redacted real statements.
"""

import re
from datetime import date, datetime

import parser_common

from . import common

KIND = parser_common.KIND_BROKERAGE

_INSTITUTION = "Vanguard"
_CURRENCY = "USD"

# The Vanguard Federal Money Market Fund is printed with no ticker in
# the settlement/sweep section; this is its real symbol.
_MMF_NAME_RE = re.compile(r"VANGUARD FEDERAL MONEY MARKET FUND", re.I)
_MMF_SYMBOL = "VMFXX"

# A printed money/quantity/price cell: always has a decimal point, may
# carry a '$', a sign, thousands separators.
_NUM = r"-?\$?-?[\d,]+\.\d+"
_NUM_RE = re.compile(r"^" + _NUM + r"$")
# A holdings/activity cell that may instead be Vanguard's "no value"
# dash.
_CELL_RE = re.compile(r"^(?:" + _NUM + r"|[-–—])$")

_DASHES = {"-", "–", "—"}

# Statement date: the "<Month> D, YYYY, ... statement" header line every
# page reprints.
_STMT_DATE_RE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4}),\s")

# Account number: "Individual brokerage account—00004152",
# "Trust brokerage account—XXXX6763", "Vanguard Brokerage Account—XXXX6763".
_ACCOUNT_RE = re.compile(r"brokerage account[\s—–-]+([X\d]{3,})", re.I)

# See common.ACCOUNT_TYPE_PATTERNS - this table used to be duplicated here
# and in fidelity_brokerage.py, and had drifted out of step with it.
# The account-title line names the registration/wrapper, e.g.
# "Individual brokerage account—00004152" / "Trust brokerage account—XXXX6763".
_ACCOUNT_TITLE_RE = re.compile(r"^\S.*brokerage account[\s—–-]", re.I)

# Holdings section headers.
_HOLDING_SECURITY_SECTIONS = {
    "mutual funds", "stocks", "bonds", "exchange-traded funds", "etfs",
    "options", "other holdings", "other",
}
_HOLDING_CASH_SECTIONS = {"settlement fund", "sweep program"}

# Lines inside a holdings section that are column headers / notes, not
# data rows or name continuations.
_HOLDING_SKIP_RE = re.compile(
    r"^(?:Symbol\s+Name|Name\s+Quantity|Average price|Price on|Balance on|"
    r"Est\.\s|7-day |Total\b|Your securities|This section)"
)
_EST_INCOME_RE = re.compile(
    r"Est\.\s*annual income:\s*\$?(?P<income>[\d,]+\.\d+);\s*"
    r"Est\.\s*yield:\s*(?P<yield>[\d.]+)%"
)

# Activity ("Completed transactions") transaction types, longest first.
_TXN_TYPES = (
    "Sweep in", "Sweep out", "Capital gain", "Corporate action",
    "Buy", "Sell", "Dividend", "Reinvestment", "Transfer", "Conversion",
    "Rollover", "Contribution", "Distribution", "Withdrawal", "Exchange",
    "Redemption", "Interest", "Reclassification",
)
_TXN_ROW_RE = re.compile(
    r"^(?P<sdate>\d{2}/\d{2})\s+(?P<tdate>\d{2}/\d{2})\s+"
    r"(?P<symbol>\S+)\s+"
    r"(?P<name>.+?)\s+"
    r"(?P<ttype>" + "|".join(_TXN_TYPES) + r")\s+"
    r"(?P<acct>Cash|[-–—])\s+"
    r"(?P<qty>" + _NUM + r"|[-–—])\s+"
    r"(?P<price>" + _NUM + r"|[-–—])\s+"
    r"(?P<fees>" + _NUM + r"|[-–—])\s+"
    r"(?P<amount>" + _NUM + r")$"
)
_TXN_START_RE = re.compile(r"^\d{2}/\d{2}\s+\d{2}/\d{2}\s")
_TICKER_RE = re.compile(r"^[A-Z]{2,6}$")

_TRANSFER_TYPES = {"Transfer", "Sweep in", "Sweep out", "Exchange", "Conversion", "Rollover"}

# Page header/footer boilerplate pdfplumber interleaves with the tables -
# mail-sort codes, the per-page statement-title line, phone numbers, the
# "Page N of M" footer. Dropped before the section walker runs so it is
# never mistaken for a name-continuation line.
_BOILERPLATE_RE = re.compile(
    r"^\d{5,}$"
    r"|^[0-9A-Z]{18,}$"
    r"|^C$"
    r"|^[Xx ]+$"
    r"|Page \d+ of\s?\d"
    r"|Voyager Select Services"
    r"|Vanguard Personal Investor"
    r"|vanguard\.com"
    r"|^\d{3}-\d{3}-\d{4}$"
    r"|quarter-to-date statement"
    r"|monthly transaction statement"
    r"|^CDDLRREG$"
    r"|^(?:Individual|Joint|Trust|Traditional|Roth|Rollover|SEP|SIMPLE|Inherited)\b.*brokerage account"
)


def _is_boilerplate(line: str) -> bool:
    return bool(_BOILERPLATE_RE.search(line))


def detect(head_text: str) -> tuple[bool, str]:
    low = head_text.lower()
    if "vanguard" not in low:
        return False, "'vanguard' not found"
    if "vanguard brokerage" not in low:
        return False, "'Vanguard Brokerage' not found"
    if "brokerage account" not in low:
        return False, "'brokerage account' not found"
    return True, "found 'Vanguard Brokerage' account statement"


def _cell(token: str | None) -> float | None:
    if token is None:
        return None
    token = token.strip()
    if token in _DASHES or not token:
        return None
    return common.parse_amount(token)


def _statement_date(pages_text: list[str]) -> tuple[str, date | None]:
    for page in pages_text:
        for line in page.splitlines():
            m = _STMT_DATE_RE.match(line.strip())
            if m:
                try:
                    d = datetime.strptime(m.group(1), "%B %d, %Y").date()
                    return d.isoformat(), d
                except ValueError:
                    return "", None
    return "", None


def _account(text: str) -> str:
    m = _ACCOUNT_RE.search(text)
    return common.last4_digits(m.group(1)) if m else ""


def _account_type(text: str) -> str:
    for line in text.splitlines():
        if _ACCOUNT_TITLE_RE.match(line.strip()):
            return common.classify_account_type(line, "Brokerage")
    return "Brokerage"


def _resolve_date(month_day: str, anchor: date | None) -> str:
    month, day = (int(p) for p in month_day.split("/"))
    if anchor is None:
        raise ValueError(f"cannot resolve year for activity date {month_day}: no statement date")
    year = anchor.year
    # A settlement-dated row can carry an item from the tail of the
    # previous month; if the statement is for January and the row is in
    # December, it belongs to the prior year.
    if month == 12 and anchor.month == 1:
        year -= 1
    return date(year, month, day).isoformat()


# --- holdings ---

def _finish_holding_name(row: dict) -> None:
    name = " ".join(row.pop("_name").split())
    row["description"] = name
    if not row.get("symbol"):
        if _MMF_NAME_RE.search(name):
            row["symbol"] = _MMF_SYMBOL
        else:
            # Fall back to the first word so the required, non-empty
            # symbol constraint is met; real security rows always carry a
            # printed ticker.
            row["symbol"] = name.split()[0] if name.split() else "CASH"


def _parse_holding_row(tokens: list[str], section: str) -> dict | None:
    """Builds a brokerage_holdings row from one linearised data line's
    tokens, or None if the line is not a holding row.

    Trailing numeric/dash cells decide the layout: six means the older
    statement's "Average price per share / Total cost / Quantity / Price
    / Balance begin / Balance end", four means "Quantity / Price /
    Balance begin / Balance end".
    """
    trailing = 0
    for tok in reversed(tokens):
        if _CELL_RE.match(tok):
            trailing += 1
        else:
            break
    if trailing not in (4, 6):
        return None
    cells = tokens[-trailing:]
    head = tokens[:-trailing]
    if not head:
        return None

    symbol = ""
    if section in _HOLDING_SECURITY_SECTIONS and _TICKER_RE.match(head[0]):
        symbol = head[0]
        head = head[1:]
    if not head:
        return None

    row: dict = {
        "symbol": symbol,
        "_name": " ".join(head),
        "type": "Sweep" if section in _HOLDING_CASH_SECTIONS else "Mutual Fund",
        "currency_code": _CURRENCY,
    }
    if section in _HOLDING_CASH_SECTIONS:
        row["is_cash_equivalent"] = True

    if trailing == 6:
        avg_price, total_cost, quantity, price, _begin, end = cells
        row["average_cost_basis"] = _cell(avg_price)
        row["cost_basis_total"] = _cell(total_cost)
    else:
        quantity, price, _begin, end = cells
    row["quantity"] = _cell(quantity)
    row["price"] = _cell(price)
    row["current_value"] = _cell(end)
    return row


# --- activity ---

_ACTIONS = {
    "Sweep in": "Sweep In",
    "Sweep out": "Sweep Out",
    "Capital gain": "Capital Gain",
    "Corporate action": "Corporate Action",
}


def _parse_txn(block_lines: list[str], anchor: date | None) -> dict | None:
    first = block_lines[0]
    m = _TXN_ROW_RE.match(first)
    if not m:
        return None

    name_parts = [m.group("name")]
    from_note = ""
    for line in block_lines[1:]:
        s = line.strip()
        if s.startswith("FROM:"):
            from_note = s[len("FROM:"):].strip()
        elif s:
            name_parts.append(s)
    name = " ".join(" ".join(name_parts).split())

    ttype = m.group("ttype")
    action = _ACTIONS.get(ttype, ttype)
    symbol = m.group("symbol")
    if symbol in _DASHES:
        symbol = ""
    if not symbol and _MMF_NAME_RE.search(name):
        symbol = _MMF_SYMBOL

    description = f"{action} {name}".strip()
    if from_note:
        description += f" (from {from_note})"

    row: dict = {
        "date": _resolve_date(m.group("sdate"), anchor),
        "description": description,
        "amount": common.parse_amount(m.group("amount")),
        "action": action,
    }
    if symbol:
        row["symbol"] = symbol
    qty = _cell(m.group("qty"))
    if qty is not None:
        row["quantity"] = qty
    price = _cell(m.group("price"))
    if price is not None:
        row["price"] = price
    fees = _cell(m.group("fees"))
    if fees is not None:
        row["commission_and_fees"] = fees
    if ttype in _TRANSFER_TYPES:
        row["transaction_type"] = "internal_transfer"
    return row


def parse(pages_text: list[str], pdf_path: str) -> dict:
    text = "\n".join(pages_text)
    statement_date, anchor = _statement_date(pages_text)
    account = _account(text)
    account_type = _account_type(text)

    lines = [line.strip() for line in text.splitlines()]

    holdings: list[dict] = []
    transactions: list[dict] = []

    mode = None            # None | "holdings" | "activity"
    section = None         # current holdings section key
    in_completed = False
    current_holding: dict | None = None
    txn_block: list[str] = []

    def flush_txn_block() -> None:
        nonlocal txn_block
        if txn_block:
            row = _parse_txn(txn_block, anchor)
            if row:
                transactions.append(row)
            txn_block = []

    for line in lines:
        if line and _is_boilerplate(line):
            continue
        low = line.lower()

        if low.startswith("balances and holdings for"):
            mode, section, current_holding = "holdings", None, None
            continue
        if low.startswith("account activity for"):
            flush_txn_block()
            mode, in_completed, current_holding = "activity", False, None
            continue

        if mode == "holdings":
            base = re.sub(r"\s*\(continued\)\s*$", "", low).strip()
            if base in _HOLDING_SECURITY_SECTIONS or base in _HOLDING_CASH_SECTIONS:
                section = base
                current_holding = None
                continue
            if section is None or not line:
                continue
            if _HOLDING_SKIP_RE.match(line):
                m_est = _EST_INCOME_RE.search(line)
                if m_est and not line.lower().startswith("total") and current_holding is not None:
                    current_holding["estimated_annual_income"] = common.parse_amount(m_est.group("income"))
                    current_holding["estimated_yield"] = float(m_est.group("yield"))
                continue
            tokens = line.split()
            if tokens and all(_CELL_RE.match(t) for t in tokens):
                continue  # a section/sub-total's bare figures line
            row = _parse_holding_row(tokens, section)
            if row is not None:
                holdings.append(row)
                current_holding = row
                continue
            # Otherwise a name-continuation line.
            if current_holding is not None:
                current_holding["_name"] += " " + line
            continue

        if mode == "activity":
            if low == "completed transactions":
                in_completed = True
                continue
            if not in_completed:
                continue
            if low.startswith("if you had an adjustment") or low.startswith("income summary"):
                flush_txn_block()
                in_completed = False
                continue
            if not line:
                continue
            if _TXN_START_RE.match(line):
                flush_txn_block()
                txn_block = [line]
            elif txn_block:
                txn_block.append(line)
            continue

    flush_txn_block()

    for row in holdings:
        _finish_holding_name(row)

    common.tag_account(holdings, account, account_type)
    common.tag_account(transactions, account, account_type)

    return {
        "institution": _INSTITUTION,
        "statementDate": statement_date,
        "tables": {
            "brokerage_holdings": holdings,
            "brokerage_transactions": transactions,
        },
    }
